"""Generate clearly labelled synthetic test footage, not a real-stream evaluation.

Requires Pillow, FFmpeg and Windows' built-in offline speech voice. Outputs are
owned test graphics plus synthesized speech; no external media is downloaded.
"""
from pathlib import Path
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont

out = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/local-smoke").resolve()
out.mkdir(parents=True, exist_ok=True)
font_path = Path("C:/Windows/Fonts/segoeui.ttf")
font = ImageFont.truetype(str(font_path), 42)
small = ImageFont.truetype(str(font_path), 23)
stages = [
    ("#233350", "PREVIEW OFF", "ONE COLUMN", "This is a synthetic local test. The first preview is off."),
    ("#664431", "PREVIEW ON", "ONE COLUMN", "I enabled the preview because the first attempt was blank."),
    ("#233350", "PREVIEW WORKS", "TWO COLUMNS", "The preview now works. Look at the screen for a visual change."),
    ("#403451", "PREVIEW WORKS", "CAPTIONS NEXT", "Next I will add captions to this design."),
]
for number, (background, headline, detail, speech) in enumerate(stages):
    image = Image.new("RGB", (960, 540), background)
    draw = ImageDraw.Draw(image)
    draw.text((35, 28), "RECALL / SYNTHETIC TEST FOOTAGE", font=small, fill="#b5bac8")
    draw.text((35, 140), headline, font=font, fill="white")
    draw.text((35, 210), detail, font=font, fill="#b3f3d0")
    draw.text((35, 466), f"STAGE {number+1} / local AI smoke test / not a real stream", font=small, fill="white")
    if number >= 2:
        draw.ellipse((740, 150, 890, 300), fill="#4edb85")
    image.save(out / f"stage-{number}.png")
    ps = "Add-Type -AssemblyName System.Speech\n$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
    ps += "$s.SetOutputToWaveFile('" + str(out / f"speech-{number}.wav").replace("'", "''") + "')\n"
    ps += "$s.Speak('" + speech.replace("'", "''") + "')\n$s.Dispose()\n"
    script = out / f"speech-{number}.ps1"
    script.write_text(ps, encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "10",
                    "-i", str(out / f"stage-{number}.png"), "-i", str(out / f"speech-{number}.wav"),
                    "-t", "8", "-af", "apad", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(out / f"part-{number}.mp4")], check=True)
(out / "concat.txt").write_text("\n".join(f"file 'part-{i}.mp4'" for i in range(4)))
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i",
                str(out / "concat.txt"), "-c", "copy", "-movflags", "+faststart", str(out / "synthetic-demo.mp4")], check=True)
(out / "README.txt").write_text("Synthetic graphics and offline synthesized narration for provider testing only.\n"
    "This clip is not one of the two real recordings required by the acceptance plan.\n"
    "Visual-only test: a green circle first appears in stage 3 (16 seconds).\n"
    "Spoken-plan test: captions are the stated next step in stage 4 (24 seconds).\n")
print(out / "synthetic-demo.mp4")
