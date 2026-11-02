"""Local media providers. Nothing in this module uploads a recording."""
from __future__ import annotations

import base64
import math
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from .schema import TranscriptSegment


class MediaError(RuntimeError):
    pass


def ffmpeg(args: list[str], *, timeout: float = 60) -> bytes:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
            capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"Local media processing failed: {type(exc).__name__}") from exc
    if result.returncode:
        raise MediaError(result.stderr.decode(errors="replace")[-500:])
    return result.stdout


def frame_at(video: Path, seconds: float, *, width: int = 960) -> str:
    """Return the latest frame at/before a cutoff, including near video EOF.

    A normal accurate seek chooses the next frame; near EOF there may be none.
    Decode at most one preceding second and reverse that bounded window instead.
    The tiny endpoint tolerance includes a frame exactly at the requested time.
    """
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Frame time must be finite and non-negative.")
    start = max(0.0, seconds - 1)
    span = max(0.001, seconds - start + 0.000001)
    data = ffmpeg(["-ss", str(start), "-t", str(span), "-i", str(video),
                   "-an", "-frames:v", "1", "-vf", f"reverse,scale={int(width)}:-2,format=yuvj420p",
                   "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"])
    if not data:
        raise MediaError("The recording has no frame at this time.")
    return base64.b64encode(data).decode("ascii")


class LocalTranscriber:
    """Timestamped CPU transcription; model downloads are a setup action only."""
    def __init__(self, cache_dir: str | Path, *, model: str = "small", allow_download: bool = False,
                 device: str = "cpu", cpu_threads: int | None = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.model_name = model
        self.allow_download = allow_download
        self.device = device
        self.compute_type = 'int8_float16' if device == 'cuda' else 'int8'
        self.cpu_threads = cpu_threads or max(1, min(8, (os.cpu_count() or 4) // 2))
        self._model = None
        self.hotwords = ''
        self._lock = threading.Lock()

    def prepare(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(
                    self.model_name, device=self.device, compute_type=self.compute_type,
                    download_root=str(self.cache_dir / "models"),
                    local_files_only=not self.allow_download,
                    cpu_threads=self.cpu_threads,
                )
            except Exception as exc:
                raise MediaError(
                    "Local speech model is unavailable. Run 'recall prepare' once during setup. "
                    f"({type(exc).__name__})"
                ) from exc

    def transcribe_window(self, video: Path, start: float, end: float) -> list[TranscriptSegment]:
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
            raise ValueError("Audio windows must have increasing finite bounds.")
        self.prepare()
        with self._lock, tempfile.TemporaryDirectory(dir=self.cache_dir, prefix="audio-") as folder:
            audio = Path(folder) / "window.wav"
            ffmpeg(["-y", "-ss", str(start), "-i", str(video), "-t", str(end - start),
                    "-vn", "-af", "highpass=f=180,lowpass=f=3800", "-ac", "1", "-ar", "16000", str(audio)])
            segments, _ = self._model.transcribe(
                str(audio), beam_size=3, vad_filter=True, condition_on_previous_text=False,
                vad_parameters={"threshold": 0.25, "min_speech_duration_ms": 250},
                word_timestamps=True, hallucination_silence_threshold=1.5,
                **({'hotwords':self.hotwords} if self.hotwords else {}),
            )
            records = []
            for segment in segments:
                if getattr(segment, "avg_logprob", 0) < -1.0 or getattr(segment, "no_speech_prob", 0) > 0.65:
                    continue
                a, b = float(segment.start), float(segment.end)
                if not math.isfinite(a) or not math.isfinite(b):
                    continue
                a, b = max(start, min(end, start+a)), max(start, min(end, start+b))
                if b <= a or not segment.text.strip():
                    continue
                records.append(TranscriptSegment(
                    t_start=a, t_end=b, text=segment.text.strip(), speaker="unknown",
                    payload={"provider": f"faster-whisper-{self.model_name}-{self.device}-{self.compute_type}", "window_end": end,
                             "audio_preprocessing": "speech-band-filter", "automated_transcript": True}))
            return records
