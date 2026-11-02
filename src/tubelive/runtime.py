"""One local stream session: source clock, bounded memory, shared cube and work queue."""
from __future__ import annotations

import base64
import copy
import math
import hashlib
import json
import re
import os
import secrets
import shutil
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future
from pathlib import Path

from .ask import ask, answer_with_model
from .ingest import probe
from .media import LocalTranscriber, frame_at
from .schema import SceneSegment, VideoMeta
from .store import StreamMemory
from .diagnostics import DiagnosticLog, system_snapshot
from .testmode import DEFAULTS, validate_config, speech_options, cuda_speech_available, read_results, save_result
from .story import profile_at, validate_profile, evidence_row, review_episode, extractive_anchors, recording_context
from .semantic import LocalSemanticIndex


class BusyError(RuntimeError):
    pass


SOURCE_TIMEOUT_S = 8.0
MAX_PENDING_PER_LANE = 16


def finite_time(value) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Time must be a finite, non-negative number.")
    return number


class ReplaySession:
    def __init__(self, *, memory: StreamMemory | None = None, video: Path | None = None,
                 cache_dir: Path, model=None, transcriber=None, replay: bool = False):
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.cache_dir = cache_dir.resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.diagnostics = DiagnosticLog(self.cache_dir)
        self.model = model
        self.transcriber = transcriber or LocalTranscriber(self.cache_dir)
        self.pipeline = {**DEFAULTS, 'model': getattr(model, 'model', DEFAULTS['model'])}
        self._test_started = None
        self._test_finished = None
        self._test_stats = {'peak_lag_s': 0.0, 'audio_seconds': 0.0, 'vision_seconds': 0.0,
                            'question_seconds': 0.0, 'question_count': 0, 'failed_jobs': 0}
        self.csrf_token = secrets.token_urlsafe(32)
        self.video_path = video.resolve() if video else None
        self.video_info = probe(self.video_path) if self.video_path else {"has_audio": False}
        self.mode = "replay" if replay else ("recorded" if self.video_path else "fixture")
        if not replay and memory and memory.video.id == "synthetic-local-demo":
            self.mode = "synthetic"
        self.memory = self._empty_memory() if replay else (memory or self._empty_memory())
        self.run_id = uuid.uuid4().hex
        self.memory.run_id = self.run_id
        for event in self.memory.story_events:
            event['run_id'] = self.run_id
        self.semantic = LocalSemanticIndex(self.cache_dir)
        self.memory.semantic = self.semantic
        self._conversations = {}
        if not self.memory.profile_versions:
            self._load_profile()
        self.playback_s = 0.0 if replay else self.memory.duration_s
        self.audio_through_s = 0.0 if replay else self.memory.duration_s
        self.vision_through_s = 0.0 if replay else self.memory.duration_s
        self.memory.set_processed_through(self.processed_through_s)
        self.state = "idle" if replay else "ready"
        self.gap = None
        self.error = None
        self.frames: dict[str, dict] = {}
        self.answer = None
        self.rotation = 0.0
        self.selection = 0
        self.controls_enabled = True
        self.revision = 0
        self.source_revision = 0
        self.source_command = None
        self._ingest = deque()
        self._audio_ingest = deque()
        self._questions = deque()
        self._active_question = False
        self._closed = False
        self._audio_scheduled = 0.0
        self._frame_bucket = -1
        self._vision_scheduled = -1.0
        self._source_started = False
        self._source_ended = False
        self._retryable_gap = False
        self._source_suspended = False
        self._ingest_epoch = 0
        self._failures = {'audio':0,'vision':0}
        self._last_clock_wall = None
        self._source_owner = None
        self._answer_turn = True
        self.metrics = {"frames_processed": 0, "audio_windows_processed": 0,
                        "exact_frames_reused": 0,
                        "last_answer_s": None, "last_vision_s": None, "last_audio_s": None}
        self.bot = None
        self._active_job = None
        self._active_audio_job = None
        self._browser_events = deque()
        self._monitor_stop = threading.Event()
        if self.model is not None and hasattr(self.model, "diagnostics"):
            self.model.diagnostics = self.diagnostics
            self.model.diagnostic_context = lambda: {"run_id": (self._active_job or {}).get("run_id", self.run_id),
                "job_id": (self._active_job or {}).get("job_id", "")}
        self.diagnostics.event("session_started", run_id=self.run_id, mode=self.mode,
                               duration_s=self.memory.duration_s, has_audio=bool(self.video_info.get("has_audio")))
        self.worker = threading.Thread(target=self._work, daemon=True, name="recall-local-worker")
        self.worker.start()
        self.audio_worker = threading.Thread(target=self._work, kwargs={"audio_only": True}, daemon=True, name="recall-audio-worker")
        self.audio_worker.start()
        self.monitor = threading.Thread(target=self._monitor, daemon=True, name="recall-diagnostics")
        self.monitor.start()

    def _monitor(self):
        while not self._monitor_stop.wait(2):
            with self.condition:
                self._suspend_if_stale()
            status = self.status()
            self.diagnostics.event("session_snapshot", **{k: status[k] for k in (
                "run_id", "state", "playback_s", "processed_through_s", "audio_through_s",
                "vision_through_s", "lag_s", "queue_depth", "questions_waiting", "source_connected", "source_age_s")},
                active_age_s=(status.get("active_job") or {}).get("age_s", 0))
            hardware = system_snapshot()
            # Detailed hardware data is included in the report; periodic samples
            # contain only numbers, never device identifiers or filesystem paths.
            mem = hardware.get("memory", {})
            gpu = next(iter(hardware.get("gpu", {}).get("devices", [])), {})
            self.diagnostics.event("hardware_snapshot", ram_total_mb=mem.get("total_mb"),
                ram_available_mb=mem.get("available_mb"), process_memory_mb=mem.get("process_mb"),
                gpu_utilization_pct=gpu.get("utilization_pct"), gpu_memory_used_mb=gpu.get("memory_used_mb"),
                gpu_memory_total_mb=gpu.get("memory_total_mb"), gpu_temperature_c=gpu.get("temperature_c"))
            self.diagnostics.event('performance_sample', run_id=self.run_id,
                sample_age_s=hardware.get('sample_age_s'), cpu_utilization_pct=hardware.get('cpu_power',{}).get('utilization_pct'),
                ac_connected=hardware.get('cpu_power',{}).get('ac_connected'),
                **{'gpu_'+key:gpu.get(source) for key,source in {
                    'clock_mhz':'clock_mhz','max_clock_mhz':'max_clock_mhz','memory_clock_mhz':'memory_clock_mhz',
                    'power_w':'power_w','power_limit_w':'power_limit_w','clock_reasons':'clock_reasons'}.items()})

    def diagnostic_report(self):
        status = self.status()
        status.pop("title", None)
        log = self.diagnostics.snapshot()
        return {"schema_version": 1, "status": status, "hardware": system_snapshot(),
                "events": log.get("recent", []),
                "logging": {k: v for k, v in log.items() if k not in {"recent", "path", "files"}},
                "privacy": "Local operational metadata only; no footage, transcript, questions or credentials."}

    def browser_event(self, data):
        events = {"play", "playing", "pause", "waiting", "stalled", "seeking", "seeked", "ended",
                  "error", "abort", "visibility", "pagehide", "snapshot", "control", "heartbeat_start",
                  "heartbeat_complete", "heartbeat_error", "start_error", "freeze", "resume", "source_command",
                  "javascript_error", "unhandled_rejection", "loadedmetadata", "ratechange"}
        name = str(data.get("event", ""))
        if name.removeprefix("browser_") not in events or not name.startswith("browser_"):
            raise ValueError("Unknown diagnostic event.")
        with self.lock:
            if data.get("run_id") != self.run_id:
                raise ValueError("Diagnostic event belongs to an earlier replay.")
            now = time.monotonic()
            while self._browser_events and self._browser_events[0] < now - 1:
                self._browser_events.popleft()
            if len(self._browser_events) >= 30:
                raise BusyError("Diagnostic event rate exceeded.")
            self._browser_events.append(now)
            self.diagnostics.event(name, **{k: v for k, v in data.items() if k != "event"})
        return {"ok": True}

    def _empty_memory(self) -> StreamMemory:
        info = self.video_info if hasattr(self, "video_info") else {}
        path = self.video_path
        return StreamMemory(VideoMeta(
            id=uuid.uuid4().hex[:12], title=path.stem if path else "Choose a recording",
            duration_s=float(info.get("duration_s", 0)), fps=float(info.get("fps", 30)),
            width=int(info.get("width", 0)), height=int(info.get("height", 0)),
            source=str(path or "")), through_s=0)

    @property
    def processed_through_s(self) -> float:
        return min(self.audio_through_s, self.vision_through_s, self.playback_s)

    def _rewind_schedulers(self) -> None:
        audio = self._active_audio_job or {}
        vision = self._active_job or {}
        self._audio_scheduled = max(self.audio_through_s, audio.get('end_s',0) if audio.get('run_id')==self.run_id else 0)
        t = max(self.vision_through_s, vision.get('frame_s',0) if vision.get('run_id')==self.run_id else 0)
        self._frame_bucket = int(t // self.pipeline['frame_interval_s']) if self.metrics['frames_processed'] or vision.get('kind')=='vision' else -1
        self._vision_scheduled = t if self._frame_bucket >= 0 else -1

    def _suspend_if_stale(self) -> None:
        if (self.mode=='replay' and self.state=='playing' and self._last_clock_wall is not None
                and time.monotonic()-self._last_clock_wall > SOURCE_TIMEOUT_S and not self._source_suspended):
            count = len(self._ingest)+len(self._audio_ingest)
            self._ingest.clear(); self._audio_ingest.clear()
            self._source_suspended = True
            self._rewind_schedulers()
            self.diagnostics.event('source_work_suspended',run_id=self.run_id,dropped_jobs=count,reason='heartbeat_lost')

    def _skip_to_playhead(self, position: float, reason: str) -> None:
        """Explicitly mark missing analysis; never invent records across the hole."""
        lanes = [('vision',self.vision_through_s)]
        if self.video_info.get('has_audio'):
            lanes.append(('audio',self.audio_through_s))
        for lane,start in lanes:
            if start < position:
                self.memory.add_coverage_gap(start,position,lane,reason)
        self._ingest_epoch += 1  # In-flight work from the older position cannot commit.
        self._ingest.clear(); self._audio_ingest.clear()
        self.playback_s = self.audio_through_s = self.vision_through_s = position
        self._audio_scheduled = position
        self._frame_bucket = int(position // self.pipeline['frame_interval_s'])
        self._vision_scheduled = position
        self._ingest.append((self.run_id,'vision',(position,None)))
        self._source_suspended = False
        self._source_started = True
        self.gap = self.error = None
        self._retryable_gap = False
        self._failures = {'audio':0,'vision':0}
        self.memory.set_processed_through(self.processed_through_s)
        self.diagnostics.event('coverage_skipped',run_id=self.run_id,reason=reason,playback_s=position,
                               coverage_gap_count=len(self.memory.coverage_gaps))

    def status(self, *, include_token: bool = False) -> dict:
        with self.lock:
            age = None if self._last_clock_wall is None else max(0, time.monotonic() - self._last_clock_wall)
            connected = age is not None and age <= SOURCE_TIMEOUT_S
            state = "source_disconnected" if self.mode == "replay" and self.state == "playing" and not connected else self.state
            jobs = [{"kind": job["kind"], "job_id": job["job_id"],
                     "age_s": round(time.monotonic() - job["started"], 3)}
                    for job in (self._active_job, self._active_audio_job) if job is not None]
            result = {
                "run_id": self.run_id, "mode": self.mode, "title": self.memory.video.title,
                "duration_s": self.memory.duration_s, "has_video": self.video_path is not None,
                "playback_s": self.playback_s, "processed_through_s": self.processed_through_s,
                "audio_through_s": self.audio_through_s, "vision_through_s": self.vision_through_s,
                "state": state, "gap": self.gap, "error": self.error,
                "source_connected": connected, "source_age_s": None if age is None else round(age, 2),
                "lag_s": max(0, self.playback_s - self.processed_through_s),
                "provider": f"{getattr(self.model, 'model', 'Local model')} · local" if self.model else "Offline evidence tools",
                "model": getattr(self.model, "model", None),
                "pipeline": dict(self.pipeline),
                "controls_enabled": self.controls_enabled,
                "queue_depth": len(self._ingest) + len(self._audio_ingest), "questions_waiting": len(self._questions),
                'audio_queue_depth':len(self._audio_ingest), 'vision_queue_depth':len(self._ingest),
                'coverage_gaps':copy.deepcopy(self.memory.coverage_gaps),
                'resume_available':bool(self.video_path and self.playback_s > 0),
                "retry_available": bool(self.gap and self._retryable_gap),
                "metrics": dict(self.metrics), "twitch": self.bot.status() if self.bot else {"state": "not_connected"},
                "active_job": next(iter(jobs), None), "active_jobs": jobs,
            }
            if include_token:
                result["csrf_token"] = self.csrf_token
            return result

    def memory_dict(self, now: float | None = None) -> dict:
        with self.lock:
            cutoff = self.processed_through_s
            if now is not None:
                cutoff = min(cutoff, finite_time(now))
            out = self.memory.to_dict(through_s=cutoff)
            labels={b['evidence_id']:b['name'] for b in profile_at(self.memory.profile_versions,cutoff)['data']['speaker_bindings']}
            for row in out['records']:
                if row['kind']=='transcript' and row['evidence_id'] in labels:
                    row['speaker']=labels[row['evidence_id']]
                    row['payload']={**row['payload'],'speaker_basis':'creator_label_for_this_excerpt_only'}
            out["video"]["source"] = ""  # Don't expose the local filesystem to the overlay.
            out["frames"] = [{"id": f["id"], "t": f["t"], "url": f["url"]}
                             for f in self.frames.values() if f["t"] <= cutoff and f["run_id"] == self.run_id]
            out.update(run_id=self.run_id, through_s=cutoff,
                       server={"has_video": self.video_path is not None, "video_url": "/video",
                               "video_name": self.video_path.name if self.video_path else None})
            return out

    def _profile_path(self) -> Path:
        key = hashlib.sha256(str(self.video_path or 'no-source').encode()).hexdigest()[:24]
        return self.cache_dir/'profiles'/f'{key}.json'

    def _load_profile(self) -> None:
        try:
            data = validate_profile(json.loads(self._profile_path().read_text(encoding='utf-8')),[])
            self.memory.profile_versions = [{'version':1,'known_s':0,'data':data}]
        except (OSError,ValueError,TypeError):
            pass

    def stream_context(self) -> dict:
        with self.lock:
            memory = self.memory.bounded(self.processed_through_s)
            profile = profile_at(memory.profile_versions,memory.end_time())
            return {'run_id':self.run_id,'profile':profile['data'],'version':profile['version'],
                'recording_title':memory.video.title,
                'known_s':profile['known_s'],'story_through_s':memory.story_through_s,
                'events':memory.story_events[-12:], 'event_count':len(memory.story_events),
                'story_gaps':memory.story_gaps,
                'semantic_ready':self.semantic.ready,'semantic_error':self.semantic.error,
                'transcripts':[{'evidence_id':r.evidence_id,'t_start':r.t_start,'text':r.text[:180],
                    'speaker':evidence_row(r,profile=profile['data']).get('speaker','unknown')}
                    for r in memory.records if r.kind=='transcript'][-40:]}

    def set_stream_context(self, data: dict) -> dict:
        with self.condition:
            if data.get('run_id') != self.run_id:
                raise ValueError('The recording changed. Refresh stream context before saving.')
            snapshot = self.memory.bounded(self.processed_through_s)
            profile = validate_profile(data.get('profile'),snapshot.records)
            version = max((v['version'] for v in self.memory.profile_versions),default=0)+1
            self.memory.profile_versions.append({'version':version,'known_s':self.processed_through_s,'data':profile})
            # Only static creator settings carry into another replay. Excerpt
            # labels belong to the run and are preserved in its memory bundle.
            persisted = {**profile,'speaker_bindings':[]}
            target = self._profile_path(); target.parent.mkdir(parents=True,exist_ok=True)
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(persisted,ensure_ascii=False),encoding='utf-8')
            temporary.replace(target)
            self._persist_memory()
            self.revision += 1
            self.diagnostics.event('stream_context_saved',run_id=self.run_id,revision=version)
            self.condition.notify_all()
            return self.stream_context()

    def _persist_memory(self) -> None:
        try:
            target = self.cache_dir/'runs'/self.run_id/'memory.json'
            temporary = target.with_suffix('.tmp')
            self.memory.bounded(self.processed_through_s).save(temporary)
            temporary.replace(target)
        except OSError as exc:
            self.diagnostics.event('memory_save_failed',run_id=self.run_id,error_type=type(exc).__name__)

    def _story_due(self) -> bool:
        return bool(not self.gap and callable(getattr(self.model,'summarize_episode',None)) and
            (self.processed_through_s >= self.memory.story_through_s+60 or
             self._source_ended and self.processed_through_s > self.memory.story_through_s+.01))

    def _answer_frame(self, record, run: str, cutoff: float):
        with self.lock:
            if run != self.run_id or record.completion_s > cutoff or record.kind != 'scene':
                return None
            frame = self.frames.get(record.payload.get('frame_id'))
            path = frame['path'] if frame and frame['run_id']==run and frame['t']<=cutoff else None
            video = self.video_path
        if path and path.is_file():
            return base64.b64encode(path.read_bytes()).decode()
        return frame_at(video,record.t_start,width=768) if video else None

    def model_options(self) -> dict:
        speech = f"Whisper {self.pipeline['speech_model']} · {self.pipeline['speech_device']}"
        if not self.model or not callable(getattr(self.model, 'installed_models', None)):
            return {'selected': None, 'models': [], 'speech': speech}
        try:
            return {'selected': self.model.model, 'models': self.model.installed_models(),
                    'speech': speech}
        except Exception:
            return {'selected': self.model.model, 'models': [], 'error': 'Ollama model list is unavailable. Check that the local service is running.'}

    def select_model(self, name: str) -> dict:
        with self.condition:
            if not self.model or not callable(getattr(self.model, 'select_model', None)):
                raise ValueError('Start Recall with local AI enabled to select a model.')
            if self.state in {'playing', 'waiting_for_source'} or self._active_job or self._ingest or self._questions:
                raise BusyError('Pause playback and wait for frame analysis and questions to finish before changing models.')
            if self.pipeline['enabled'] and self._test_started is not None:
                raise ValueError('Start a new test to change models so the timing comparison stays accurate.')
            self.model.select_model(name)
            self.pipeline['model'] = self.model.model
            self.diagnostics.event('model_selected', run_id=self.run_id, model=name)
            return {'selected': self.model.model}

    def test_config(self) -> dict:
        return {'active': dict(self.pipeline), 'speech_models': speech_options(self.cache_dir),
                'cuda_speech_available': cuda_speech_available(), 'max_cpu_threads': min(32, os.cpu_count() or 1),
                'models': self.model_options()}

    def _save_test_result(self) -> None:
        if not self.pipeline['enabled'] or self._test_started is None:
            return
        finished = bool(self._source_ended and self.processed_through_s >= self.memory.duration_s - .01)
        save_result(self.cache_dir, {'run_id': self.run_id, 'config': dict(self.pipeline),
            'state': self.state, 'finished':finished, 'complete':finished and not self.memory.coverage_gaps,
            'coverage_gap_count':len(self.memory.coverage_gaps),
            'duration_s': self.memory.duration_s, 'playback_s': self.playback_s,
            'processed_through_s': self.processed_through_s,
            'elapsed_wall_s': round((self._test_finished or time.monotonic()) - self._test_started, 3),
            **dict(self.metrics), **{k:round(v,3) for k,v in self._test_stats.items()}})

    def test_results(self) -> dict:
        with self.lock:
            self._save_test_result()
            return {'results': read_results(self.cache_dir),
                    'note': 'Elapsed wall time includes pauses and questions. Frame sampling may miss brief actions. No transcript or questions are included.'}

    def start_test(self, settings: dict) -> dict:
        config = validate_config(settings)
        with self.condition:
            if not self.video_path:
                raise ValueError('Choose a recording first, then start a test.')
            if self.state in {'playing','waiting_for_source'} or self._active_job or self._active_audio_job or self._ingest or self._audio_ingest or self._questions:
                raise BusyError('Pause the replay and wait for processing and questions to finish before starting a test.')
            if not self.model or not callable(getattr(self.model,'select_model',None)):
                raise ValueError('Start Recall with local AI enabled first.')
            if not any(item['name']==config['speech_model'] and item['ready'] for item in speech_options(self.cache_dir)):
                raise ValueError('This speech model is not downloaded yet. Select a ready model.')
            if config['speech_device']=='cuda' and not cuda_speech_available():
                raise ValueError('GPU speech requires CUDA 12 and cuDNN 9 libraries. Use CPU speech for now.')
            # Validate and load providers before discarding any existing memory.
            transcriber = LocalTranscriber(self.cache_dir, model=config['speech_model'],
                device=config['speech_device'], cpu_threads=config['cpu_threads'])
            transcriber.prepare()
            self.model.select_model(config['model'])
            self._reset()
            self.pipeline = config
            self.transcriber = transcriber
            self.model.vision_detail = config['vision_detail']
            self.model.cpu_threads = config['model_cpu_threads']
            self._command('restart')
            self.state = 'waiting_for_source'
            self.diagnostics.event('test_started',run_id=self.run_id,model=config['model'])
            self.condition.notify_all()
            return self.status()

    def shared(self) -> dict:
        with self.lock:
            return {"run_id": self.run_id, "rotation": self.rotation, "selection": self.selection,
                    "answer": copy.deepcopy(self.answer), "controls_enabled": self.controls_enabled,
                    "revision": self.revision, "source_command": self.source_command,
                    "source_revision": self.source_revision}

    def _reset(self) -> None:
        self._save_test_result()
        self._persist_memory()
        self.diagnostics.event("run_reset", run_id=self.run_id, playback_s=self.playback_s)
        old_frames = [item["path"] for item in self.frames.values()]
        for _, _, future in self._questions:
            if not future.done():
                future.set_exception(RuntimeError("The source restarted. Ask again in the new session."))
        self.run_id = uuid.uuid4().hex
        self.mode = "replay"
        previous_video = self.memory.video
        self.memory = self._empty_memory()
        if previous_video.source == self.memory.video.source:
            self.memory.video.title = previous_video.title
        self.memory.run_id = self.run_id
        self.memory.semantic = self.semantic
        self._load_profile()
        self._conversations.clear()
        self.frames = {}
        self.playback_s = self.audio_through_s = self.vision_through_s = 0.0
        self._audio_scheduled = 0.0
        self._frame_bucket = -1
        self._vision_scheduled = -1.0
        self._source_started = self._source_ended = self._retryable_gap = False
        self._source_suspended = False
        self._ingest_epoch += 1
        self._failures = {'audio':0,'vision':0}
        self._last_clock_wall = None
        self._source_owner = None
        self._ingest.clear()
        self._audio_ingest.clear()
        self._questions.clear()
        self.answer = None
        self.selection = 0
        self.gap = self.error = None
        self.state = "idle"
        self.metrics.update(frames_processed=0, audio_windows_processed=0, exact_frames_reused=0,
                            last_answer_s=None, last_vision_s=None, last_audio_s=None)
        self._test_started = None
        self._test_finished = None
        self._test_stats = dict.fromkeys(self._test_stats, 0)
        self.diagnostics.event("run_started", run_id=self.run_id, duration_s=self.memory.duration_s)
        self.revision += 1
        generated_frames = (self.cache_dir / "frames").resolve()
        for path in old_frames:
            try:
                # Bundles can point to the user's saved frame assets. Only
                # remove transient frames generated by this session's cache.
                if path.resolve().is_relative_to(generated_frames):
                    path.unlink(missing_ok=True)
            except OSError:
                pass

    def load_source(self, video: Path) -> dict:
        info = probe(video)
        if not info.get("width") or not info.get("duration_s"):
            raise ValueError("Choose a playable video recording.")
        with self.lock:
            self.video_path = video.resolve()
            self.video_info = info
            self._reset()
            self._command("restart")
            return self.status()

    def _command(self, action: str) -> None:
        self.source_command = action
        self.source_revision += 1
        self.diagnostics.event("source_command", run_id=self.run_id, action=action,
                               source_revision=self.source_revision, playback_s=self.playback_s)

    def control(self, action: str, *, enabled=None, index=None, remote: bool = False) -> dict:
        with self.condition:
            self.diagnostics.event("control_requested", run_id=self.run_id, action=action, remote=remote)
            if remote and action.startswith("cube_") and not self.controls_enabled:
                raise ValueError("Shared viewer controls are paused by the streamer.")
            if action in ("play", "pause", "restart"):
                if not self.video_path:
                    raise ValueError("Choose a recording before starting replay.")
                if action == "restart" or (action == "play" and self.mode != "replay"):
                    self._reset()
                    action = "restart"
                if action == "play" and self.gap:
                    raise ValueError("Retry processing, or resume from the playhead to mark missing analysis and continue.")
                self._command(action)
                self.state = "paused" if action == "pause" else "waiting_for_source"
            elif action == 'resume_from_playhead':
                if not self.video_path or self.playback_s <= 0:
                    raise ValueError('Start a recording before resuming at the playhead.')
                self._skip_to_playhead(self.playback_s,'manual_resume')
                self.state = 'waiting_for_source'
                self._command('play')
            elif action == "retry_processing":
                if not self.gap or not self._retryable_gap:
                    raise ValueError("Only a processing failure can be retried. Source jumps require a clean restart.")
                if self._active_job or self._active_audio_job:
                    raise BusyError("Wait for the current work to finish, then retry processing.")
                # Retry the missing work from the owned recording. The accepted
                # source clock and existing evidence do not move or get cleared.
                self._ingest.clear()
                self._audio_ingest.clear()
                self._audio_scheduled = self.audio_through_s
                self._frame_bucket = int(self.vision_through_s // self.pipeline['frame_interval_s']) if self.metrics["frames_processed"] else -1
                self._vision_scheduled = self.vision_through_s if self.metrics["frames_processed"] else -1.0
                self._source_started = True
                self._source_suspended = False
                self.gap = self.error = None
                self._retryable_gap = False
                self.state = "paused"
                self._command("pause")
                self._schedule_ingest()
                self.diagnostics.event("processing_retried", run_id=self.run_id,
                                       playback_s=self.playback_s, processed_through_s=self.processed_through_s)
            elif action == "clear":
                self._reset()
                self._command("pause")
            elif action == "clear_answer":
                self.answer = None
                self.selection = 0
            elif action in ("controls", "enable_controls", "disable_controls"):
                self.controls_enabled = bool(enabled) if action == "controls" else action == "enable_controls"
            elif action in ("cube_left", "cube_right"):
                self.rotation += (-1 if action == "cube_left" else 1) * math.pi / 12
            elif action in ("cube_next", "cube_prev", "select"):
                count = len((self.answer or {}).get("citations", []))
                if count:
                    self.selection = (int(index) if action == "select" else
                                      self.selection + (1 if action == "cube_next" else -1)) % count
            else:
                raise ValueError("Unknown control.")
            self.revision += 1
            self.condition.notify_all()
            return self.shared()

    def _schedule_ingest(self, captured: tuple[float, str] | None = None) -> None:
        """Bounded work descriptors; deferred samples come from the owned file.

        Only the acknowledged source clock authorizes input. Slower processing
        leaves a backlog on disk, not a queue of unlimited JPEGs in memory, and
        never turns elapsed lag into a missing-evidence failure.
        Call with the condition lock held.
        """
        if self._closed or self.gap or self._source_suspended or not self._source_started or self.mode != "replay":
            return
        position = self.playback_s
        if self.video_info.get("has_audio"):
            while len(self._audio_ingest) < MAX_PENDING_PER_LANE:
                end = min(self._audio_scheduled + 10, self.memory.duration_s)
                if self._source_ended:
                    end = min(end, position)
                if end > position + 0.001 or end <= self._audio_scheduled:
                    break
                self._audio_ingest.append((self.run_id, "audio", (self._audio_scheduled, end)))
                self._audio_scheduled = end
        interval = self.pipeline['frame_interval_s']
        limit = 3 if self.pipeline['catch_up']=='live' else MAX_PENDING_PER_LANE
        if self.pipeline['catch_up']=='live':
            oldest = max(0,(int(position//interval)-2)*interval)
            active = (self._active_job or {}).get('frame_s',self.vision_through_s)
            dropped = [job for job in self._ingest if job[2][0] < oldest]
            unscheduled = (self._frame_bucket+1)*interval < oldest
            if dropped or unscheduled:
                self.memory.add_coverage_gap(max(self.vision_through_s,active),oldest,'vision','backpressure')
                self._ingest = deque(job for job in self._ingest if job[2][0] >= oldest)
                self._frame_bucket = max(self._frame_bucket,int(oldest//interval)-1)
                self.diagnostics.event('stale_frames_dropped',run_id=self.run_id,dropped_jobs=len(dropped),
                    start_s=max(self.vision_through_s,active),end_s=oldest,reason='backpressure')
        while len(self._ingest) < limit:
            bucket = self._frame_bucket + 1
            sample = bucket * self.pipeline['frame_interval_s']
            if sample > position:
                break
            raw = None
            if captured and int(captured[0] // self.pipeline['frame_interval_s']) == bucket:
                sample, raw = captured
            self._ingest.append((self.run_id, "vision", (sample, raw)))
            self._frame_bucket = bucket
            self._vision_scheduled = sample
        # Include the final fractional interval so completion can reach EOF.
        if (self._source_ended and len(self._ingest) < limit
                and position > self._vision_scheduled + 0.001):
            raw = captured[1] if captured and abs(captured[0] - position) < 0.001 else None
            self._ingest.append((self.run_id, "vision", (position, raw)))
            self._vision_scheduled = position

    def source_update(self, data: dict) -> dict:
        """Only the source player supplies this clock, never the evidence player."""
        position = finite_time(data.get("time_s", 0))
        with self.condition:
            if data.get("run_id") != self.run_id:
                raise ValueError("Source session changed. Refresh its status and restart.")
            if self.mode != "replay" or not self.video_path:
                raise ValueError("Start a recording replay before supplying source frames.")
            # Reject the whole callback before changing any clock, owner or queue.
            validated_frame = None
            if data.get("frame") is not None:
                frame = data["frame"]
                if not isinstance(frame, dict):
                    raise ValueError("Expected a frame object.")
                frame_t = finite_time(frame.get("t", position))
                if frame_t > min(position, self.memory.duration_s) + 0.05:
                    raise ValueError("A source frame cannot come from the future.")
                raw = str(frame.get("data", ""))
                if raw.startswith("data:"):
                    raw = raw.partition(",")[2]
                try:
                    image = base64.b64decode(raw, validate=True)
                except Exception as exc:
                    raise ValueError("Invalid frame image.") from exc
                if not image.startswith(b"\xff\xd8") or len(image) > 2_000_000:
                    raise ValueError("Expected a JPEG frame smaller than 2 MB.")
                validated_frame = (frame_t, raw)
            owner = str(data.get("source_id", "broadcast"))[:100]
            wall = time.monotonic()
            stale = self._last_clock_wall is not None and wall - self._last_clock_wall > SOURCE_TIMEOUT_S
            if self._source_owner and owner != self._source_owner:
                if not stale:
                    raise ValueError("Another broadcast page already controls this source.")
            self._source_owner = owner
            position = min(position, self.memory.duration_s)
            if position + 0.75 < self.playback_s:
                self._reset()
                self._command("pause")
                self.gap = "Source moved backward. Restart to begin a clean memory."
                self.state = "gap"
                return self.status()
            # A forward jump is a declared coverage hole, not invented understanding.
            elapsed = min(2.0, wall - self._last_clock_wall) if self._last_clock_wall else 0.5
            if position - self.playback_s > max(3.0, elapsed * 1.6 + 1.5):
                self._skip_to_playhead(position,'source_jump')
            self._last_clock_wall = wall
            self.playback_s = position
            self._source_suspended = False
            if self.gap:
                return self.status()
            playing, ended = bool(data.get("playing")), bool(data.get("ended"))
            old_state = self.state
            self.state = "playing" if playing else ("ended" if ended else "paused")
            self._source_started = True
            self._source_ended = ended
            if self._test_started is None:
                self._test_started = wall
            self._test_stats['peak_lag_s'] = max(self._test_stats['peak_lag_s'], self.playback_s - self.processed_through_s)
            if self.state != old_state:
                self.diagnostics.event("source_state", run_id=self.run_id, source_id=owner,
                                       state=self.state, playback_s=position, playing=playing, ended=ended)
            if not self.video_info.get("has_audio"):
                self.audio_through_s = position
            self._schedule_ingest(validated_frame)
            self.memory.set_processed_through(self.processed_through_s)
            self.condition.notify_all()
            return self.status()

    def request_answer(self, question: str, now: float | None = None, *, timeout: float = 120,
                       conversation_id: str = '', share: bool = True) -> dict:
        question = question.strip()
        if not question or len(question) > 1500:
            raise ValueError("Ask a question between 1 and 1,500 characters.")
        if not isinstance(share, bool):
            raise ValueError('Choose whether to share this answer.')
        if not isinstance(conversation_id,str) or conversation_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',conversation_id):
            raise ValueError('Invalid local conversation.')
        with self.condition:
            if int(self._active_question) + len(self._questions) >= 2:
                raise BusyError("One question is running and one is waiting. Please try again shortly.")
            cutoff = self.processed_through_s
            if now is not None:
                cutoff = min(cutoff, finite_time(now))
            future = Future()
            # Freeze both source cutoff and available evidence at receipt.
            snapshot = self.memory.bounded(cutoff)
            snapshot.run_id = self.run_id
            snapshot.conversation_id = conversation_id
            snapshot.share_answer = share
            snapshot.conversation = [copy.deepcopy(row) for row in self._conversations.get(conversation_id,[])
                                     if row['through_s'] <= cutoff] if conversation_id else []
            self._questions.append((question, snapshot, future))
            self.diagnostics.event("question_queued", run_id=self.run_id, question_chars=len(question),
                                   processed_through_s=cutoff, questions_waiting=len(self._questions))
            self.condition.notify_all()
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            future.cancel()
            self.diagnostics.event("question_timeout", run_id=snapshot.run_id, timeout_s=timeout)
            raise BusyError("The local model is taking too long. Check model status before asking again.") from exc

    def _work(self, *, audio_only: bool = False) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self._closed or
                    (self._audio_ingest if audio_only else self._questions or self._ingest or self._story_due()))
                if self._closed:
                    return
                if audio_only:
                    task = self._audio_ingest.popleft()
                elif self._questions and (self._answer_turn or not self._ingest):
                    question, snapshot, future = self._questions.popleft()
                    self._active_question = True
                    task = (snapshot.run_id, "question", (question, snapshot, future))
                    self._answer_turn = False
                elif self._ingest:
                    task = self._ingest.popleft()
                    self._answer_turn = True
                else:
                    end = min(self.processed_through_s,self.memory.story_through_s+60)
                    task = (self.run_id,'story',(self.memory.story_through_s,end,self.memory.bounded(end)))
                run, kind, payload = task
                epoch = self._ingest_epoch
                started = time.monotonic()
                job_id = uuid.uuid4().hex[:12]
                job = {"kind": kind, "job_id": job_id, "started": started, "run_id": run}
                job.update({'start_s':payload[0],'end_s':payload[1]} if kind=='audio' else {'frame_s':payload[0]} if kind=='vision' else {})
                if audio_only:
                    self._active_audio_job = job
                else:
                    self._active_job = job
            success = False
            outcome = 'cancelled'
            self.diagnostics.event("job_started", run_id=run, job_id=job_id, kind=kind,
                queue_depth=len(self._ingest), **({"start_s": payload[0], "end_s": payload[1]}
                if kind == "audio" else {"frame_s": payload[0]} if kind == "vision" else {}))
            try:
                with self.lock:
                    if run != self.run_id:
                        if kind == "question" and not payload[2].done():
                            payload[2].set_exception(RuntimeError("The source session changed."))
                        continue
                    if kind != "question" and (self.gap or epoch != self._ingest_epoch):
                        continue
                    video = self.video_path
                if kind == "question":
                    question, snapshot, future = payload
                    if future.cancelled():
                        continue
                    answer = (answer_with_model(snapshot, question, self.model,
                                                now=snapshot.through_s, run_id=run,
                                                answer_style=self.pipeline['answer_style'],
                                                allow_predictions=self.pipeline['allow_predictions'],
                                                frame_loader=lambda r:self._answer_frame(r,run,snapshot.end_time()),
                                                conversation=getattr(snapshot,'conversation',[]))
                              if self.model and self.mode != "fixture" else
                              ask(snapshot, question, now=snapshot.through_s))
                    result = answer.to_dict()
                    result.update(run_id=run, through_s=snapshot.through_s)
                    with self.lock:
                        if future.cancelled():
                            continue
                        if run != self.run_id:
                            raise RuntimeError("The source session changed. Ask again.")
                        if getattr(snapshot, 'share_answer', True):
                            self.answer = result
                            self.selection = 0
                            self.revision += 1
                        self.metrics["last_answer_s"] = round(time.monotonic() - started, 3)
                        conversation_id = getattr(snapshot,'conversation_id','')
                        if conversation_id and result.get('status')=='answered':
                            history = self._conversations.setdefault(conversation_id,[])
                            history.append({'question':question[:300],'answer':result['summary'][:400],
                                'evidence_ids':[c['evidence_id'] for c in result.get('citations',[])],
                                'through_s':snapshot.end_time()})
                            del history[:-3]
                            while len(self._conversations)>32: self._conversations.pop(next(iter(self._conversations)))
                    if not future.done():
                        future.set_result(result)
                    self.diagnostics.event("answer_ready", run_id=run, job_id=job_id,
                        state=result.get("status"), citation_count=len(result.get("citations", [])))
                    success = result.get("status") != "error"
                    if not success:
                        outcome = 'failed'
                elif kind == 'story':
                    start,end,snapshot = payload
                    events = review_episode(snapshot,self.model,start,end)
                    with self.lock:
                        if run == self.run_id and epoch == self._ingest_epoch and not self.gap:
                            old_ids = {e['id'] for e in self.memory.story_events}
                            self.memory.story_events.extend(e for e in events if e['id'] not in old_ids)
                            self.memory.story_through_s = end
                            self._persist_memory()
                            self.diagnostics.event('story_ready',run_id=run,start_s=start,end_s=end,record_count=len(events))
                            success = True
                elif kind == "audio":
                    start, end = payload
                    if hasattr(self.transcriber,'hotwords'):
                        with self.lock:
                            profile=profile_at(self.memory.profile_versions,end)['data']
                        names=[profile[k] for k in ('streamer','character') if profile[k]]
                        names += [name for person in profile['cast'] for name in [person['name'],*person['aliases']]]
                        self.transcriber.hotwords=', '.join(dict.fromkeys(names))[:800]
                    records = self.transcriber.transcribe_window(video, start, end)
                    with self.lock:
                        if run == self.run_id and epoch == self._ingest_epoch and not self.gap:
                            for record in records:
                                self.memory.append(record)
                            self.audio_through_s = max(self.audio_through_s,end)
                            self.metrics["audio_windows_processed"] += 1
                            self.metrics["last_audio_s"] = round(time.monotonic() - started, 3)
                            self.memory.set_processed_through(self.processed_through_s)
                            self.diagnostics.event("audio_ready", run_id=run, job_id=job_id,
                                record_count=len(records), start_s=start, end_s=end)
                            success = True
                elif kind == "vision":
                    t, raw = payload
                    if self.model is None:
                        raise RuntimeError("The local vision model is not connected. Start with --local-ai.")
                    if raw is None:
                        raw = frame_at(video, t, width=self.pipeline['frame_width'])
                    with self.lock:
                        frame_context = recording_context(self.memory, t)
                    extra = {'stream_context':frame_context} if getattr(self.model,'supports_frame_context',False) else {}
                    observed = self.model.observe_frame(raw, t, **extra)
                    description = str(observed.get("description", "")).strip()
                    topics = [str(s)[:80] for s in observed.get("topics", [])][:8]
                    frame_id = f"{run}-{int(t * 1000):09d}"
                    path = self.cache_dir / "frames" / f"{frame_id}.jpg"
                    with self.lock:
                        if run != self.run_id or epoch != self._ingest_epoch or self.gap:
                            continue
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(base64.b64decode(raw))
                        url = f"/frames/{frame_id}"
                        self.frames[frame_id] = {"id": frame_id, "t": t, "url": url,
                                                 "path": path, "run_id": run}
                        if description:
                            record = SceneSegment(
                                t_start=t, t_end=t, text=description, caption=description,
                                activity="visual_observation",
                                payload={"provider": "local-ollama-vision", "model": getattr(self.model, "model", "local"), "frame_url": url,
                                         "frame_id": frame_id, "window_end": t, "topics": topics,
                                         "visible_text": str(observed.get("visible_text", ""))[:2000],
                                         "objects": observed.get("objects", [])},
                            )
                            self.memory.append(record)
                        self.vision_through_s = max(self.vision_through_s,t)
                        self.metrics["frames_processed"] += 1
                        self.metrics["exact_frames_reused"] += int(bool(observed.get("reused_exact_frame")))
                        self.metrics["last_vision_s"] = round(time.monotonic() - started, 3)
                        self.memory.set_processed_through(self.processed_through_s)
                        self.diagnostics.event("vision_ready", run_id=run, job_id=job_id, frame_s=t,
                            reused_exact_frame=bool(observed.get("reused_exact_frame")))
                        success = True
            except Exception as exc:
                outcome = 'failed'
                self.diagnostics.event("job_failed", run_id=run, job_id=job_id, kind=kind,
                                       error_type=type(exc).__name__, reason=getattr(exc, "code", "unknown"),
                                       duration_s=time.monotonic() - started)
                with self.lock:
                    if kind == "question":
                        future = payload[2]
                        if not future.done():
                            future.set_exception(exc)
                    elif kind == 'story' and run == self.run_id and epoch == self._ingest_epoch:
                        # Optional enrichment must never stop raw audio/video.
                        self.memory.story_events.extend(extractive_anchors(payload[2],payload[0],payload[1]))
                        self.memory.story_gaps.append({'start_s':payload[0],'end_s':payload[1],'reason':'summary_failed'})
                        self.memory.story_through_s = payload[1]
                        self._persist_memory()
                        self.diagnostics.event('story_update_skipped',run_id=run,start_s=payload[0],end_s=payload[1],error_type=type(exc).__name__)
                    elif run == self.run_id and epoch == self._ingest_epoch:
                        self._failures[kind] += 1
                        if self.pipeline['catch_up']=='live' and self._failures[kind] < 3:
                            start = self.audio_through_s if kind=='audio' else self.vision_through_s
                            end = payload[1] if kind=='audio' else payload[0]
                            self.memory.add_coverage_gap(start,end,kind,'processing_failed')
                            if kind=='audio': self.audio_through_s = max(start,end)
                            else: self.vision_through_s = max(start,end)
                            self.memory.set_processed_through(self.processed_through_s)
                            continue
                        self.error = f"{kind.title()} analysis stopped ({getattr(exc, 'code', type(exc).__name__)}). Retry processing to keep your memory."
                        self.gap = f"{kind.title()} processing failed; earlier completed evidence is still searchable."
                        self._retryable_gap = True
                        self.state = "gap"
                        self._ingest.clear()
                        self._audio_ingest.clear()
                        self._command("pause")
            finally:
                self.diagnostics.event("job_finished", run_id=run, job_id=job_id, kind=kind,
                                       success=success, outcome='completed' if success else outcome, duration_s=time.monotonic() - started)
                with self.condition:
                    if run == self.run_id:
                        self._test_stats[f'{kind}_seconds'] = self._test_stats.get(f'{kind}_seconds',0) + time.monotonic() - started
                        if kind == 'question':
                            self._test_stats['question_count'] += 1
                        if outcome=='failed':
                            self._test_stats['failed_jobs'] += 1
                        if success and kind in self._failures:
                            self._failures[kind] = 0
                    if audio_only:
                        self._active_audio_job = None
                    else:
                        self._active_job = None
                    if kind == "question":
                        self._active_question = False
                    self._schedule_ingest()
                    if self._source_ended and self.processed_through_s >= self.memory.duration_s - .01 and self._test_finished is None:
                        self._test_finished = time.monotonic()
                        self._save_test_result()
                    self.condition.notify_all()

    def frame_path(self, frame_id: str) -> Path | None:
        with self.lock:
            frame = self.frames.get(frame_id)
            if frame and frame["run_id"] == self.run_id and frame["t"] <= self.processed_through_s:
                return frame["path"]
            return None

    def export(self, folder: Path) -> Path:
        with self.lock:
            folder.mkdir(parents=True, exist_ok=True)
            frame_folder = folder / "frames"
            for frame in self.frames.values():
                if frame["run_id"] == self.run_id and frame["t"] <= self.processed_through_s:
                    frame_folder.mkdir(exist_ok=True)
                    shutil.copy2(frame["path"], frame_folder / f'{frame["id"]}.jpg')
            return self.memory.bounded(self.processed_through_s).save(folder / "memory.json")

    def close(self) -> None:
        self._monitor_stop.set()
        self.monitor.join(timeout=3)
        if self.bot:
            self.bot.stop()
        with self.condition:
            self._closed = True
            self._ingest_epoch += 1
            for _, _, future in self._questions:
                if not future.done():
                    future.set_exception(RuntimeError("The session closed."))
            self._questions.clear()
            self.condition.notify_all()
        self.worker.join(timeout=2)
        self.audio_worker.join(timeout=2)
        with self.lock:
            self._save_test_result()
            self._persist_memory()
        self.diagnostics.event("session_closed", run_id=self.run_id)
        self.diagnostics.close()


def ingest_recording(video: Path, output: Path, model, *, transcriber=None) -> dict:
    """Offline import using the same providers and completed-window discipline."""
    info = probe(video)
    output.mkdir(parents=True, exist_ok=True)
    speech = transcriber or LocalTranscriber(output / "cache")
    memory = StreamMemory(VideoMeta(
        id=uuid.uuid4().hex[:12], title=video.stem, source=str(video.resolve()),
        duration_s=info["duration_s"], fps=info["fps"], width=info["width"], height=info["height"]),
        run_id=uuid.uuid4().hex, through_s=0)
    duration = info["duration_s"]
    frames = output / "frames"
    frames.mkdir(exist_ok=True)
    started = time.monotonic()
    position = 0.0
    while position < duration:
        end = min(duration, position + 10)
        if info["has_audio"]:
            for record in speech.transcribe_window(video, position, end):
                memory.append(record)
        samples = [position, min(position + 5, end - 0.05)]
        for t in sorted(set(max(0, t) for t in samples)):
            raw = frame_at(video, t)
            extra = {'stream_context':recording_context(memory,t)} if getattr(model,'supports_frame_context',False) else {}
            observed = model.observe_frame(raw, t, **extra)
            frame_id = f"{memory.run_id}-{int(t * 1000):09d}"
            (frames / f"{frame_id}.jpg").write_bytes(base64.b64decode(raw))
            memory.append(SceneSegment(
                t_start=t, t_end=t, text=observed["description"], caption=observed["description"],
                activity="visual_observation",
                payload={"provider": "local-qwen-vision", "window_end": t,
                         "frame_id": frame_id, "frame_url": f"/frames/{frame_id}",
                         "topics": observed.get("topics", []),
                         "visible_text": observed.get("visible_text", ""),
                         "objects": observed.get("objects", [])},
            ))
        memory.set_processed_through(end)
        if callable(getattr(model,'summarize_episode',None)) and (end >= memory.story_through_s+60 or end==duration):
            try:
                memory.story_events.extend(review_episode(memory.bounded(end),model,memory.story_through_s,end))
            except Exception:
                # Failed notes cannot replace the original records or stop import.
                memory.story_events.extend(extractive_anchors(memory,memory.story_through_s,end))
                memory.story_gaps.append({'start_s':memory.story_through_s,'end_s':end,'reason':'summary_failed'})
            memory.story_through_s=end
        memory.save(output / "memory.json")
        position = end
    return {"status": "ready", "duration_s": duration, "records": len(memory.records),
            "processing_s": round(time.monotonic() - started, 3), "run_id": memory.run_id,
            "bundle": str(output / "memory.json"), "provider": "fully-local"}
