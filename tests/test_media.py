"""Local speech/media timing and setup contracts without model downloads."""
import base64
import math
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tubelive import media


class SpeechModel:
    def __init__(self, segments):
        self.segments = segments
        self.options = None

    def transcribe(self, path, **options):
        self.options = options
        return iter(self.segments), None


def segment(start, end, text):
    return types.SimpleNamespace(start=start, end=end, text=text)


def test_media_transcript_offsets_and_whole_source_window_are_preserved():
    with tempfile.TemporaryDirectory() as folder, patch.object(media, "ffmpeg", return_value=b"") as ff:
        speech = media.LocalTranscriber(folder)
        speech._model = SpeechModel([segment(1, 3, " Next we add blue paper. "), segment(4, 5, " ")])
        records = speech.transcribe_window(Path("owned.mp4"), 10, 20)
        assert len(records) == 1
        assert records[0].t_start == 11 and records[0].t_end == 13
        assert records[0].completion_s == 20 and records[0].speaker == "unknown"
        assert "-t" in ff.call_args[0][0] and "10" in ff.call_args[0][0]
        assert speech._model.options["condition_on_previous_text"] is False
        assert speech._model.options["vad_filter"] is True
        assert speech._model.options["vad_parameters"]["threshold"] == 0.25
        assert "highpass=f=180,lowpass=f=3800" in ff.call_args[0][0]
        assert not list(Path(folder).glob("audio-*"))


def test_media_silence_returns_no_fabricated_records():
    with tempfile.TemporaryDirectory() as folder, patch.object(media, "ffmpeg", return_value=b""):
        speech = media.LocalTranscriber(folder)
        speech._model = SpeechModel([])
        assert speech.transcribe_window(Path("owned.mp4"), 0, 10) == []


def test_low_confidence_noise_is_not_added_to_transcript():
    with tempfile.TemporaryDirectory() as folder, patch.object(media, "ffmpeg", return_value=b""):
        speech=media.LocalTranscriber(folder)
        noise=segment(0,3,'Thank you for watching')
        noise.avg_logprob=-1.2;noise.no_speech_prob=0.8
        speech._model=SpeechModel([noise,segment(4,6,'Migratory birds')])
        assert [r.text for r in speech.transcribe_window(Path('owned.mp4'),0,10)] == ['Migratory birds']


def test_media_nonfinite_or_reversed_windows_are_rejected_before_processing():
    with tempfile.TemporaryDirectory() as folder, patch.object(media, "ffmpeg", return_value=b"") as ff:
        speech = media.LocalTranscriber(folder)
        speech._model = SpeechModel([])
        for start, end in [(0, math.inf), (math.nan, 10), (0, math.nan), (-1, 1), (10, 5), (0, 0)]:
            try:
                speech.transcribe_window(Path("owned.mp4"), start, end)
                assert False, (start, end)
            except ValueError:
                pass
        assert not ff.called


def test_media_out_of_window_and_invalid_model_segments_are_dropped():
    with tempfile.TemporaryDirectory() as folder, patch.object(media, "ffmpeg", return_value=b""):
        speech = media.LocalTranscriber(folder)
        speech._model = SpeechModel([segment(-4, -1, "before"), segment(15, 18, "after"),
            segment(7, 2, "backward"), segment(math.nan, 5, "nan"), segment(8, 12, "clipped")])
        records = speech.transcribe_window(Path("owned.mp4"), 10, 20)
        assert [r.text for r in records] == ["clipped"]
        assert records[0].t_start == 18 and records[0].t_end == 20
        assert all(10 <= r.t_start < r.t_end <= 20 for r in records)


def test_media_prepare_uses_cpu_int8_and_disallows_implicit_downloads():
    captured = []
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = lambda *args, **kwargs: captured.append((args, kwargs)) or SpeechModel([])
    with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {"faster_whisper": module}):
        speech = media.LocalTranscriber(folder)
        speech.prepare()
        speech.prepare()
        assert len(captured) == 1
        options = captured[0][1]
        assert options["device"] == "cpu" and options["compute_type"] == "int8"
        assert options["local_files_only"] is True


def test_media_frames_are_local_jpeg_bytes_and_failure_is_explicit():
    with patch.object(media, "ffmpeg", return_value=b"\xff\xd8owned\xff\xd9") as ff:
        image = media.frame_at(Path("owned.mp4"), 5)
        assert base64.b64decode(image).startswith(b"\xff\xd8")
        args = ff.call_args[0][0]
        start = float(args[args.index("-ss") + 1])
        span = float(args[args.index("-t") + 1])
        assert start <= 5 and start + span <= 5.001
    with patch.object(media, "ffmpeg", return_value=b""):
        try:
            media.frame_at(Path("owned.mp4"), 5)
            assert False
        except media.MediaError:
            pass


def test_media_subprocess_failure_is_wrapped_and_bounded():
    with patch.object(media.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
        try:
            media.ffmpeg([], timeout=1)
            assert False
        except media.MediaError as exc:
            assert "TimeoutExpired" in str(exc)


def test_real_frame_sampling_uses_past_pixels_and_handles_between_last_frame_and_eof():
    import io
    import shutil
    if not shutil.which("ffmpeg"):
        return
    from PIL import Image
    with tempfile.TemporaryDirectory() as folder:
        video = Path(folder) / "owned-boundary.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            "color=c=blue:size=96x64:rate=10:duration=0.5", "-f", "lavfi", "-i",
            "color=c=red:size=96x64:rate=10:duration=0.5", "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(video)], check=True)
        before = Image.open(io.BytesIO(base64.b64decode(media.frame_at(video, 0.45)))).convert("RGB")
        end = Image.open(io.BytesIO(base64.b64decode(media.frame_at(video, 0.999)))).convert("RGB")
        assert before.getpixel((10, 10))[2] > 200 and before.getpixel((10, 10))[0] < 30
        assert end.getpixel((10, 10))[0] > 200 and end.getpixel((10, 10))[2] < 30
