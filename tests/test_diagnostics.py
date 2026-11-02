"""Logging must stay useful without retaining content or breaking processing."""
import json
import math
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tubelive import diagnostics


def test_diagnostics_only_logs_safe_structural_fields(tmp_path):
    log = diagnostics.DiagnosticLog(tmp_path)
    item = log.event("source.heartbeat", run_id="run-123", playback_s=83.7, paused=True,
                     state="paused", query="secret query", transcript="private speech",
                     token="secret-token", frame="base64-secret", path="C:/private/video.mov",
                     reason="private words", duration_s=math.inf, endpoint="/api/source")
    assert item["playback_s"] == 83.7 and item["paused"] is True
    assert item["endpoint"] == "/api/source"
    content = log.path.read_text(encoding="utf-8")
    assert all(secret not in content for secret in ["private", "secret", "base64", "Infinity"])
    assert json.loads(content) == item
    assert item["timestamp"].endswith("+00:00")
    assert item["session_elapsed_s"] >= 0


def test_diagnostics_rotation_is_bounded_and_records_remain_valid(tmp_path):
    log = diagnostics.DiagnosticLog(tmp_path)
    with patch.object(diagnostics, "MAX_LOG_BYTES", 750):
        for number in range(40):
            log.event("processing.progress", sequence=number, playback_s=number)
        files = list(log.path.parent.glob("diagnostics.jsonl*"))
        assert len(files) == 3
        assert all(path.stat().st_size <= 750 for path in files)
        records = [json.loads(line) for path in files for line in path.read_text().splitlines()]
        assert any(record["sequence"] == 39 for record in records)
    assert len(log.snapshot()["recent"]) == 40


def test_history_reads_rotations_in_order_and_ignores_damaged_lines(tmp_path):
    log = diagnostics.DiagnosticLog(tmp_path)
    with patch.object(diagnostics, "MAX_LOG_BYTES", 750):
        for number in range(40):
            log.event("processing.progress", sequence=number)
        history = log.history()
    numbers = [record["sequence"] for record in history]
    assert numbers == sorted(numbers) and numbers[-1] == 39
    assert numbers[0] > 0 and len(history) > 3
    with log.path.open("ab") as handle:
        handle.write(b'not-json\n["not-an-event"]\n{"event":"missing_metadata"}\n')
        handle.write(b'{"invalid":"\xff"}\n')
        injected = {**history[-1], "sequence": 40, "query": "private query", "token": "private token"}
        handle.write((json.dumps(injected) + "\n").encode())
        handle.write(b'{"truncated":')
    restored = diagnostics.DiagnosticLog(tmp_path).history()
    assert [record["sequence"] for record in restored] == numbers + [40]
    assert "private" not in json.dumps(restored)
    assert restored[0]["session_id"] == log.session_id


def test_history_contains_more_than_recent_window(tmp_path):
    log = diagnostics.DiagnosticLog(tmp_path)
    for number in range(300):
        log.event("source.heartbeat", sequence=number)
    assert len(log.snapshot()["recent"]) == 256
    assert [item["sequence"] for item in log.history()] == list(range(300))


def test_diagnostics_concurrent_writes_and_snapshot_are_independent(tmp_path):
    log = diagnostics.DiagnosticLog(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(lambda n: log.event("queue.sample", sequence=n), range(400)))
    records = [json.loads(line) for line in log.path.read_text().splitlines()]
    assert len(records) == 400
    assert len({record["sequence"] for record in records}) == 400
    snapshot = log.snapshot()
    assert len(snapshot["recent"]) == 256
    snapshot["recent"][0]["event"] = "modified"
    assert log.snapshot()["recent"][0]["event"] == "queue.sample"


def test_logging_failure_does_not_break_caller(tmp_path):
    log = diagnostics.DiagnosticLog(tmp_path)
    with patch.object(Path, "mkdir", side_effect=PermissionError("private filesystem detail")):
        log.event("source.pause", state="paused")
    snapshot = log.snapshot()
    assert snapshot["logging_error"] == "PermissionError"
    assert "private filesystem detail" not in json.dumps(snapshot)
    log.event("source.play", state="playing")
    assert log.snapshot()["logging_error"] is None


def test_hardware_snapshot_is_cached_and_unavailable_is_explicit():
    with patch.object(diagnostics, "_hardware_cached", None), patch.object(diagnostics, "_memory_snapshot", return_value={"status":"unavailable","total_mb":None}) as memory, patch.object(diagnostics, "_gpu_snapshot", return_value={"status":"unavailable","devices":[]}) as gpu:
        first = diagnostics.system_snapshot()
        second = diagnostics.system_snapshot()
        assert memory.call_count == gpu.call_count == 1
        assert first["memory"]["total_mb"] is None
        first["gpu"]["status"] = "modified"
        assert second["gpu"]["status"] == "unavailable"


def test_gpu_timeout_is_bounded_and_partial_metrics_are_preserved():
    with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 2)) as run:
        assert diagnostics._gpu_snapshot() == {"status":"unavailable","devices":[]}
        assert run.call_args.kwargs["timeout"] == 2
    with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "2, 16303, 2825, [N/A]\n")):
        result = diagnostics._gpu_snapshot()
        assert result["status"] == "available"
        assert result["devices"][0]["memory_used_mb"] == 2825
        assert result["devices"][0]["temperature_c"] is None


def test_clock_power_flags_and_cpu_fields_are_kept_without_content(tmp_path):
    with patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],0,'34, 16303, 7500, 67, 450, 3090, 9001, 35.5, [N/A], 0x0000000000000004\n')):
        gpu=diagnostics._gpu_snapshot()['devices'][0]
        assert gpu['clock_mhz']==450 and gpu['clock_reasons']==4
        assert gpu['power_w']==35.5 and gpu['power_limit_w'] is None
    log=diagnostics.DiagnosticLog(tmp_path)
    log.event('performance_sample',cpu_utilization_pct=92.5,ac_connected=False,gpu_clock_mhz=450,gpu_clock_reasons=4,sample_age_s=0.1,transcript='secret')
    row=log.history()[-1]
    assert row['cpu_utilization_pct']==92.5 and row['ac_connected'] is False and 'transcript' not in row
    log.close()
