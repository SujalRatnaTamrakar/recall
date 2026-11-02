"""Replay lifecycle tests with tiny fake media providers; no real inference."""
import base64
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tubelive import runtime
from tubelive.schema import SceneSegment, TranscriptSegment

JPEG = base64.b64encode(b"\xff\xd8fake-owned-frame\xff\xd9").decode()


class Model:
    def __init__(self, block_vision=None, block_answer=False, blank=False):
        self.block_vision = block_vision
        self.block_answer = block_answer
        self.blank = blank
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.seen_evidence = []

    def observe_frame(self, data, t):
        self.calls.append(("vision", t))
        if t == self.block_vision:
            self.entered.set()
            assert self.release.wait(4), "test did not release model"
        return {"description": "" if self.blank else f"Blue paper at {t}",
                "objects": ["paper"], "topics": ["craft"], "visible_text": []}

    def answer_from_evidence(self, question, evidence):
        self.calls.append(("answer", question))
        self.seen_evidence.append(evidence)
        if self.block_answer:
            self.entered.set()
            assert self.release.wait(4), "test did not release model"
        return {"claims": [{"text": "Blue paper is visible.",
                             "evidence_ids": [evidence[0]["evidence_id"]]}], "insufficient": False}


class Speech:
    def __init__(self, silent=False):
        self.calls = []
        self.silent = silent

    def transcribe_window(self, video, start, end):
        self.calls.append((start, end))
        return [] if self.silent else [TranscriptSegment(start, min(start + 1, end),
                    text="Next we add blue paper.", payload={"window_end": end})]


@contextmanager
def session(*, audio=True, duration=15, model=None, speech=None):
    model = model or Model()
    speech = speech or Speech()
    with tempfile.TemporaryDirectory(prefix="tube-runtime-") as folder:
        cache = Path(folder)
        video = cache / "owned.mp4"
        video.write_bytes(b"test")
        with patch.object(runtime, "probe", return_value={"duration_s": duration, "width": 640,
                          "height": 360, "fps": 30, "has_audio": audio}):
            instance = runtime.ReplaySession(video=video, cache_dir=cache / "cache",
                                             model=model, transcriber=speech, replay=True)
            try:
                yield instance, model, speech
            finally:
                model.release.set()
                instance.close()


def update(instance, position, *, frame=False, ended=False, playing=True):
    data = {"run_id": instance.run_id, "source_id": "test-owner", "time_s": position,
            "playing": playing, "ended": ended}
    if frame:
        data["frame"] = {"t": position, "data": JPEG}
    return instance.source_update(data)


def wait_for(instance, predicate):
    with instance.condition:
        assert instance.condition.wait_for(predicate, timeout=3), instance.status()


def advance(instance, end, *, frames=True, ended=False):
    positions = list(range(int(instance.playback_s), int(end) + 1))
    if not positions or positions[-1] != end:
        positions.append(end)
    for position in positions:
        update(instance, position, frame=frames and (position % 5 == 0 or (ended and position == end)),
               ended=ended and position == end, playing=not (ended and position == end))


def test_runtime_starts_empty_and_audio_chunk_is_not_available_early():
    with session() as (instance, model, speech):
        assert not instance.memory_dict()["records"] and instance.processed_through_s == 0
        advance(instance, 9)
        wait_for(instance, lambda: instance.metrics["frames_processed"] >= 2)
        assert not speech.calls and instance.processed_through_s == 0
        assert all(r["t_end"] <= 0 and r["kind"] != "transcript"
                   for r in instance.memory_dict()["records"])
        advance(instance, 10)
        wait_for(instance, lambda: instance.processed_through_s == 10)
        assert speech.calls == [(0.0, 10.0)]
        records = instance.memory_dict()["records"]
        assert records and all(r["t_end"] <= 10 for r in records)
        assert any(r["payload"].get("window_end") == 10 for r in records if r["kind"] == "transcript")


def test_live_frame_worker_receives_recording_background():
    class ContextModel(Model):
        supports_frame_context=True
        def observe_frame(self,data,t,*,stream_context):
            self.context=stream_context
            return super().observe_frame(data,t)
    model=ContextModel()
    with session(model=model) as (instance,_,_):
        instance.set_stream_context({'run_id':instance.run_id,'profile':{
            'title':'A paper craft tutorial','description':'Demonstrating paper folding.'}})
        update(instance,0,frame=True)
        wait_for(instance,lambda:instance.metrics['frames_processed']==1)
        assert model.context['title']=='A paper craft tutorial'
        assert model.context['description']=='Demonstrating paper folding.'
        assert instance.stream_context()['recording_title']=='owned'


def test_uploaded_title_survives_restart_but_does_not_label_a_different_source():
    with session() as (instance,_,_):
        instance.memory.video.title='Original upload title.mp4'
        instance.control('restart')
        assert instance.memory.video.title=='Original upload title.mp4'
        other=instance.video_path.with_name('different recording.mp4')
        other.write_bytes(b'test')
        instance.load_source(other)
        assert instance.memory.video.title=='different recording'


def test_diagnostics_track_active_work_without_source_content_and_reset_metrics():
    model = Model(block_vision=0)
    with session(model=model) as (instance, _, _):
        update(instance, 0, frame=True)
        assert model.entered.wait(2)
        active = instance.status()["active_job"]
        assert active["kind"] == "vision" and active["age_s"] >= 0
        model.release.set()
        wait_for(instance, lambda: instance.status()["active_job"] is None)
        assert instance.metrics["frames_processed"] == 1
        events = instance.diagnostics.snapshot()["recent"]
        assert any(e["event"] == "job_started" and e["kind"] == "vision" for e in events)
        assert any(e["event"] == "job_finished" and e["success"] for e in events)
        instance.control("restart")
        assert instance.metrics["frames_processed"] == 0
        assert instance.metrics["last_vision_s"] is None


def test_browser_diagnostics_are_bounded_and_do_not_store_content():
    with session() as (instance, _, _):
        instance.browser_event({"event":"browser_pause", "run_id":instance.run_id,
            "position_s":83.7,"paused":True,"question":"private words", "frame":"secret pixels"})
        record = instance.diagnostics.snapshot()["recent"][-1]
        assert record["event"] == "browser_pause" and record["position_s"] == 83.7
        assert "question" not in record and "frame" not in record
        try:
            instance.browser_event({"event":"browser_pause","run_id":"old-run"})
            assert False
        except ValueError:
            pass


def test_stale_source_is_reported_and_new_page_resumes_without_losing_memory():
    with session() as (instance, _, _):
        advance(instance, 10)
        wait_for(instance, lambda: instance.processed_through_s == 10)
        run = instance.run_id
        count = len(instance.memory.records)
        instance._last_clock_wall -= 20
        assert instance.status()["state"] == "source_disconnected"
        assert not instance.status()["source_connected"]
        instance.source_update({"run_id": run, "source_id": "replacement-page", "time_s": 10, "playing": True})
        assert instance.status()["state"] == "playing"
        assert instance.run_id == run and len(instance.memory.records) == count
        assert instance.processed_through_s == 10


def test_active_source_cannot_be_stolen_and_stale_jump_records_coverage():
    with session(duration=60) as (instance, _, _), patch.object(runtime,'frame_at',return_value=JPEG):
        update(instance, 0)
        try:
            instance.source_update({'run_id':instance.run_id,'source_id':'other','time_s':0,'playing':True})
            assert False,'active source takeover must be refused'
        except ValueError: pass
        instance._last_clock_wall -= 20
        instance.source_update({'run_id':instance.run_id,'source_id':'other','time_s':30,'playing':True})
        assert not instance.gap and instance.playback_s==30
        assert {g['lane'] for g in instance.memory.coverage_gaps}=={'audio','vision'}


def test_runtime_silence_blank_frames_and_pause_still_advance_watermark():
    with session(model=Model(blank=True), speech=Speech(silent=True)) as (instance, _, speech):
        advance(instance, 10)
        wait_for(instance, lambda: instance.processed_through_s == 10)
        update(instance, 10, playing=False)
        assert instance.state == "paused" and instance.processed_through_s == 10
        assert speech.calls == [(0.0, 10.0)] and not instance.memory_dict()["records"]
        assert instance.memory_dict()["frames"]


def test_runtime_ended_short_final_audio_chunk_is_completed():
    with session(duration=12.5, speech=Speech(silent=True)) as (instance, _, speech):
        advance(instance, 12.5, ended=True)
        wait_for(instance, lambda: instance.processed_through_s == 12.5)
        assert speech.calls == [(0.0, 10.0), (10.0, 12.5)]
        assert instance.state == "ended"


def test_runtime_frame_access_and_memory_are_filtered_by_cutoff_and_generation():
    with session() as (instance, _, _):
        advance(instance, 5)
        wait_for(instance, lambda: instance.metrics["frames_processed"] == 2)
        future_frame = next(k for k, v in instance.frames.items() if v["t"] == 5)
        assert instance.frame_path(future_frame) is None
        assert all(f["t"] <= 0 for f in instance.memory_dict()["frames"])
        advance(instance, 10)
        wait_for(instance, lambda: instance.processed_through_s == 10)
        assert instance.frame_path(future_frame).exists()
        assert all(f["t"] <= 4 for f in instance.memory_dict(now=4)["frames"])
        instance.control("restart")
        assert instance.frame_path(future_frame) is None
        assert not instance.memory_dict()["records"] and not instance.shared()["answer"]


def test_runtime_backward_seek_restarts_generation_and_forward_seek_preserves_declared_hole():
    with session(audio=False) as (instance, _, _), patch.object(runtime,'frame_at',return_value=JPEG):
        advance(instance, 5)
        wait_for(instance, lambda: instance.processed_through_s == 5)
        old_run = instance.run_id
        update(instance, 2)
        assert instance.run_id != old_run and instance.state == "gap"
        assert instance.playback_s == 0 and not instance.memory_dict()["records"]
        instance.control("restart")
        old_run = instance.run_id
        update(instance, 10)
        assert instance.run_id == old_run and instance.state == "playing"
        wait_for(instance,lambda: instance.metrics['frames_processed']==1)
        assert instance.processed_through_s == 10
        assert all(r.t_start==10 for r in instance.memory.records)
        assert instance.memory_dict()['coverage_gaps']


def test_runtime_rejected_frame_callback_does_not_mutate_clock_or_queue():
    with session() as (instance, _, _):
        advance(instance, 9, frames=False)
        before = (instance.playback_s, instance._audio_scheduled, instance._source_owner,
                  instance._last_clock_wall, list(instance._ingest))
        try:
            instance.source_update({"run_id": instance.run_id, "source_id": "test-owner", "time_s": 10,
                                    "playing": True, "frame": {"t": 11, "data": JPEG}})
            assert False, "future frame accepted"
        except ValueError:
            pass
        after = (instance.playback_s, instance._audio_scheduled, instance._source_owner,
                 instance._last_clock_wall, list(instance._ingest))
        assert after == before, (before, after)
        try:
            instance.source_update({"run_id": instance.run_id, "source_id": "test-owner", "time_s": 10,
                                    "playing": True, "frame": {"t": 10, "data": "invalid-image"}})
            assert False, "bad image accepted"
        except ValueError:
            pass
        assert instance.playback_s == before[0] and instance._audio_scheduled == before[1]


def test_runtime_running_and_queued_questions_are_invalidated_on_restart():
    model = Model(block_answer=True)
    with session(audio=False, model=model) as (instance, _, _):
        advance(instance, 5)
        wait_for(instance, lambda: instance.processed_through_s == 5)
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(instance.request_answer, "what color paper?", timeout=3)
            assert model.entered.wait(2)
            queued = pool.submit(instance.request_answer, "find the paper", timeout=3)
            wait_for(instance, lambda: len(instance._questions) == 1)
            old_run = instance.run_id
            instance.control("restart")
            model.release.set()
            for future in (running, queued):
                try:
                    future.result(timeout=3)
                    assert False, "old question survived restart"
                except RuntimeError:
                    pass
            assert instance.run_id != old_run and instance.answer is None


def test_runtime_overload_is_bounded_and_frozen_question_snapshot_has_no_future():
    model = Model(block_answer=True)
    with session(audio=False, model=model) as (instance, _, _):
        advance(instance, 5)
        wait_for(instance, lambda: instance.processed_through_s == 5)
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(instance.request_answer, "what color paper?", timeout=3)
            assert model.entered.wait(2)
            queued = pool.submit(instance.request_answer, "find paper", timeout=3)
            wait_for(instance, lambda: len(instance._questions) == 1)
            try:
                instance.request_answer("third paper question", timeout=0.01)
                assert False
            except runtime.BusyError:
                pass
            # A later observed record must not enter already accepted snapshots.
            with instance.lock:
                instance.memory.append(SceneSegment(6, 6, caption="Future red paper"))
                instance.playback_s = instance.audio_through_s = instance.vision_through_s = 6
                instance.memory.set_processed_through(6)
            model.release.set()
            assert running.result(timeout=3)["through_s"] == 5
            assert queued.result(timeout=3)["through_s"] == 5
            assert all(e["t_start"] <= 5 for evidence in model.seen_evidence for e in evidence)


def test_runtime_model_failure_creates_gap_and_no_later_observations():
    class FailedModel(Model):
        def observe_frame(self, *args):
            raise RuntimeError("intentional test model error")
    with session(audio=False, model=FailedModel()) as (instance, _, _):
        update(instance, 0, frame=True)
        wait_for(instance, lambda: instance.state == "gap")
        assert instance.error and not instance.memory_dict()["records"]
        assert instance.source_command == "pause"


def test_runtime_shared_evidence_controls_never_seek_source():
    with session(audio=False) as (instance, _, _):
        advance(instance, 5)
        wait_for(instance, lambda: instance.processed_through_s == 5)
        instance.answer = {"citations": [{"t": 0}, {"t": 5}]}
        before = (instance.playback_s, instance.source_revision, instance.run_id)
        instance.control("cube_next", remote=True)
        instance.control("cube_left", remote=True)
        assert instance.selection == 1 and instance.rotation < 0
        assert before == (instance.playback_s, instance.source_revision, instance.run_id)
        instance.control("disable_controls")
        try:
            instance.control("cube_next", remote=True)
            assert False
        except ValueError:
            pass


def test_runtime_old_running_vision_cannot_commit_into_restarted_run():
    model = Model(block_vision=0)
    with session(audio=False, model=model) as (instance, _, _):
        update(instance, 0, frame=True)
        assert model.entered.wait(2)
        old_run = instance.run_id
        instance.control("restart")
        model.release.set()
        # This queued sentinel executes only after the old vision task finishes.
        result = instance.request_answer("what color paper?", timeout=3)
        assert result["status"] == "insufficient_evidence"
        assert instance.run_id != old_run and not instance.frames and not instance.memory.records


def test_runtime_questions_and_ingestion_take_fair_alternating_turns():
    model = Model(block_vision=5)
    with session(audio=False, model=model) as (instance, _, _):
        update(instance, 0, frame=True)
        wait_for(instance, lambda: instance.metrics["frames_processed"] == 1)
        advance(instance, 5)
        assert model.entered.wait(2)
        advance(instance, 10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(instance.request_answer, "what color paper?", timeout=3)
            second = pool.submit(instance.request_answer, "find paper", timeout=3)
            wait_for(instance, lambda: len(instance._questions) == 2)
            model.release.set()
            assert first.result(timeout=3)["through_s"] == 0
            assert second.result(timeout=3)["through_s"] == 0
        kinds = [kind for kind, _ in model.calls]
        assert kinds == ["vision", "vision", "answer", "vision", "answer"], model.calls


def test_runtime_timed_out_question_cannot_publish_later():
    model = Model(block_answer=True)
    with session(audio=False, model=model) as (instance, _, _):
        advance(instance, 5)
        wait_for(instance, lambda: instance.processed_through_s == 5)
        try:
            instance.request_answer("what color paper?", timeout=0.02)
            assert False
        except runtime.BusyError:
            pass
        assert model.entered.is_set()
        model.release.set()
        wait_for(instance, lambda: not instance._active_question)
        assert instance.answer is None


def test_runtime_export_contains_only_processed_frame_assets_and_preserves_source():
    with session(audio=False) as (instance, _, _):
        advance(instance, 5)
        wait_for(instance, lambda: instance.processed_through_s == 5)
        source = instance.video_path
        future = instance.cache_dir / "future.jpg"
        future.write_bytes(b"future pixels")
        instance.frames["future"] = {"id": "future", "t": 12, "url": "/frames/future",
                                     "path": future, "run_id": instance.run_id}
        destination = instance.cache_dir / "export"
        saved = instance.export(destination)
        assert saved.is_file() and source.exists()
        copied = list((destination / "frames").glob("*.jpg"))
        assert len(copied) == 2 and not (destination / "frames/future.jpg").exists()


def test_replay_constructor_discards_a_preindexed_bundle_instead_of_reusing_future_facts():
    from tubelive.store import StreamMemory
    from tubelive.schema import VideoMeta
    with tempfile.TemporaryDirectory() as folder:
        video = Path(folder) / "owned.mp4"
        video.write_bytes(b"test")
        loaded = StreamMemory(VideoMeta("old", "Preindexed", 15),
                              [SceneSegment(0, 0, caption="Cached first frame"),
                               SceneSegment(10, 10, caption="Cached future frame")])
        with patch.object(runtime, "probe", return_value={"duration_s": 15, "has_audio": False}):
            instance = runtime.ReplaySession(memory=loaded, video=video, cache_dir=Path(folder)/"cache", replay=True)
            try:
                assert not instance.memory.records and not instance.memory_dict()["records"]
                assert instance.processed_through_s == 0
            finally:
                instance.close()


def test_restart_preserves_saved_bundle_frames_but_clears_session_cache_frames():
    with session(audio=False) as (instance, _, _):
        advance(instance, 5)
        wait_for(instance, lambda: instance.processed_through_s == 5)
        generated = [frame['path'] for frame in instance.frames.values()]
        saved = instance.cache_dir.parent / 'saved-bundle-frame.jpg'
        saved.write_bytes(b'user-owned saved frame')
        instance.frames['saved'] = {'id': 'saved', 't': 0, 'url': '/frames/saved',
                                    'path': saved, 'run_id': instance.run_id}
        instance.control('restart')
        assert saved.read_bytes() == b'user-owned saved frame'
        assert all(not path.exists() for path in generated)


def test_cpu_speech_and_gpu_vision_run_concurrently_with_shared_cutoff():
    class BlockingSpeech(Speech):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()
        def transcribe_window(self, *args):
            self.entered.set()
            assert self.release.wait(3)
            return super().transcribe_window(*args)
    model, speech = Model(block_vision=0), BlockingSpeech()
    with session(model=model, speech=speech) as (instance, _, _):
        try:
            advance(instance, 10)
            assert model.entered.wait(1) and speech.entered.wait(1)
            assert {job['kind'] for job in instance.status()['active_jobs']} == {'audio','vision'}
            assert instance.processed_through_s == 0
            speech.release.set()
            wait_for(instance, lambda: instance.audio_through_s == 10)
            assert instance.processed_through_s == 0
            model.release.set()
            wait_for(instance, lambda: instance.processed_through_s == 10)
        finally:
            speech.release.set()


def test_minutes_of_backlog_are_bounded_and_drain_after_source_ends():
    model = Model(block_vision=0)
    with session(audio=False, duration=180, model=model) as (instance, _, _), patch.object(runtime, 'frame_at', return_value=JPEG) as sample:
        update(instance, 0, frame=True)
        assert model.entered.wait(1)
        advance(instance, 180, ended=True)
        assert not instance.gap and instance.playback_s == 180
        assert len(instance._ingest) <= runtime.MAX_PENDING_PER_LANE
        assert instance.processed_through_s == 0
        model.release.set()
        wait_for(instance, lambda: instance.processed_through_s == 180)
        assert [t for kind,t in model.calls if kind == 'vision'] == list(range(0,181,5))
        assert sample.called and all(call.args[1] <= 180 for call in sample.call_args_list)
        assert not instance.gap and instance.state == 'ended'


def test_retry_failed_processing_keeps_memory_and_does_not_skip_failed_sample():
    class FailsOnce(Model):
        failed = False
        def observe_frame(self, raw, t):
            if t == 5 and not self.failed:
                self.failed = True
                raise RuntimeError('temporary vision failure')
            return super().observe_frame(raw,t)
    with session(audio=False, duration=10, model=FailsOnce()) as (instance, _, _), patch.object(runtime, 'frame_at', return_value=JPEG):
        advance(instance,10,ended=True)
        wait_for(instance,lambda: instance.gap and not instance._active_job)
        run = instance.run_id
        saved = instance.memory.records[0].evidence_id
        assert instance.status()['retry_available'] and instance.vision_through_s == 0
        instance.control('retry_processing')
        wait_for(instance,lambda: instance.processed_through_s == 10)
        assert instance.run_id == run and not instance.gap
        assert [r.t_start for r in instance.memory.records] == [0,5,10]
        assert instance.memory.records[0].evidence_id == saved
        assert instance.playback_s == 10 and instance.source_command == 'pause'


def test_model_switch_is_rejected_during_active_vision_and_preserves_memory():
    class Selectable(Model):
        model = 'old:local'
        def select_model(self,name): self.model = name
    model = Selectable(block_vision=0)
    with session(audio=False,model=model) as (instance, _, _):
        update(instance,0,frame=True)
        assert model.entered.wait(1)
        try:
            instance.select_model('new:local')
            assert False
        except runtime.BusyError:
            pass
        model.release.set()
        wait_for(instance,lambda: not instance._active_job)
        instance.control('pause')
        run = instance.run_id
        records = len(instance.memory.records)
        assert instance.select_model('new:local')['selected'] == 'new:local'
        assert instance.run_id == run and len(instance.memory.records) == records
