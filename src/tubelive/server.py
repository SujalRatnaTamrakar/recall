"""Loopback-only application server; source, evidence and controls share an origin."""
from __future__ import annotations
import hmac
import json
import mimetypes
import re
import sys
import shutil
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from .runtime import BusyError, ReplaySession, finite_time
from .media import ffmpeg
from .store import StreamMemory
from .moments import Moments
from .reviews import Reviews

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_UPLOAD_BYTES = 8 * 1024 ** 3
UPLOAD_CHUNK_BYTES = 1024 ** 2


def _copy_upload(source, destination, length):
    """Copy the request in bounded chunks, including for multi-gigabyte files."""
    remaining = length
    while remaining:
        chunk = source.read(min(UPLOAD_CHUNK_BYTES, remaining))
        if not chunk:
            raise ValueError("The file transfer was interrupted.")
        destination.write(chunk)
        remaining -= len(chunk)


def create_server(bundle=None, video=None, host="127.0.0.1", port=8765, *, session=None, viewer_dir=None):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Recall binds only to the local computer.")
    video_path = Path(video).resolve() if video else None
    if video_path and not video_path.is_file():
        raise FileNotFoundError("The recording does not exist.")
    app = session or ReplaySession(memory=StreamMemory.load(bundle) if bundle else None,
        video=video_path, cache_dir=Path(tempfile.gettempdir()) / "tube-live" / uuid.uuid4().hex)
    if bundle:
        for record in app.memory.records:
            fid = str(record.payload.get("frame_id", ""))
            if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", fid):
                path = Path(bundle).resolve().parent / "frames" / f"{fid}.jpg"
                if path.is_file():
                    app.frames[fid] = {"id": fid, "t": record.t_start, "url": f"/frames/{fid}",
                                       "path": path, "run_id": app.run_id}
    built_ui = Path(viewer_dir or PROJECT_ROOT / "viewer" / "dist").resolve()
    clip_slot = threading.BoundedSemaphore(1)
    moments = Moments(app)
    reviews = Reviews(app)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass
        def send_response(self, code, message=None):
            path = urlsplit(self.path).path
            if code >= 400 or self.command == "POST" and path != "/api/diagnostics/event":
                # No request body, query string, auth headers or source filename.
                app.diagnostics.event("http_response", run_id=app.run_id, endpoint=path,
                    operation=self.command, http_status=code)
            super().send_response(code, message)
        def trusted_host(self):
            port = self.server.server_address[1]
            return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}
        def send(self, code, body, ctype, extra=None):
            self.send_response(code)
            for key, value in {"Content-Type": ctype, "Content-Length": str(len(body)),
                "X-Content-Type-Options": "nosniff", "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer", "Cache-Control": "no-store", **(extra or {})}.items():
                self.send_header(key, value)
            if self.close_connection:
                self.send_header("Connection", "close")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; "
                    "connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
        def json(self, code, value):
            self.send(code, json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
                      "application/json; charset=utf-8")
        def authorized(self):
            port = self.server.server_address[1]
            origin = self.headers.get("Origin")
            valid = self.trusted_host() and (not origin or origin in {
                f"http://127.0.0.1:{port}", f"http://localhost:{port}"})
            valid = valid and hmac.compare_digest(self.headers.get("X-Tube-Token", ""), app.csrf_token)
            if not valid:
                self.json(403, {"error": "Refresh the local Recall page before making changes."})
                self.close_connection = True
            return valid
        def do_HEAD(self):
            self.do_GET()
        def do_GET(self):
            if not self.trusted_host():
                self.json(403, {"error": "Local host required."})
                return
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            try:
                if path == "/api/status":
                    self.json(200, app.status(include_token=True))
                elif path == "/api/models":
                    self.json(200, app.model_options())
                elif path == '/api/context':
                    self.json(200, app.stream_context())
                elif path == '/api/reviews':
                    self.json(200, reviews.get(parse_qs(parsed.query).get('role', ['moderator'])[0]))
                elif path == "/api/test/config":
                    self.json(200, app.test_config())
                elif path == "/api/test/results":
                    self.json(200, app.test_results())
                elif path in {"/api/diagnostics", "/api/diagnostics/download"}:
                    report = app.diagnostic_report()
                    if path.endswith("/download"):
                        report["events"] = app.diagnostics.history()
                        self.send(200, json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2).encode(),
                            "application/json; charset=utf-8", {"Content-Disposition": 'attachment; filename="recall-debug.json"'})
                    else:
                        self.json(200, report)
                elif path == "/api/shared":
                    self.json(200, app.shared())
                elif path == "/api/memory":
                    q = parse_qs(parsed.query)
                    self.json(200, app.memory_dict(float(q["now"][0]) if "now" in q else None))
                elif path.startswith("/api/moments/"):
                    self.json(200, moments.get(path.rsplit("/", 1)[-1]))
                elif path in {"/video", "/api/video"}:
                    requested_run = parse_qs(parsed.query).get("run", [None])[0]
                    with app.lock:
                        if requested_run is not None and requested_run != app.run_id:
                            self.json(410, {"error": "This replay expired. Ask again for a new moment."})
                            return
                        source = app.video_path
                    self.file(source, ranges=True) if source else self.json(404, {"error": "No recording selected."})
                elif path.startswith(("/frames/", "/api/frames/")):
                    asset = app.frame_path(path.rsplit("/", 1)[-1])
                    self.file(asset) if asset else self.json(404, {"error": "Frame unavailable at the processed time."})
                elif path == "/favicon.ico":
                    self.send(204, b"", "image/x-icon")
                else:
                    name = "index.html" if path in {"/", "/broadcast", "/overlay"} else path.lstrip("/")
                    target = (built_ui / name).resolve()
                    if target.is_relative_to(built_ui) and target.is_file():
                        self.file(target)
                    elif name == "index.html":
                        self.send(503, b"<!doctype html><title>Recall</title><h1>Recall</h1>"
                            b"<p>Run Setup.ps1 to build the local interface, then Start.ps1.</p>", "text/html; charset=utf-8")
                    else:
                        self.json(404, {"error": "Not found."})
            except (ValueError, TypeError):
                self.json(400, {"error": "Invalid request."})
        def do_POST(self):
            if not self.authorized():
                return
            parsed = urlsplit(self.path)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if parsed.path == "/api/diagnostics/event" and not 0 < length <= 4096:
                    self.close_connection = True
                    self.json(413, {"error": "Diagnostic event too large."})
                    return
                if parsed.path == "/api/upload":
                    self.upload(length, parse_qs(parsed.query).get("name", ["recording.mp4"])[0])
                    return
                if not 0 < length <= 3_000_000:
                    self.close_connection = True
                    self.json(413, {"error": "Request too large or empty."})
                    return
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Expected an object.")
                if parsed.path == "/api/ask":
                    self.json(200, app.request_answer(str(data.get("question", "")), data.get("now"),
                        conversation_id=data.get('conversation_id',''), share=data.get('share', True)))
                elif parsed.path == '/api/reviews':
                    self.json(200, reviews.mutate(data))
                elif parsed.path == '/api/reviews/moment':
                    self.json(200, moments.from_evidence(data))
                elif parsed.path == '/api/context':
                    self.json(200, app.set_stream_context(data))
                elif parsed.path == "/api/model":
                    self.json(200, app.select_model(str(data.get("model", ""))))
                elif parsed.path == "/api/test/start":
                    self.json(200, app.start_test(data))
                elif parsed.path == "/api/diagnostics/event":
                    self.json(200, app.browser_event(data))
                elif parsed.path == "/api/source":
                    self.json(200, app.source_update(data))
                elif parsed.path == "/api/control":
                    self.json(200, app.control(str(data.get("action", "")), enabled=data.get("enabled"), index=data.get("index")))
                elif parsed.path == "/api/export":
                    self.json(200, {"path": str(app.export(app.cache_dir / "exports" / app.run_id))})
                elif parsed.path == "/api/moments":
                    self.json(200, moments.create(data))
                elif parsed.path == "/api/clip":
                    self.clip(data)
                else:
                    self.json(404, {"error": "Not found."})
            except BusyError as exc:
                app.diagnostics.event("request_rejected", run_id=app.run_id, endpoint=parsed.path, error_type=type(exc).__name__)
                self.json(429, {"error": str(exc)})
            except (ValueError, TypeError, KeyError) as exc:
                app.diagnostics.event("request_rejected", run_id=app.run_id, endpoint=parsed.path, error_type=type(exc).__name__)
                self.json(400, {"error": str(exc)[:250]})
            except Exception as exc:
                app.diagnostics.event("request_failed", run_id=app.run_id, endpoint=parsed.path, error_type=type(exc).__name__)
                self.json(503, {"error": "Local processing is unavailable. Check session status and retry."})
        def upload(self, length, name):
            started = time.monotonic()
            app.diagnostics.event("upload_started", run_id=app.run_id, byte_count=length)
            if not 0 < length <= MAX_UPLOAD_BYTES:
                self.close_connection = True
                self.json(413, {"error": "Choose a recording no larger than 8 GiB or launch it by local path."})
                return
            suffix = Path(name).suffix.lower()
            if suffix not in {".mp4", ".webm", ".mkv", ".mov", ".m4v"}:
                self.close_connection = True
                raise ValueError("Choose an MP4, WebM, MKV, MOV or M4V recording.")
            folder = app.cache_dir / "sources"
            folder.mkdir(exist_ok=True)
            if shutil.disk_usage(folder).free < length:
                self.close_connection = True
                self.json(507, {"error": "Not enough free space to copy this recording locally."})
                return
            target = folder / (uuid.uuid4().hex + suffix)
            try:
                with target.open("wb") as handle:
                    _copy_upload(self.rfile, handle, length)
                result = app.load_source(target)
                with app.lock:
                    app.memory.video.title = Path(name).name[:180]
                result["title"] = app.memory.video.title
                self.json(200, result)
                app.diagnostics.event("upload_finished", run_id=app.run_id, byte_count=length, duration_s=time.monotonic()-started)
            except Exception:
                # Any unread upload body must not be parsed as another request.
                self.close_connection = True
                target.unlink(missing_ok=True)
                raise
        def clip(self, data):
            started = time.monotonic()
            start, end = finite_time(data["start_s"]), finite_time(data["end_s"])
            if not 0 <= start < end or end - start > 60:
                raise ValueError("Choose an evidence clip between zero and 60 seconds long.")
            with app.lock:
                answer = app.answer or {}
                moment = moments.get(str(data["moment_id"])) if data.get("moment_id") else None
                if moment:
                    answer = {"run_id": moment["run_id"], "through_s": moment["through_s"],
                              "citations": [{"t": moment["start_s"], "t_end": moment["end_s"]}]}
                if data.get("run_id") != app.run_id or answer.get("run_id") != app.run_id:
                    raise ValueError("This answer belongs to an earlier replay. Ask again.")
                cutoff = min(app.processed_through_s, finite_time(answer.get("through_s", 0)))
                if end > cutoff + 0.000001 or not app.video_path:
                    raise ValueError("This clip is beyond the available evidence.")
                allowed = False
                for citation in answer.get("citations", []):
                    t = finite_time(citation.get("t", 0))
                    t_end = finite_time(citation.get("t_end", t))
                    evidence_end = t_end if t_end > t else t + 5
                    if start >= t - 0.000001 and end <= min(evidence_end, cutoff) + 0.000001:
                        allowed = True
                        break
                if not allowed:
                    raise ValueError("Choose a range within the answer's supporting evidence.")
                run, video = app.run_id, app.video_path
            if not clip_slot.acquire(blocking=False):
                raise BusyError("A clip is already being prepared. Please try again shortly.")
            try:
                app.diagnostics.event("clip_started", run_id=run, start_s=start, end_s=end)
                with tempfile.TemporaryDirectory(prefix="tube-clip-", dir=app.cache_dir) as folder:
                    output = Path(folder) / "recall-clip.mp4"
                    ffmpeg(["-y", "-ss", str(start), "-i", str(video), "-t", str(end - start),
                            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
                            "-c:a", "aac", "-movflags", "+faststart", str(output)], timeout=120)
                    with app.lock:
                        if run != app.run_id or end > app.processed_through_s + 0.000001:
                            raise ValueError("The replay changed while preparing the clip. Ask again.")
                    self.file(output, ranges=True, extra_headers={
                        "Content-Disposition": 'attachment; filename="recall-clip.mp4"'})
                    app.diagnostics.event("clip_finished", run_id=run, start_s=start, end_s=end, duration_s=time.monotonic()-started)
            finally:
                clip_slot.release()

        def file(self, path, *, ranges=False, extra_headers=None):
            started = time.monotonic()
            request_id = uuid.uuid4().hex[:12]
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".js":
                ctype = "text/javascript; charset=utf-8"
            start, end, status = 0, size - 1, 200
            spec = self.headers.get("Range") if ranges else None
            if spec:
                try:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", spec)
                    if not match or not any(match.groups()):
                        raise ValueError
                    a, b = match.groups()
                    if not a:
                        if int(b) <= 0:
                            raise ValueError
                        start = max(0, size - int(b))
                    else:
                        start = int(a)
                        end = min(int(b), size - 1) if b else size - 1
                    if start < 0 or start > end or start >= size:
                        raise ValueError
                    status = 206
                except ValueError:
                    self.send(416, b"", ctype, {"Content-Range": f"bytes */{size}"})
                    return
            extra = {"Accept-Ranges": "bytes"} if ranges else {}
            extra.update(extra_headers or {})
            if status == 206:
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            if not ranges:
                self.send(status, path.read_bytes(), ctype)
                return
            self.send_response(status)
            app.diagnostics.event("media_request_started", run_id=app.run_id, request_id=request_id,
                endpoint=urlsplit(self.path).path, operation=self.command, http_status=status, byte_count=max(0,end-start+1))
            for key, value in {"Content-Type": ctype, "Content-Length": str(max(0, end-start+1)),
                "X-Content-Type-Options": "nosniff", "Cross-Origin-Resource-Policy": "same-origin",
                "Cache-Control": "no-store", **extra}.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command == "HEAD":
                return
            sent = 0
            try:
                with path.open("rb") as handle:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = handle.read(min(256*1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                        sent += len(chunk)
                app.diagnostics.event("media_request_finished", run_id=app.run_id, request_id=request_id,
                    duration_s=time.monotonic()-started, output_bytes=sent, success=True)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
                app.diagnostics.event("media_request_finished", run_id=app.run_id, request_id=request_id,
                    duration_s=time.monotonic()-started, output_bytes=sent, success=False, error_type=type(exc).__name__)
    class LocalServer(ThreadingHTTPServer):
        daemon_threads = True
        def handle_error(self, request, client_address):
            error = sys.exception()
            app.diagnostics.event("http_connection_closed" if isinstance(error, ConnectionError) else "server_error",
                                  run_id=app.run_id, error_type=type(error).__name__)
            if not isinstance(error, ConnectionError):
                super().handle_error(request, client_address)
        def server_close(self):
            app.close()
            super().server_close()
    server = LocalServer((host, port), Handler)
    server.session = app
    return server


def serve(bundle=None, video=None, host="127.0.0.1", port=8765, **kwargs):
    server = create_server(bundle, video, host, port, **kwargs)
    print(f"Recall: http://127.0.0.1:{server.server_address[1]}/", flush=True)
    print("OBS browser source: append /broadcast. Files and inference stay local.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
