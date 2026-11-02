"""Command line entry points for local setup, import and Twitch replay."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from .ask import ask, answer_with_model
from .schema import mmss
from .store import StreamMemory

ROOT = Path(__file__).resolve().parents[2]


def parse_time(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text.endswith("m"):
        return float(text[:-1])*60
    if ":" in text:
        minutes, seconds = text.split(":", 1)
        return float(minutes)*60 + float(seconds)
    return float(text)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="recall", description="Recall — local stream memory")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("serve", help="open the studio and optional Twitch replay")
    run.add_argument("--bundle", type=Path)
    run.add_argument("--video", type=Path)
    run.add_argument("--replay", action="store_true")
    run.add_argument("--local-ai", action="store_true")
    run.add_argument("--twitch", action="store_true")
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    run.add_argument("--cache", type=Path, default=ROOT / ".tubelive")
    query = commands.add_parser("ask", help="ask an imported memory bundle")
    query.add_argument("--bundle", required=True, type=Path)
    query.add_argument("--q", required=True)
    query.add_argument("--now")
    query.add_argument("--json", action="store_true")
    query.add_argument("--local-ai", action="store_true")
    ingest = commands.add_parser("ingest", help="automatically import a recording with local models")
    ingest.add_argument("--video", required=True, type=Path)
    ingest.add_argument("--out", required=True, type=Path)
    ingest.add_argument("--srt", type=Path)
    ingest.add_argument("--title")
    ingest.add_argument("--transcript-only", action="store_true")
    prepare = commands.add_parser("prepare", help="download the speech model once during setup")
    prepare.add_argument("--cache", type=Path, default=ROOT / ".tubelive")
    prepare.add_argument("--speech-model", choices=['tiny','tiny.en','base','base.en','small','small.en'], default='small')
    semantic = commands.add_parser('prepare-semantic', help='download the small CPU search model once; no footage is sent')
    semantic.add_argument('--cache',type=Path,default=ROOT / '.tubelive')
    doctor = commands.add_parser("doctor", help="check installed local dependencies")
    doctor.add_argument("--cache", type=Path, default=ROOT / ".tubelive")
    args = parser.parse_args(argv)
    service = None
    try:
        if args.command == 'prepare-semantic':
            from .semantic import prepare_semantic, LocalSemanticIndex
            prepare_semantic(args.cache)
            index = LocalSemanticIndex(args.cache)
            index._encode(['Verify local semantic search.'])
            print('Local semantic search is ready. Future searches work offline.')
            return 0
        if args.command in {"prepare", "doctor"}:
            from .localai import LocalAI
            from .media import LocalTranscriber
            if args.command == "prepare":
                print("Preparing the local speech model. This setup download does not send any footage.", flush=True)
                LocalTranscriber(args.cache, model=args.speech_model, allow_download=True).prepare()
            result = {"python": sys.version.split()[0], "ffmpeg": bool(shutil.which("ffmpeg")),
                      "ffprobe": bool(shutil.which("ffprobe")), "ollama": bool(shutil.which("ollama")),
                      "node": bool(shutil.which("node")), "local_model": LocalAI().health(),
                      "speech_cache": str(args.cache / "models")}
            print(json.dumps(result, indent=2))
            return 0 if result["ffmpeg"] and result["local_model"].get("ok") else 1
        if args.command == "ask":
            memory = StreamMemory.load(args.bundle)
            if args.local_ai:
                from .localai import LocalAI
                from .localservice import OllamaService
                service = OllamaService(ROOT / ".tubelive")
                model = LocalAI(service.start())
                answer = answer_with_model(memory, args.q, model, now=parse_time(args.now))
            else:
                answer = ask(memory, args.q, now=parse_time(args.now))
            if args.json:
                print(json.dumps(answer.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(answer.summary)
                for citation in answer.citations:
                    print(f"  [{mmss(citation.t)}] {citation.label}")
            return 0
        if args.command == "ingest":
            if args.transcript_only or args.srt:
                from .ingest import ingest
                result = ingest(args.video, args.out, srt=args.srt, title=args.title)
            else:
                from .localai import LocalAI
                from .localservice import OllamaService
                from .media import LocalTranscriber
                from .runtime import ingest_recording
                service = OllamaService(ROOT / ".tubelive")
                model = LocalAI(service.start())
                result = ingest_recording(args.video.resolve(), args.out.resolve(), model,
                                          transcriber=LocalTranscriber(ROOT / ".tubelive"))
            print(json.dumps(result, indent=2))
            return 0
        from .runtime import ReplaySession
        from .server import create_server
        from .localai import LocalAI
        from .localservice import OllamaService
        model = None
        if args.local_ai:
            service = OllamaService(args.cache)
            model = LocalAI(service.start())
            if not model.health().get("ok"):
                raise RuntimeError("The local qwen3.5:9b model is missing. Add it with Ollama during setup.")
        bundle = args.bundle
        if not bundle and not args.video:
            synthetic = ROOT / "examples" / "local-smoke" / "memory.json"
            if synthetic.is_file():
                bundle = synthetic
                args.video = synthetic.parent / "synthetic-demo.mp4"
            else:
                bundle = ROOT / "fixtures" / "gta_rp_demo" / "memory.json"
        app = ReplaySession(memory=StreamMemory.load(bundle) if bundle else None,
                            video=args.video, cache_dir=args.cache, model=model,
                            replay=args.replay or (args.video is not None and bundle is None))
        server = create_server(bundle, args.video, args.host, args.port, session=app)
        if args.twitch:
            from .twitch import TwitchBot
            client_id = os.environ.get("TWITCH_CLIENT_ID", "")
            token = os.environ.get("TWITCH_ACCESS_TOKEN", "")
            if not client_id or not token:
                server.server_close()
                raise RuntimeError("Set TWITCH_CLIENT_ID and TWITCH_ACCESS_TOKEN locally; see docs/twitch-setup.md.")
            app.bot = TwitchBot(client_id=client_id, access_token=token,
                on_ask=lambda question, cutoff: app.request_answer(question, cutoff),
                on_control=lambda action: app.control(action, remote=True), get_status=app.status)
            app.bot.start()
        print(f"Recall studio: http://127.0.0.1:{server.server_address[1]}/", flush=True)
        print("OBS browser source: /broadcast · local AI only · Ctrl+C to close", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except Exception as exc:
        print(f"Recall: {exc}", file=sys.stderr)
        return 1
    finally:
        if service:
            service.stop()


if __name__ == "__main__":
    raise SystemExit(main())
