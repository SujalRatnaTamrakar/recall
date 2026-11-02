"""Offline tests of the actual Twitch command/transport adapter; no credentials."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from tubelive.twitch import TwitchBot, _AuthorizationError, _reconnect_url, parse_command


def _bot(on_ask=None, status=None):
    bot = TwitchBot("client", "secret-token", on_ask or (lambda q, t: {"summary": q}),
                    lambda action: {}, lambda: status or {"processed_through_s": 42, "playback_s": 40})
    bot._user_id = "channel"
    bot.replies = []
    bot._reply = lambda text, parent: bot.replies.append((text, parent))
    return bot


def _event(text, identifier="event-1", user="viewer", badges=None):
    return {"metadata": {"message_type": "notification", "message_id": identifier,
                         "subscription_type": "channel.chat.message"},
            "payload": {"event": {"broadcaster_user_id": "channel", "chatter_user_id": user,
                                   "message_id": "chat-" + identifier, "message": {"text": text},
                                   "badges": badges or []}}}


def test_twitch_parser_and_timestamp_validation():
    assert parse_command(" !recall @12:34 Why did that happen? ") == ("ask", "Why did that happen?", 754.0)
    assert parse_command("!recall Where is the red cup?") == ("ask", "Where is the red cup?", None)
    assert parse_command("!RECALL What's happening?") == ("ask", "What's happening?", None)
    assert parse_command("!ask Where is the red cup?") == ("ask", "Where is the red cup?", None)
    assert parse_command("!ask @12:34 Why did that happen?") == ("ask", "Why did that happen?", 754.0)
    assert parse_command("!cube left") == ("control", "cube_left", None)
    assert parse_command("!tube disable") == ("admin", "disable_controls", None)
    assert parse_command("!recall disable") == ("admin", "disable_controls", None)
    assert parse_command("!RECALL enable") == ("admin", "enable_controls", None)
    for invalid in ("hello", "!recall", "!recall @1:99 why", "!recalling why", "!ask", "!ask @1:99 why",
                    "!cube delete", "!tube disable now", "!recall disable now"):
        assert parse_command(invalid) is None


def test_twitch_token_validation_and_secret_free_status():
    bot = _bot()
    calls = []
    def http(method, url, body=None):
        calls.append((method, url))
        return {"client_id": "client", "user_id": "channel",
                "scopes": ["user:read:chat", "user:write:chat"]}
    bot._http = http
    bot._validate()
    assert calls == [("GET", "https://id.twitch.tv/oauth2/validate")]
    assert bot._user_id == "channel" and bot._last_validated > 0
    assert "secret-token" not in json.dumps(bot.status())
    bot._http = lambda *args: {"client_id": "wrong", "user_id": "channel", "scopes": []}
    try:
        bot._validate()
        assert False, "wrong app and missing scopes must be rejected"
    except _AuthorizationError:
        pass


def test_twitch_ordinary_chat_dedup_self_and_wrong_channel():
    bot = _bot()
    bot._notification(_event("hello"))
    assert bot._jobs.empty() and not bot.replies
    message = _event("!recall why?", "ask-1")
    bot._notification(message)
    bot._notification(message)
    bot._notification(_event("!recall own message", "ask-2", user="channel"))
    wrong = _event("!recall wrong channel", "ask-3", user="other")
    wrong["payload"]["event"]["broadcaster_user_id"] = "different-channel"
    bot._notification(wrong)
    assert bot._jobs.qsize() == 1 and bot._inflight == 1


def test_twitch_queue_bound_cooldown_and_frozen_cutoff():
    status = {"processed_through_s": 42, "playback_s": 40}
    bot = _bot(status=status)
    bot._notification(_event("!recall latest", "one", "one"))
    status["processed_through_s"] = 100
    status["playback_s"] = 90
    bot._notification(_event("!ask @0:30 earlier", "two", "two"))
    bot._notification(_event("!recall busy", "three", "three"))
    bot._notification(_event("!recall too soon", "four", "one"))
    assert bot._inflight == 2 and bot._jobs.qsize() == 2
    assert bot._jobs.get_nowait() == ("latest", 40, "chat-one", "")
    assert bot._jobs.get_nowait() == ("earlier", 30, "chat-two", "")
    assert any("two questions" in text for text, _ in bot.replies)
    assert any("10 seconds" in text for text, _ in bot.replies)


def test_twitch_future_timestamp_clamped_and_invalid_status_fails_closed():
    bot = _bot(status={"processed_through_s": 22, "playback_s": 20})
    bot._notification(_event("!recall @9:59 future"))
    assert bot._jobs.get_nowait()[1] == 20
    bot = _bot(status={"processed_through_s": float("nan"), "playback_s": 200})
    bot._notification(_event("!recall future"))
    assert bot._jobs.get_nowait()[1] == 0


def test_twitch_controls_admin_roles_and_disabled_state():
    controls = []
    status = {"controls_enabled": True}
    bot = _bot(status=status)
    bot._on_control = lambda action: controls.append(action) or {}
    bot._notification(_event("!cube next", "next"))
    bot._notification(_event("!cube prev", "prev"))
    bot._notification(_event("!recall disable", "unauthorized"))
    bot._notification(_event("!recall disable", "mod", badges=[{"set_id": "moderator"}]))
    bot._notification(_event("!tube enable", "owner", user="channel"))
    assert controls == ["cube_next", "disable_controls", "enable_controls"]
    status["controls_enabled"] = False
    bot._last_control = -float("inf")
    bot._notification(_event("!cube right", "disabled"))
    assert len(controls) == 3


def test_twitch_inference_worker_public_reply_and_stop():
    got = []
    bot = _bot(on_ask=lambda question, cutoff: got.append((question, cutoff)) or {"summary": "Red cup [0:20]."})
    bot._notification(_event("!recall cup?"))
    original_reply = bot._reply
    done = threading.Event()
    def reply(text, parent):
        original_reply(text, parent)
        done.set()
        bot._stop.set()
    bot._reply = reply
    thread = threading.Thread(target=bot._work)
    thread.start()
    assert done.wait(2), "worker must process the queued question"
    thread.join(2)
    assert got == [("cup?", 40)]
    assert bot.replies == [("Recall @0:40: Red cup [0:20].", "chat-event-1")]
    assert bot._inflight == 0


def test_twitch_question_waiting_during_restart_cannot_query_the_new_recording():
    status = {"run_id": "old", "processed_through_s": 40, "playback_s": 40}
    called = []
    bot = _bot(on_ask=lambda *args: called.append(args) or {"summary": "wrong session"}, status=status)
    bot._notification(_event("!recall what happened?"))
    status["run_id"] = "new"
    original = bot._reply
    def reply(text, parent):
        original(text, parent)
        bot._stop.set()
    bot._reply = reply
    thread = threading.Thread(target=bot._work)
    thread.start()
    thread.join(2)
    assert not called and "recording changed" in bot.replies[0][0]


def test_twitch_reply_limit_no_localhost_and_api_parent():
    bot = _bot()
    calls = []
    bot._http = lambda method, url, body=None: calls.append(body) or {"data": [{"is_sent": True}]}
    TwitchBot._reply(bot, "Evidence http://127.0.0.1:8765/private " + "x" * 600, "parent")
    assert not calls, "EventSub reader must not block on reply network I/O"
    text, parent = bot._outbox.get_nowait()
    bot._post(text, parent)
    assert len(calls[0]["message"]) <= 450
    assert "127.0.0.1" not in calls[0]["message"]
    assert calls[0]["reply_parent_message_id"] == "parent"
    assert calls[0]["sender_id"] == calls[0]["broadcaster_id"] == "channel"


class _Socket:
    def __init__(self, messages):
        self.messages = iter(json.dumps(message) for message in messages)
        self.closed = False
    def recv(self):
        return next(self.messages)
    def settimeout(self, timeout):
        self.timeout = timeout
    def close(self):
        self.closed = True


def _welcome(identifier):
    return {"metadata": {"message_type": "session_welcome"},
            "payload": {"session": {"id": identifier, "keepalive_timeout_seconds": 10}}}


def test_twitch_actual_transport_subscribe_transfer_and_revocation():
    bot = _bot()
    bot._validate = lambda: setattr(bot, "_last_validated", __import__("time").monotonic())
    reconnect = {"metadata": {"message_type": "session_reconnect"}, "payload": {
        "session": {"reconnect_url": "wss://eventsub.wss.twitch.tv/ws?session=transfer"}}}
    revocation = {"metadata": {"message_type": "revocation"}, "payload": {}}
    first = _Socket([_welcome("first"), reconnect])
    second = _Socket([_welcome("second"), revocation])
    sockets = iter([first, second])
    connected = []
    def connect(url):
        assert _reconnect_url(url)
        connected.append(url)
        return next(sockets)
    bot._connect = connect
    calls = []
    bot._http = lambda method, url, body=None: calls.append(body) or {}
    bot._run()
    assert len(connected) == 2
    assert len(calls) == 1, "transferred subscriptions must not be recreated"
    assert calls[0]["transport"] == {"method": "websocket", "session_id": "first"}
    assert calls[0]["condition"] == {"broadcaster_user_id": "channel", "user_id": "channel"}
    assert first.closed and second.closed
    assert bot.status()["state"] == "authorization_required"
    assert bot.status()["reconnects"] == 1 and bot._stop.is_set()


def test_twitch_reconnect_allowlist_and_failed_auth_stop():
    assert _reconnect_url("wss://eventsub.wss.twitch.tv/ws?x=1")
    for address in ("ws://eventsub.wss.twitch.tv/ws", "wss://evil.test/ws",
                    "wss://eventsub.wss.twitch.tv.evil.test/ws", "wss://user@eventsub.wss.twitch.tv/ws",
                    "wss://eventsub.wss.twitch.tv:999/ws"):
        assert not _reconnect_url(address)
    bot = _bot()
    def unauthorized():
        raise _AuthorizationError()
    bot._validate = unauthorized
    bot._run()
    assert bot._stop.is_set() and bot.status()["state"] == "authorization_required"


def test_twitch_hourly_validation_without_blocking_events():
    from unittest.mock import patch
    clock = [0.0]
    validations = []
    bot = _bot()
    def validate():
        validations.append(clock[0])
        bot._last_validated = clock[0]
    bot._validate = validate
    messages = [_welcome("one"), {"metadata": {"message_type": "session_keepalive"}},
                {"metadata": {"message_type": "revocation"}}]
    class ClockSocket(_Socket):
        def recv(self):
            message = super().recv()
            if "session_keepalive" in message:
                clock[0] = 3601.0
            return message
    bot._connect = lambda url: ClockSocket(messages)
    bot._http = lambda *args: {}
    with patch("tubelive.twitch.time.monotonic", lambda: clock[0]):
        bot._run()
    assert validations == [0.0, 3601.0]


def test_twitch_keepalive_failure_records_context_gap():
    from unittest.mock import patch
    bot = _bot()
    bot._validate = lambda: None
    class TimeoutSocket(_Socket):
        def recv(self):
            raise TimeoutError()
    connection = TimeoutSocket([])
    bot._connect = lambda url: connection
    bot._stop.wait = lambda seconds: bot._stop.set() or True
    with patch("tubelive.twitch.time.monotonic", side_effect=[0.0, 10.0, 20.0]):
        bot._run()
    assert bot.status()["context_gaps"] == 1
    assert "cannot be recovered" in bot.status()["error"]
    assert connection.closed
