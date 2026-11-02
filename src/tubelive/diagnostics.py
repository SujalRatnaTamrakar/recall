"""Bounded local diagnostics. Content, credentials and request bodies are never logged.

``DiagnosticLog(cache_dir).event('source.heartbeat', run_id=..., playback_s=...)``
accepts only the structural fields listed below. Unknown fields are discarded.
``snapshot()`` returns a copy of recent events and local log-file metadata;
``history()`` reads the bounded files oldest first and ignores damaged records.
Neither samples hardware. ``system_snapshot()`` separately returns cached,
best-effort local hardware measurements, with explicit unavailable states.
"""
from __future__ import annotations

import copy
import ctypes
import json
import math
import os
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_LOG_FILES = 3
_NUMBER_FIELDS = frozenset("""
playback_s processed_through_s audio_through_s vision_through_s lag_s source_age_s
queue_depth questions_waiting frames_processed audio_windows_processed
exact_frames_reused last_answer_s last_vision_s last_audio_s duration_s elapsed_s
wait_s inference_s queue_wait_s start_s end_s window_start_s window_end_s
time_s through_s cutoff_s frame_s frame_t t frame_count audio_count record_count
transcript_count candidate_count evidence_count question_length question_chars citation_count input_bytes
output_bytes bytes byte_count content_length http_status status_code port
attempt sequence revision source_revision ready_state network_state
buffered_s buffered_end_s current_time_s playback_rate dropped_frames total_frames
video_width video_height image_width image_height request_count pending_count
load_duration_s prompt_eval_count eval_count prompt_eval_duration_s eval_duration_s
tokens_per_second cpu_percent process_memory_mb ram_total_mb ram_available_mb
timeout_s position_s active_age_s window_s prompt_tokens output_tokens load_s prompt_s decode_s job_age_s error_code
gpu_utilization_pct gpu_memory_used_mb gpu_memory_total_mb gpu_temperature_c
gpu_clock_mhz gpu_max_clock_mhz gpu_memory_clock_mhz gpu_power_w gpu_power_limit_w gpu_clock_reasons
cpu_utilization_pct sample_age_s dropped_jobs coverage_gap_count vision_queue_depth audio_queue_depth
profile_version story_through_s context_characters selected_records accepted_claims
""".split())
_BOOL_FIELDS = frozenset("""
paused ended seeking muted hidden visible source_connected controls_enabled
has_video has_audio active_question cached reused success ok cancelled timed_out
model_available speech_available playback_stalled browser_online reused_exact_frame remote playing source_engaged source_busy captured
ac_connected
""".split())
_ID_FIELDS = frozenset({"run_id", "source_id", "request_id", "job_id"})
_LABEL_FIELDS = frozenset("""
operation state previous_state reason error_type action visibility model provider outcome
phase kind endpoint mode origin trigger stage command event_type media_error page_role
""".split())
_LABEL = re.compile(r"[A-Za-z0-9_.:/-]{1,80}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_EVENT = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")


def _safe_fields(fields):
    result = {}
    for key, value in fields.items():
        if key in _BOOL_FIELDS and isinstance(value, bool):
            result[key] = value
        elif key in _NUMBER_FIELDS and isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                if math.isfinite(value):
                    result[key] = value
            except (OverflowError, TypeError):
                pass
        elif key in _ID_FIELDS and isinstance(value, str) and _ID.fullmatch(value):
            result[key] = value
        elif key in _LABEL_FIELDS and isinstance(value, str) and _LABEL.fullmatch(value):
            # No absolute paths, URLs, query strings, spaces or arbitrary prose.
            if "/" not in value and "\\" not in value:
                result[key] = value
            elif key == "endpoint" and re.fullmatch(r"/api/[a-z_-]{1,30}|/video|/broadcast", value):
                result[key] = value
    return result


class DiagnosticLog:
    """Append-and-flush JSONL; at most three 2 MiB files plus 256 recent events.

    Callers may share an instance across threads. Logging failures are exposed
    as exception *types* in ``snapshot()['logging_error']`` and never interrupt
    playback or processing. No automatic network upload occurs.
    """
    def __init__(self, cache_dir: str | Path):
        self.path = Path(cache_dir).resolve() / "logs" / "diagnostics.jsonl"
        self.session_id = uuid.uuid4().hex
        self._started = time.monotonic()
        self._lock = threading.RLock()
        self._recent = deque(maxlen=256)
        self._logging_error = None

    def event(self, name: str, **fields) -> dict:
        with self._lock:
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "session_elapsed_s": round(time.monotonic() - self._started, 3),
                "session_id": self.session_id,
                "event": name if isinstance(name, str) and _EVENT.fullmatch(name) else "invalid_event",
                **_safe_fields(fields),
            }
            self._recent.append(record)
            raw = (json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size + len(raw) > MAX_LOG_BYTES:
                    oldest = self.path.with_name(self.path.name + f".{MAX_LOG_FILES - 1}")
                    oldest.unlink(missing_ok=True)
                    for index in range(MAX_LOG_FILES - 2, 0, -1):
                        source = self.path.with_name(self.path.name + f".{index}")
                        if source.exists():
                            source.replace(self.path.with_name(self.path.name + f".{index + 1}"))
                    self.path.replace(self.path.with_name(self.path.name + ".1"))
                with self.path.open("ab") as handle:
                    handle.write(raw)
                    handle.flush()
                self._logging_error = None
            except OSError as exc:
                self._logging_error = type(exc).__name__
            return copy.deepcopy(record)

    def snapshot(self) -> dict:
        with self._lock:
            files = []
            for index in range(MAX_LOG_FILES):
                path = self.path if index == 0 else self.path.with_name(self.path.name + f".{index}")
                try:
                    if path.is_file():
                        files.append({"name": path.name, "bytes": path.stat().st_size})
                except OSError:
                    continue
            return {"session_id": self.session_id, "path": str(self.path),
                    "max_files": MAX_LOG_FILES, "max_file_bytes": MAX_LOG_BYTES,
                    "files": files, "recent": copy.deepcopy(list(self._recent)),
                    "logging_error": self._logging_error}

    def history(self) -> list[dict]:
        """Return retained events oldest first, reapplying the safe field schema.

        Only the three fixed log files are opened and at most 2 MiB is read per
        file. Oversized files contribute their tail; malformed/truncated lines
        are skipped. This includes earlier process sessions still on disk.
        """
        records = []
        with self._lock:
            for index in range(MAX_LOG_FILES - 1, -1, -1):
                path = self.path if index == 0 else self.path.with_name(self.path.name + f".{index}")
                try:
                    with path.open("rb") as handle:
                        size = handle.seek(0, os.SEEK_END)
                        handle.seek(max(0, size - MAX_LOG_BYTES))
                        lines = handle.read(MAX_LOG_BYTES).splitlines()
                except OSError:
                    continue
                for raw in lines:
                    try:
                        item = json.loads(raw)
                        if not isinstance(item, dict):
                            continue
                        timestamp, session, event = item.get("timestamp"), item.get("session_id"), item.get("event")
                        elapsed = item.get("session_elapsed_s")
                        if not isinstance(timestamp, str) or len(timestamp) > 40:
                            continue
                        if datetime.fromisoformat(timestamp).utcoffset() != timezone.utc.utcoffset(None):
                            continue
                        if not isinstance(session, str) or not re.fullmatch(r"[0-9a-f]{32}", session):
                            continue
                        if not isinstance(event, str) or not _EVENT.fullmatch(event):
                            continue
                        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
                            continue
                        records.append({"timestamp": timestamp, "session_elapsed_s": elapsed,
                                        "session_id": session, "event": event, **_safe_fields(item)})
                    except (ValueError, TypeError, OverflowError, RecursionError):
                        continue
        return records

    def close(self):
        """Compatibility hook: each event is already flushed and closed."""


_hardware_lock = threading.Lock()
_hardware_cached = None
_hardware_cached_at = float("-inf")
_cpu_previous = None


def _cpu_power_snapshot() -> dict:
    """Windows counters, without blocking for a sampling interval."""
    global _cpu_previous
    result = {'utilization_pct': None, 'ac_connected': None}
    if os.name != 'nt':
        return result
    try:
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        idle, system, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(system), ctypes.byref(user)):
            ticks = lambda value: (value.dwHighDateTime << 32) | value.dwLowDateTime
            current = (ticks(idle), ticks(system) + ticks(user))
            if _cpu_previous and current[1] > _cpu_previous[1]:
                result['utilization_pct'] = round(max(0,min(100,100*(1-(current[0]-_cpu_previous[0])/(current[1]-_cpu_previous[1])))),1)
            _cpu_previous = current
        class Power(ctypes.Structure):
            _fields_ = [('ac',wintypes.BYTE),('flags',wintypes.BYTE),('percent',wintypes.BYTE),('reserved',wintypes.BYTE),('life',wintypes.DWORD),('full',wintypes.DWORD)]
        power = Power()
        if kernel.GetSystemPowerStatus(ctypes.byref(power)) and power.ac in {0,1}:
            result['ac_connected'] = bool(power.ac)
    except (OSError,AttributeError):
        pass
    return result


def _memory_snapshot() -> dict:
    result = {"status": "unavailable", "total_mb": None, "available_mb": None,
              "process_mb": None}
    if os.name != "nt":
        return result
    try:
        from ctypes import wintypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD),
                        ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong), ("available_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                        ("available_extended_virtual", ctypes.c_ulonglong)]

        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
        kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
        if kernel.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result.update(status="available", total_mb=round(memory.total_physical / 2**20, 1),
                          available_mb=round(memory.available_physical / 2**20, 1))

        class ProcessMemory(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("page_fault_count", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("peak_working_set", "working_set", "quota_peak_paged",
                "quota_paged", "quota_peak_nonpaged", "quota_nonpaged", "pagefile", "peak_pagefile")]

        process = ProcessMemory()
        process.cb = ctypes.sizeof(process)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(process), process.cb):
            result["process_mb"] = round(process.working_set / 2**20, 1)
    except (OSError, AttributeError):
        pass
    return result


def _gpu_snapshot() -> dict:
    result = {"status": "unavailable", "devices": []}
    try:
        command = ["nvidia-smi", "--query-gpu=utilization.gpu,memory.total,memory.used,temperature.gpu,clocks.sm,clocks.max.sm,clocks.mem,power.draw,power.limit,clocks_event_reasons.active",
                   "--format=csv,noheader,nounits"]
        process = subprocess.run(command, capture_output=True, text=True, timeout=2,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if process.returncode != 0:
            return result
        for line in process.stdout.splitlines()[:8]:
            parts = line.split(",")
            if len(parts) not in {4,10}:
                continue
            values = []
            for part in parts:
                try:
                    number = int(part.strip(),16) if part.strip().lower().startswith('0x') else float(part.strip())
                    values.append(number if math.isfinite(number) and number >= 0 else None)
                except ValueError:
                    values.append(None)
            if any(value is not None for value in values):
                result["devices"].append(dict(zip(
                    ("utilization_pct", "memory_total_mb", "memory_used_mb", "temperature_c", "clock_mhz", "max_clock_mhz", "memory_clock_mhz", "power_w", "power_limit_w", "clock_reasons"), values)))
        if result["devices"]:
            result["status"] = "available"
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def system_snapshot() -> dict:
    """Best-effort measurements cached for two seconds; GPU sampling takes <=2s.

    ``memory.process_mb`` describes this Python process, not Ollama. GPU memory
    describes all programs on that GPU. Unsupported metrics are ``None``.
    """
    global _hardware_cached, _hardware_cached_at
    with _hardware_lock:
        now = time.monotonic()
        if _hardware_cached is None or now - _hardware_cached_at >= 2:
            _hardware_cached = {"sampled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "memory": _memory_snapshot(), "gpu": _gpu_snapshot(), 'cpu_power': _cpu_power_snapshot()}
            _hardware_cached_at = time.monotonic()
        result = copy.deepcopy(_hardware_cached)
        result["sample_age_s"] = round(time.monotonic() - _hardware_cached_at, 1)
        return result
