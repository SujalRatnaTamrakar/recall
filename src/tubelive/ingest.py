"""Ingest a recorded video into a Recall bundle directory (v0).

Uses ffprobe/ffmpeg as CLIs (no Python media libraries):

    out/
      meta.json     probe data + provider notices
      memory.json   StreamMemory bundle (transcript filled from --srt if given)
      audio.wav     extracted audio, if the source has an audio stream
      frames/       thumbnails every 10 s (frame_XXXX.jpg)

Transcript providers:
* ``--srt file.srt`` — parsed into TranscriptSegments.  This path WORKS.
* faster-whisper — attempted via import; if the package is unavailable a
  clear notice is written to meta.json and ingest still succeeds.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .schema import TranscriptSegment, VideoMeta
from .store import StreamMemory


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(video: Path) -> dict:
    """ffprobe -> normalised meta dict."""
    proc = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                 "-of", "json", str(video)])
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {video}: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    vstream = next((s for s in data.get("streams", [])
                    if s.get("codec_type") == "video"), {})
    astream = next((s for s in data.get("streams", [])
                    if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    num, _, den = str(vstream.get("avg_frame_rate", "30/1")).partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return {
        "duration_s": float(fmt.get("duration", vstream.get("duration", 0)) or 0),
        "width": int(vstream.get("width", 0) or 0),
        "height": int(vstream.get("height", 0) or 0),
        "fps": round(fps, 3),
        "has_audio": astream is not None,
        "video_codec": vstream.get("codec_name", ""),
        "audio_codec": (astream or {}).get("codec_name", ""),
    }


_SRT_TIME = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _srt_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(path: str | Path) -> list[TranscriptSegment]:
    """Parse an .srt file into TranscriptSegments (speaker = 'unknown',
    unless a line starts with 'NAME:' — then that name is the speaker)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    segments: list[TranscriptSegment] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        tm = None
        body: list[str] = []
        for ln in lines:
            m = _SRT_TIME.search(ln)
            if m:
                tm = m
            elif tm is not None:
                body.append(ln)
            # lines before the timestamp (the numeric index) are skipped
        if tm is None or not body:
            continue
        spoken = " ".join(body)
        speaker = "unknown"
        mspk = re.match(r"^([A-Z][A-Za-z .']{1,30}):\s*(.+)$", spoken)
        if mspk:
            speaker, spoken = mspk.group(1).strip(), mspk.group(2).strip()
        segments.append(TranscriptSegment(
            t_start=_srt_seconds(*tm.groups()[:4]),
            t_end=_srt_seconds(*tm.groups()[4:]),
            speaker=speaker, text=spoken, confidence=0.95,
            payload={"provider": "srt"}))
    return segments


def try_faster_whisper(audio: Path) -> tuple[list[TranscriptSegment] | None, str]:
    """Attempt a faster-whisper transcription.  Returns (segments, notice).

    Never raises: any failure becomes a notice for meta.json.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:  # ImportError or broken install
        return None, (f"faster-whisper unavailable ({type(exc).__name__}); "
                      f"transcript left empty — supply --srt instead.")
    try:  # pragma: no cover - only runs where the package exists
        model = WhisperModel("tiny")
        segs, _info = model.transcribe(str(audio))
        out = [TranscriptSegment(t_start=float(s.start), t_end=float(s.end),
                                 speaker="unknown", text=s.text.strip(),
                                 confidence=0.8,
                                 payload={"provider": "faster-whisper"})
               for s in segs]
        return out, "faster-whisper: transcribed with model 'tiny'."
    except Exception as exc:  # pragma: no cover
        return None, f"faster-whisper failed ({exc}); transcript left empty."


def ingest(video: str | Path, out_dir: str | Path,
           srt: str | Path | None = None, title: str | None = None,
           thumb_every_s: int = 10) -> dict:
    """Run the full v0 ingest.  Returns the meta dict (also written to disk)."""
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    out = Path(out_dir)
    (out / "frames").mkdir(parents=True, exist_ok=True)

    info = probe(video)
    notices: list[str] = []

    # Audio (only if the source actually has an audio stream).
    audio_path: Path | None = None
    if info["has_audio"]:
        audio_path = out / "audio.wav"
        proc = _run(["ffmpeg", "-y", "-v", "error", "-i", str(video),
                     "-vn", "-ac", "1", "-ar", "16000", str(audio_path)])
        if proc.returncode != 0:
            notices.append(f"audio extraction failed: {proc.stderr.strip()[:200]}")
            audio_path = None
    else:
        notices.append("source has no audio stream; audio.wav not extracted.")

    # Thumbnails every N seconds.
    proc = _run(["ffmpeg", "-y", "-v", "error", "-i", str(video),
                 "-vf", f"fps=1/{thumb_every_s}", "-q:v", "4",
                 str(out / "frames" / "frame_%04d.jpg")])
    if proc.returncode != 0:
        notices.append(f"thumbnail extraction failed: {proc.stderr.strip()[:200]}")
    n_frames = len(list((out / "frames").glob("frame_*.jpg")))

    # Transcript.
    records: list = []
    provider = "none"
    if srt is not None:
        records = parse_srt(srt)
        provider = "srt"
        notices.append(f"srt import: {len(records)} transcript segments "
                       f"from {Path(srt).name}.")
    elif audio_path is not None:
        segs, notice = try_faster_whisper(audio_path)
        notices.append(notice)
        if segs:
            records, provider = segs, "faster-whisper"
    if not records and provider == "none":
        notices.append("transcript empty: no --srt supplied and no working "
                       "transcription provider available.")

    video_id = re.sub(r"[^a-z0-9_-]+", "-", video.stem.lower()).strip("-")
    meta = {
        "id": video_id,
        "title": title or video.stem,
        "source": str(video),
        "status": "ready",
        "duration_s": info["duration_s"], "fps": info["fps"],
        "width": info["width"], "height": info["height"],
        "has_audio": info["has_audio"],
        "thumbnails": n_frames, "thumb_every_s": thumb_every_s,
        "transcript_provider": provider,
        "notices": notices,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    memory = StreamMemory(
        video=VideoMeta(id=video_id, title=meta["title"],
                        duration_s=info["duration_s"], fps=info["fps"],
                        width=info["width"], height=info["height"],
                        source=str(video)),
        records=records)
    memory.save(out / "memory.json")
    return meta
