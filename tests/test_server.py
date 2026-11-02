"""Real loopback HTTP tests; stub only the built UI and local model outputs."""
from __future__ import annotations
import base64
import json
import io
import http.client
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from tubelive.runtime import ReplaySession
from tubelive.schema import SceneSegment, VideoMeta
from tubelive.server import create_server, _copy_upload, MAX_UPLOAD_BYTES, UPLOAD_CHUNK_BYTES
from tubelive.store import StreamMemory
from tubelive.twitch import TwitchBot
GTA = ROOT / "fixtures" / "gta_rp_demo" / "memory.json"


class _Running:
    def __init__(self, video=None, session=None):
        self.temp = tempfile.TemporaryDirectory(prefix="tube-http-test-")
        folder = Path(self.temp.name)
        ui = folder / "viewer"
        ui.mkdir()
        (ui / "index.html").write_text('<!doctype html><title>Recall test interface</title><script src="/app.js"></script>')
        (ui / "app.js").write_text('console.log("local viewer");')
        (folder / "outside-secret.txt").write_text("DO NOT SERVE THIS")
        self.session = session or ReplaySession(memory=StreamMemory.load(GTA), video=video, cache_dir=folder / "cache")
        self.httpd = create_server(session=self.session, host="127.0.0.1", port=0, viewer_dir=ui)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, *, payload=None, data=None, headers=None, method=None, authorized=True):
        values = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode()
            values.setdefault("Content-Type", "application/json")
        if method == "POST" or data is not None:
            method = method or "POST"
            if authorized:
                values.setdefault("X-Tube-Token", self.session.csrf_token)
                values.setdefault("Origin", self.base)
        req = urllib.request.Request(self.base + path, data=data, headers=values, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, response.headers, response.read()

    def json(self, path, **kwargs):
        status, headers, body = self.request(path, **kwargs)
        return status, headers, json.loads(body)


@contextmanager
def _owned_video():
    with tempfile.TemporaryDirectory(prefix="tube-owned-video-") as folder:
        video = Path(folder) / "owned-blue-recording.mp4"
        result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", "color=c=blue:s=96x64:r=2:d=8", "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)], capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        yield video, Path(folder)


def test_test_mode_endpoints_keep_local_authorization_and_validate_before_reset():
    with _Running() as app:
        old = app.session.run_id
        code, _, config = app.json('/api/test/config')
        assert code == 200 and config['active']['answer_style'] == 'natural'
        code, _, result = app.json('/api/test/results')
        assert code == 200 and result['results'] == []
        code, _, _ = app.json('/api/test/start', payload={}, authorized=False)
        assert code == 403
        code, _, _ = app.json('/api/test/start', payload={'frame_interval_s':0})
        assert code == 400 and app.session.run_id == old


def test_server_index_assets_memory_and_security_headers():
    with _Running() as app:
        for path in ("/", "/broadcast", "/overlay"):
            status, headers, body = app.request(path)
            assert status == 200 and b"Recall test interface" in body
            assert "default-src 'self'" in headers["Content-Security-Policy"]
            assert headers["X-Content-Type-Options"] == "nosniff"
            assert headers.get("Access-Control-Allow-Origin") is None
        status, headers, body = app.request("/app.js")
        assert status == 200 and headers["Content-Type"].startswith("text/javascript")
        status, _, memory = app.json("/api/memory")
        assert status == 200 and memory["video"]["id"] == "gta-rp-demo"
        assert len(memory["records"]) >= 90 and memory["server"]["has_video"] is False
        assert memory["video"]["source"] == "" and str(app.session.cache_dir) not in json.dumps(memory)


def test_server_ask_with_csrf_and_real_fixture_answer():
    with _Running() as app:
        status, _, answer = app.json("/api/ask", payload={
            "question": "what happened to Jean Paul after he robbed the bank?"})
        assert status == 200, answer
        assert "Paleto" in answer["summary"] or "chase" in answer["summary"].lower()
        assert answer["citations"] and answer["tool_trace"]
        assert answer["query_type"] == "ENTITY_AFTER_EVENT"


def test_server_missing_wrong_token_bad_origin_and_host_rejected():
    with _Running() as app:
        for headers, authorized in (({}, False), ({"X-Tube-Token": "wrong"}, True),
                                    ({"Origin": "https://evil.example"}, True), ({"Host": "evil.example"}, True)):
            status, _, result = app.json("/api/control", payload={"action": "cube_right"},
                                         headers=headers, authorized=authorized)
            assert status == 403, result
        assert app.session.rotation == 0
        for path in ("/", "/api/status", "/api/memory", "/api/shared"):
            status, headers, result = app.json(path, headers={"Host": "evil.test"})
            assert status == 403 and "csrf_token" not in result
            assert headers.get("Access-Control-Allow-Origin") is None
        status, _, result = app.json("/api/status")
        assert status == 200 and result["csrf_token"] == app.session.csrf_token
        assert app.json("/api/control", payload={"action": "cube_right"})[0] == 200
        assert app.session.rotation > 0


def test_server_rejects_network_bind_and_static_path_traversal():
    try:
        create_server(GTA, host="0.0.0.0", port=0)
        assert False, "app must not expose the laptop to the network"
    except ValueError:
        pass
    with _Running() as app:
        for path in ("/../outside-secret.txt", "/%2e%2e/outside-secret.txt", "/..%5coutside-secret.txt",
                     "/frames/../../outside-secret.txt", "/api/frames/%2e%2e%5coutside-secret.txt"):
            status, _, body = app.request(path)
            assert status == 404 and b"DO NOT SERVE THIS" not in body


def test_server_video_ranges_suffix_head_and_unsatisfiable():
    with _owned_video() as (video, _):
        expected = video.read_bytes()
        with _Running(video=video) as app:
            for header, start, end in (("bytes=100-199", 100, 199), ("bytes=-10", len(expected)-10, len(expected)-1),
                                        ("bytes=200-", 200, len(expected)-1)):
                status, headers, body = app.request("/video", headers={"Range": header})
                assert status == 206 and body == expected[start:end+1]
                assert headers["Content-Range"] == f"bytes {start}-{end}/{len(expected)}"
                assert headers["Accept-Ranges"] == "bytes" and headers.get("Access-Control-Allow-Origin") is None
            for header in ("bytes=999999999-", "bytes=200-100", "bytes=-0", "bytes=-", "bytes=0-1,4-5"):
                status, headers, body = app.request("/video", headers={"Range": header})
                assert status == 416 and body == b""
                assert headers["Content-Range"] == f"bytes */{len(expected)}"
            status, headers, body = app.request("/video", method="HEAD")
            assert status == 200 and body == b"" and int(headers["Content-Length"]) == len(expected)
            assert app.request("/video")[2] == expected


def test_video_with_run_id_rejects_expired_recording_ranges():
    with _owned_video() as (video, _), _Running(video=video) as app:
        run = app.session.run_id
        assert app.request(f"/video?run={run}", headers={"Range": "bytes=0-9"})[0] == 206
        assert app.request("/video?run=expired", headers={"Range": "bytes=0-9"})[0] == 410
        app.session.control("restart")
        assert app.request(f"/video?run={run}", headers={"Range": "bytes=0-9"})[0] == 410


def test_diagnostic_report_download_and_safe_browser_events():
    from unittest.mock import patch
    with _Running() as app, patch("tubelive.runtime.system_snapshot", return_value={"memory":{"status":"unavailable"}}):
        status, _, result = app.json("/api/diagnostics/event", payload={"event":"browser_pause",
            "run_id":app.session.run_id,"position_s":83.7,"question":"sensitive content","token":"secret"})
        assert status == 200 and result["ok"]
        status, _, report = app.json("/api/diagnostics")
        assert status == 200 and report["events"]
        serialized = json.dumps(report)
        assert "sensitive content" not in serialized and app.session.csrf_token not in serialized
        assert str(app.session.cache_dir) not in serialized and "title" not in report["status"]
        status, headers, body = app.request("/api/diagnostics/download")
        assert status == 200 and "attachment" in headers["Content-Disposition"]
        assert any(e["event"] == "browser_pause" for e in json.loads(body)["events"])
        assert app.json("/api/diagnostics/event", payload={"event":"browser_pause","run_id":"old"})[0] == 400
        assert app.json("/api/diagnostics/event", payload={"event":"browser_pause","run_id":app.session.run_id,"text":"x"*5000})[0] == 413


def test_server_owned_video_upload_stays_in_cache_and_starts_empty_replay():
    with _owned_video() as (video, _), _Running() as app:
        before = app.json("/api/status")[2]
        status, _, uploaded = app.json("/api/upload?name=..%2F..%2Fowned.mp4", data=video.read_bytes(),
                                       headers={"Content-Type": "application/octet-stream"})
        assert status == 200, uploaded
        assert uploaded["run_id"] != before["run_id"]
        assert uploaded["mode"] == "replay" and uploaded["processed_through_s"] == 0
        assert app.session.video_path.is_relative_to(app.session.cache_dir / "sources")
        assert app.session.video_path.name != "owned.mp4"
        memory = app.json("/api/memory")[2]
        assert memory["records"] == [] and memory["through_s"] == 0
        assert memory["video"]["title"] == "owned.mp4"
        assert memory["video"]["source"] == "" and str(app.session.video_path) not in json.dumps(memory)
        assert app.json("/api/upload?name=bad.exe", data=b"x")[0] == 400
        status, _, result = app.json("/api/upload?name=not-video.mp4", data=b"not a recording")
        assert status == 503 and str(app.session.cache_dir) not in json.dumps(result)


def test_server_memory_api_respects_completed_chunks_and_processed_cutoff():
    memory = StreamMemory(VideoMeta(id="cutoff", title="Owned", duration_s=30, source="C:\\private\\source.mp4"), records=[
        SceneSegment(t_start=1, t_end=1, text="seen blue cup"),
        SceneSegment(t_start=2, t_end=2, text="future-completing chunk", payload={"window_end": 20}),
        SceneSegment(t_start=12, t_end=12, text="future red cup")])
    with tempfile.TemporaryDirectory() as folder:
        session = ReplaySession(memory=memory, cache_dir=Path(folder))
        session.playback_s = session.audio_through_s = session.vision_through_s = 5
        with _Running(session=session) as app:
            for path, cutoff in (("/api/memory", 5), ("/api/memory?now=999", 5), ("/api/memory?now=1", 1)):
                status, _, result = app.json(path)
                assert status == 200 and result["through_s"] == cutoff
                assert [row["text"] for row in result["records"]] == ["seen blue cup"]
                assert result["video"]["source"] == ""
            assert app.json("/api/memory?now=nan")[0] == 400
            assert app.json("/api/memory?now=-1")[0] == 400


def test_server_frame_route_withholds_new_and_stale_run_frames():
    with _Running() as app:
        session = app.session
        path = session.cache_dir / "blue.jpg"
        path.write_bytes(b"\xff\xd8sample-frame")
        session.playback_s = session.audio_through_s = session.vision_through_s = 4
        session.frames["frame-5"] = {"id": "frame-5", "t": 5, "url": "/frames/frame-5", "path": path, "run_id": session.run_id}
        assert app.request("/frames/frame-5")[0] == 404 and app.json("/api/memory")[2]["frames"] == []
        session.playback_s = session.audio_through_s = session.vision_through_s = 5
        assert app.request("/frames/frame-5")[0] == 200
        assert len(app.json("/api/memory")[2]["frames"]) == 1
        session.frames["frame-5"]["run_id"] = "previous-run"
        assert app.request("/frames/frame-5")[0] == 404


def test_server_stale_source_callback_and_future_frame_do_not_mutate_clock():
    with _owned_video() as (video, folder):
        session = ReplaySession(video=video, cache_dir=folder / "cache", replay=True)
        with _Running(session=session) as app:
            old_run = session.run_id
            assert app.json("/api/control", payload={"action": "restart"})[0] == 200
            assert session.run_id != old_run
            status, _, _ = app.json("/api/source", payload={"run_id": old_run, "time_s": 1, "playing": True})
            assert status == 400 and session.playback_s == 0
            status, _, _ = app.json("/api/source", payload={"run_id": session.run_id, "time_s": 1,
                "source_id": "broadcast", "playing": True,
                "frame": {"t": 3, "data": base64.b64encode(b"\xff\xd8fake").decode()}})
            assert status == 400
            assert session.playback_s == 0, "invalid future frame request must not advance the source clock"
            assert not session._ingest and session._source_owner is None


def test_server_source_owner_and_invalid_upload_size_fail_closed():
    with _owned_video() as (video, folder):
        session = ReplaySession(video=video, cache_dir=folder / "cache", replay=True)
        with _Running(session=session) as app:
            update = {"run_id": session.run_id, "source_id": "first-page", "time_s": 0, "playing": True}
            assert app.json("/api/source", payload=update)[0] == 200
            update.update(source_id="second-page", time_s=1)
            assert app.json("/api/source", payload=update)[0] == 400
            assert session.playback_s == 0 and session._source_owner == "first-page"
            status, _, _ = app.json("/api/upload?name=huge.mp4", data=b"x",
                                    headers={"Content-Length": str(MAX_UPLOAD_BYTES + 1)})
            assert status == 413 and session.video_path == video


def test_upload_streams_four_gib_in_bounded_chunks_and_detects_interruption():
    class SyntheticSource:
        def __init__(self):
            self.requests = []
            self.block = b"x" * UPLOAD_CHUNK_BYTES
        def read(self, size):
            self.requests.append(size)
            return self.block[:size]
    class CountingDestination:
        def __init__(self):
            self.total = 0
        def write(self, chunk):
            self.total += len(chunk)
    source, destination = SyntheticSource(), CountingDestination()
    size = 4 * 1024 ** 3 + 123
    assert size < MAX_UPLOAD_BYTES
    _copy_upload(source, destination, size)
    assert destination.total == size and max(source.requests) == UPLOAD_CHUNK_BYTES
    try:
        _copy_upload(io.BytesIO(b"short"), io.BytesIO(), 8)
        assert False, "an incomplete transfer must fail"
    except ValueError as exc:
        assert "interrupted" in str(exc)


def test_upload_checks_available_disk_space_before_copy(monkeypatch):
    with _owned_video() as (video, _), _Running(video=video) as app:
        monkeypatch.setattr("tubelive.server.shutil.disk_usage", lambda _: type("Disk", (), {"free": 0})())
        status, _, result = app.json("/api/upload?name=large.mp4", data=b"a file")
        assert status == 507 and "free space" in result["error"]
        assert app.session.video_path == video
        assert not list((app.session.cache_dir / "sources").iterdir())


def test_interrupted_upload_cleans_partial_file_and_closes_connection():
    with _Running() as app:
        client = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
        try:
            client.putrequest("POST", "/api/upload?name=interrupted.mp4")
            client.putheader("X-Tube-Token", app.session.csrf_token)
            client.putheader("Content-Length", "100")
            client.endheaders()
            client.send(b"incomplete")
            client.sock.shutdown(socket.SHUT_WR)
            response = client.getresponse()
            assert response.status == 400 and "interrupted" in response.read().decode()
            assert response.will_close
            assert not list((app.session.cache_dir / "sources").iterdir())
        finally:
            client.close()


def test_clip_export_is_downloadable_and_limited_to_answer_evidence():
    with _owned_video() as (video, folder):
        session = ReplaySession(video=video, cache_dir=folder / "cache", replay=True)
        session.playback_s = session.audio_through_s = session.vision_through_s = 5
        session.answer = {"run_id": session.run_id, "through_s": 3,
                          "citations": [{"t": 1, "t_end": 0}]}
        with _Running(session=session) as app:
            payload = {"run_id": session.run_id, "start_s": 1, "end_s": 3}
            status, headers, body = app.request("/api/clip", payload=payload)
            assert status == 200 and headers["Content-Type"] == "video/mp4"
            assert "attachment" in headers["Content-Disposition"] and b"ftyp" in body[:40]
            clip = folder / "clip.mp4"
            clip.write_bytes(body)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "default=nw=1:nk=1", str(clip)], capture_output=True, text=True)
            assert probe.returncode == 0 and 1.5 <= float(probe.stdout) <= 2.1
            for invalid in ({**payload, "end_s": 4}, {**payload, "start_s": 0},
                            {**payload, "end_s": 0}, {**payload, "run_id": "old-run"},
                            {**payload, "end_s": "nan"}):
                assert app.json("/api/clip", payload=invalid)[0] == 400
            assert not list(session.cache_dir.glob("tube-clip-*"))


def test_server_processing_failure_status_does_not_leak_source_paths():
    class FailingLocalModel:
        def observe_frame(self, raw, at):
            raise RuntimeError("C:\\private\\recording.mp4 and SECRET-ERROR-TOKEN must stay private")
    with _owned_video() as (video, folder):
        session = ReplaySession(video=video, cache_dir=folder / "cache", replay=True, model=FailingLocalModel())
        with _Running(session=session) as app:
            status, _, _ = app.json("/api/source", payload={"run_id": session.run_id,
                "source_id": "broadcast", "time_s": 0, "playing": True,
                "frame": {"t": 0, "data": base64.b64encode(b"\xff\xd8test").decode()}})
            assert status == 200
            deadline = time.monotonic() + 2
            while not session.error and time.monotonic() < deadline:
                time.sleep(0.01)
            status, _, result = app.json("/api/status")
            assert status == 200 and result["state"] == "gap"
            serialized = json.dumps(result)
            assert "SECRET-ERROR-TOKEN" not in serialized and "private" not in serialized
            assert str(video) not in serialized and str(session.cache_dir) not in serialized


def test_moment_links_freeze_context_survive_questions_and_expire_on_restart():
    with _owned_video() as (video, folder):
        session = ReplaySession(video=video, cache_dir=folder / "cache", replay=True)
        session.playback_s = session.audio_through_s = session.vision_through_s = 7
        session.answer = {"run_id": session.run_id, "through_s": 6,
                          "citations": [{"evidence_id": "real", "t": 1, "t_end": 2, "excerpt": "Migratory birds"}]}
        with _Running(session=session) as app:
            status, _, moment = app.json('/api/moments', payload={"run_id": session.run_id,"evidence_id":"real"})
            assert status == 200 and moment['start_s'] == 1 and moment['end_s'] == 6
            assert app.json('/api/moments', payload={"run_id": session.run_id,"evidence_id":"fake"})[0] == 400
            session.answer = None
            assert app.json('/api/moments/'+moment['id'])[0] == 200
            payload = {"run_id": session.run_id,"moment_id":moment['id'],"start_s":2,"end_s":3}
            assert app.request('/api/clip',payload=payload)[0] == 200
            assert app.request('/api/clip',payload={**payload,"end_s":7})[0] == 400
            session.control('restart')
            assert app.json('/api/moments/'+moment['id'])[0] == 400
            assert app.request('/api/clip',payload=payload)[0] == 400


def test_server_public_status_does_not_disclose_twitch_token_or_paths():
    with _Running() as app:
        session = app.session
        session.bot = TwitchBot("client", "THIS-IS-A-PRIVATE-TWITCH-TOKEN", session.request_answer,
                                lambda action: session.control(action, remote=True), session.status)
        for path in ("/api/status", "/api/memory", "/api/shared"):
            status, headers, body = app.request(path)
            assert status == 200 and b"THIS-IS-A-PRIVATE-TWITCH-TOKEN" not in body
            assert str(session.cache_dir).encode() not in body and headers.get("Access-Control-Allow-Origin") is None


def test_model_selection_endpoint_requires_local_token_and_keeps_run():
    class Models:
        model = 'vision:small'
        def installed_models(self):
            return [{'name':'vision:small','compatible':True}, {'name':'vision:large','compatible':True}]
        def select_model(self,name):
            if name not in {'vision:small','vision:large'}: raise ValueError('Not installed')
            self.model = name
    with _Running() as app:
        app.session.model = Models()
        run = app.session.run_id
        status,_,options = app.json('/api/models')
        assert status == 200 and options['selected'] == 'vision:small'
        assert app.json('/api/model',payload={'model':'vision:large'},authorized=False)[0] == 403
        assert app.json('/api/model',payload={'model':'missing'})[0] == 400
        status,_,result = app.json('/api/model',payload={'model':'vision:large'})
        assert status == 200 and result['selected'] == 'vision:large'
        assert app.session.run_id == run and app.session.memory.records


def test_server_twitch_callbacks_use_real_runtime_and_disabled_controls():
    with _Running() as app:
        session = app.session
        bot = TwitchBot("client", "offline-token", session.request_answer,
                        lambda action: session.control(action, remote=True), session.status)
        bot._user_id = "channel"
        done, replies = threading.Event(), []
        def reply(text, parent):
            replies.append((text, parent))
            done.set()
            bot._stop.set()
        bot._reply = reply
        event = {"metadata": {"subscription_type": "channel.chat.message", "message_id": "event"},
                 "payload": {"event": {"broadcaster_user_id": "channel", "chatter_user_id": "viewer",
                    "message_id": "parent", "message": {"text": "!recall @20:00 what happened in the last 5 minutes?"}}}}
        bot._notification(event)
        worker = threading.Thread(target=bot._work)
        worker.start()
        assert done.wait(5)
        worker.join(2)
        assert replies and "chase" in replies[0][0].lower(), replies
        assert session.answer["through_s"] == 1200
        session.control("disable_controls")
        event["metadata"]["message_id"] = "control-event"
        event["payload"]["event"]["message"]["text"] = "!cube right"
        bot._notification(event)
        assert session.rotation == 0
