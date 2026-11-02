"""Ingest tests: build a tiny video with ffmpeg, import an SRT, verify the
bundle.  Self-contained (does not depend on any external fixture video).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tubelive.ingest import ingest, parse_srt  # noqa: E402
from tubelive.store import StreamMemory  # noqa: E402

SRT = """1
00:00:01,000 --> 00:00:04,000
Jean Paul: We hit the Fleeca today, clean and fast.

2
00:00:05,000 --> 00:00:09,000
After this we hit Paleto, I swear.
"""


def test_parse_srt():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "subs.srt"
        p.write_text(SRT, encoding="utf-8")
        segs = parse_srt(p)
    assert len(segs) == 2, segs
    assert segs[0].speaker == "Jean Paul"          # NAME: prefix -> speaker
    assert segs[0].t_start == 1.0 and segs[0].t_end == 4.0
    assert segs[1].speaker == "unknown"


def test_ingest_tiny_video_with_srt():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("SKIP: ffmpeg/ffprobe not available")
        return
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        video = td / "tiny.mp4"
        # 12 s test video WITH an audio track (sine tone).
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=duration=12:size=320x240:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
             "-shortest", str(video)], check=True)
        srt = td / "subs.srt"
        srt.write_text(SRT, encoding="utf-8")
        meta = ingest(video, td / "out", srt=srt)
        assert abs(meta["duration_s"] - 12) < 1.5, meta
        assert meta["transcript_provider"] == "srt", meta
        assert (td / "out" / "audio.wav").exists(), meta   # has audio stream
        assert meta["thumbnails"] >= 1, meta
        mem = StreamMemory.load(td / "out" / "memory.json")
        transcripts = [r for r in mem.records if r.kind == "transcript"]
        assert len(transcripts) == 2, transcripts
        # The ingested bundle is immediately queryable, incl. STATED plans.
        from tubelive import ask
        ans = ask(mem, "where are they going next?")
        assert ans.source_label == "STATED" and "Paleto" in ans.summary
