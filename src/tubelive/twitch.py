"""Installed-broadcaster Twitch chat adapter. All inference stays in callbacks.

Only command text enters the bounded question queue. Ordinary chat is neither
retained nor added to stream memory. Network I/O and local inference run on
separate threads; no listener is exposed to the internet.
"""
from __future__ import annotations

import json
import math
import queue
import re
import socket
import threading
import time
from collections import OrderedDict
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class _AuthorizationError(RuntimeError):
    pass


def parse_command(text: str) -> tuple[str, str, float | None] | None:
    """Parse only the documented commands; timestamps are recording time."""
    text = text.strip()
    # Reserve existing moderator commands before handling Recall questions.
    match = re.fullmatch(r"!(?:recall|tube)\s+(disable|enable)", text, re.IGNORECASE)
    if match:
        return "admin", match.group(1).lower() + "_controls", None
    if re.match(r"!(?:recall|tube)\s+(?:disable|enable)(?:\s|$)", text, re.IGNORECASE):
        return None
    match = re.fullmatch(r"!(?:recall|ask)\s+(?:@(\d+):([0-5]\d)\s+)?(.+)", text,
                         flags=re.IGNORECASE | re.DOTALL)
    if match:
        question = match.group(3).strip()
        # An invalid explicit timestamp must not silently become a question.
        if not question or question.startswith("@"):
            return None
        at = int(match.group(1)) * 60 + int(match.group(2)) if match.group(1) else None
        return "ask", question, float(at) if at is not None else None
    match = re.fullmatch(r"!cube\s+(next|prev|left|right)", text, re.IGNORECASE)
    if match:
        return "control", "cube_" + match.group(1).lower(), None
    return None


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
        return max(0.0, number) if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _reconnect_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "wss" and parsed.hostname == "eventsub.wss.twitch.tv"
                and parsed.port in (None, 443) and parsed.path == "/ws"
                and not parsed.username and not parsed.password and not parsed.fragment)
    except (TypeError, ValueError):
        return False


class TwitchBot:
    def __init__(self, client_id: str, access_token: str,
                 on_ask: Callable[[str, float | None], dict],
                 on_control: Callable[[str], dict],
                 get_status: Callable[[], dict]):
        self._client_id = client_id.strip()
        self._token = access_token.strip()
        self._on_ask = on_ask
        self._on_control = on_control
        self._get_status = get_status
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._jobs: queue.Queue[tuple[str, float, str, str]] = queue.Queue(maxsize=2)
        self._outbox: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=8)
        self._inflight = 0
        self._threads: list[threading.Thread] = []
        self._socket = None
        self._user_id = ""
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._cooldown: dict[str, float] = {}
        self._notice_times: dict[str, float] = {}
        self._last_control = -math.inf
        self._last_validated = 0.0
        self._state = "stopped"
        self._error = ""
        self._gaps = 0
        self._reconnects = 0
        self._last_reply = -math.inf

    def status(self) -> dict:
        with self._lock:
            return {"state": self._state, "connected": self._state == "connected",
                    "channel_user_id": self._user_id, "pending_questions": self._inflight,
                    "context_gaps": self._gaps, "reconnects": self._reconnects,
                    "error": self._error}

    def start(self) -> None:
        with self._lock:
            if any(thread.is_alive() for thread in self._threads):
                return
            self._stop.clear()
            self._state, self._error = "connecting", ""
            self._threads = [threading.Thread(target=self._run, daemon=True, name="twitch-events"),
                             threading.Thread(target=self._work, daemon=True, name="twitch-answers"),
                             threading.Thread(target=self._send, daemon=True, name="twitch-replies")]
            for thread in self._threads:
                thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            connection = self._socket
            self._state = "stopped"
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=2)
        self._discard_waiting()
        while not self._outbox.empty():
            try:
                self._outbox.get_nowait()
                self._outbox.task_done()
            except queue.Empty:
                break

    def _discard_waiting(self) -> None:
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                self._inflight = max(0, self._inflight - 1)
            self._jobs.task_done()

    def _http(self, method: str, url: str, body: dict | None = None) -> dict:
        headers = {"Authorization": "Bearer " + self._token,
                   "Client-Id": self._client_id, "Content-Type": "application/json"}
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read(1_000_000))
        except HTTPError as error:
            if error.code in (401, 403):
                raise _AuthorizationError("Twitch authorization unavailable") from None
            raise RuntimeError("Twitch API request failed") from None

    def _validate(self) -> None:
        if not self._client_id or not self._token:
            raise _AuthorizationError("Twitch credentials are required")
        data = self._http("GET", "https://id.twitch.tv/oauth2/validate")
        if (data.get("client_id") != self._client_id or not data.get("user_id")
                or not {"user:read:chat", "user:write:chat"}.issubset(set(data.get("scopes", [])))):
            raise _AuthorizationError("Token must authorize read/write chat for this app")
        with self._lock:
            self._user_id = str(data["user_id"])
            self._last_validated = time.monotonic()

    def _connect(self, url: str):
        if not _reconnect_url(url):
            raise ValueError("Unexpected EventSub address")
        import websocket  # Optional dependency until a connection is requested.
        connection = websocket.create_connection(url, timeout=10, enable_multithread=True)
        connection.settimeout(1)
        return connection

    def _subscribe(self, session_id: str) -> None:
        self._http("POST", "https://api.twitch.tv/helix/eventsub/subscriptions", {
            "type": "channel.chat.message", "version": "1",
            "condition": {"broadcaster_user_id": self._user_id, "user_id": self._user_id},
            "transport": {"method": "websocket", "session_id": session_id}})

    def _fatal(self) -> None:
        with self._lock:
            self._state = "authorization_required"
            self._error = "Twitch authorization ended. Reconnect with a valid broadcaster token."
        self._stop.set()
        self._discard_waiting()

    def _transfer(self, url: str):
        new_socket = self._connect(url)
        try:
            new_socket.settimeout(10)
            welcome = json.loads(new_socket.recv())
            if welcome.get("metadata", {}).get("message_type") != "session_welcome":
                raise RuntimeError("Reconnect did not welcome the session")
            new_socket.settimeout(1)
            session = welcome["payload"]["session"]
            if not session.get("id"):
                raise RuntimeError("Reconnect has no session")
            return new_socket, _number(session.get("keepalive_timeout_seconds"), 10)
        except Exception:
            new_socket.close()
            raise

    def _run(self) -> None:
        retry = 1.0
        while not self._stop.is_set():
            connection = None
            try:
                self._validate()
                connection = self._connect("wss://eventsub.wss.twitch.tv/ws")
                with self._lock:
                    self._socket = connection
                last_received, keepalive, welcomed = time.monotonic(), 10.0, False
                while not self._stop.is_set():
                    now = time.monotonic()
                    if now - self._last_validated >= 3600:
                        self._validate()
                    if now - last_received > keepalive + 5:
                        raise TimeoutError("EventSub keepalive missed")
                    try:
                        raw = connection.recv()
                    except Exception as error:
                        if isinstance(error, (TimeoutError, socket.timeout)) or type(error).__name__ == "WebSocketTimeoutException":
                            continue
                        raise
                    if not raw:
                        raise ConnectionError("EventSub closed")
                    last_received = time.monotonic()
                    message = json.loads(raw)
                    kind = message.get("metadata", {}).get("message_type")
                    if kind == "session_welcome":
                        if welcomed:
                            continue
                        session = message["payload"]["session"]
                        self._subscribe(session["id"])
                        keepalive = _number(session.get("keepalive_timeout_seconds"), 10)
                        welcomed, retry = True, 1.0
                        with self._lock:
                            self._state, self._error = "connected", ""
                    elif kind == "session_reconnect":
                        replacement, keepalive = self._transfer(message["payload"]["session"]["reconnect_url"])
                        old_socket, connection = connection, replacement
                        with self._lock:
                            self._socket = connection
                            self._reconnects += 1
                        old_socket.close()
                        last_received = time.monotonic()
                    elif kind == "revocation":
                        self._fatal()
                        break
                    elif kind == "notification" and welcomed:
                        self._notification(message)
            except _AuthorizationError:
                self._fatal()
            except Exception:
                if not self._stop.is_set():
                    with self._lock:
                        self._state = "disconnected"
                        self._gaps += 1
                        self._error = "Chat connection interrupted; messages during this gap cannot be recovered."
                    self._stop.wait(retry)
                    retry = min(30.0, retry * 2)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
                with self._lock:
                    self._socket = None

    def _notification(self, message: dict) -> None:
        metadata = message.get("metadata", {})
        if metadata.get("subscription_type") != "channel.chat.message":
            return
        identifier = metadata.get("message_id")
        if not identifier or identifier in self._seen:
            return
        self._seen[identifier] = None
        if len(self._seen) > 2048:
            self._seen.popitem(last=False)
        event = message.get("payload", {}).get("event", {})
        if str(event.get("broadcaster_user_id", "")) != self._user_id:
            return
        chatter = str(event.get("chatter_user_id", ""))
        if not chatter:
            return
        text = event.get("message", {}).get("text", "")
        parsed = parse_command(text) if isinstance(text, str) else None
        if parsed is None:
            return
        command, value, requested = parsed
        # Generated bot replies never start with commands. The broadcaster may
        # still use the administrative command while other self-messages are ignored.
        if chatter == self._user_id and command != "admin":
            return
        reply_id = str(event.get("message_id", ""))
        now = time.monotonic()
        if command == "admin":
            badges = {badge.get("set_id") for badge in event.get("badges", [])}
            if chatter == self._user_id or badges.intersection({"broadcaster", "moderator"}):
                self._control(value, reply_id)
            return
        status = self._get_status()
        if command == "control":
            if not status.get("controls_enabled", True) or now - self._last_control < 3:
                return
            self._last_control = now
            self._control(value, reply_id)
            return
        if len(value) > 450:
            self._notice(chatter, reply_id, "Please shorten the question to 450 characters.")
            return
        processed = _number(status.get("processed_through_s"))
        playback = _number(status.get("playback_s", status.get("source_time_s", processed)))
        cutoff = min(processed, playback, requested if requested is not None else processed)
        with self._lock:
            if now - self._cooldown.get(chatter, -math.inf) < 10:
                notice = "Please wait 10 seconds between questions."
            elif self._inflight >= 2:
                notice = "Recall is answering two questions. Please try again shortly."
            else:
                self._inflight += 1
                self._cooldown[chatter] = now
                self._cooldown = {user: at for user, at in self._cooldown.items() if now - at < 60}
                self._jobs.put_nowait((value, cutoff, reply_id, str(status.get("run_id", ""))))
                return
        self._notice(chatter, reply_id, notice)

    def _control(self, action: str, reply_id: str) -> None:
        try:
            self._on_control(action)
        except Exception:
            self._reply("Recall could not change the shared view. Please try again.", reply_id)

    def _notice(self, chatter: str, reply_id: str, text: str) -> None:
        now = time.monotonic()
        if now - self._notice_times.get(chatter, -math.inf) < 10:
            return
        self._notice_times[chatter] = now
        self._notice_times = {user: at for user, at in self._notice_times.items() if now - at < 60}
        self._reply(text, reply_id)

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                question, cutoff, reply_id, run_id = self._jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if self._stop.is_set():
                    continue
                if run_id and run_id != str(self._get_status().get("run_id", "")):
                    self._reply("The recording changed. Please ask again for the current session.", reply_id)
                    continue
                answer = self._on_ask(question, cutoff)
                if run_id and run_id != str(self._get_status().get("run_id", "")):
                    self._reply("The recording changed. Please ask again for the current session.", reply_id)
                    continue
                content = answer.get("summary") or answer.get("answer") or "I do not have enough observed evidence yet."
                if isinstance(content, dict):
                    content = content.get("summary") or "I do not have enough observed evidence yet."
                stamp = f"{int(cutoff) // 60}:{int(cutoff) % 60:02d}"
                if not self._stop.is_set():
                    self._reply(f"Recall @{stamp}: {content}", reply_id)
            except Exception:
                if not self._stop.is_set():
                    self._reply("Recall could not answer from the available evidence. Please try again.", reply_id)
            finally:
                with self._lock:
                    self._inflight = max(0, self._inflight - 1)
                self._jobs.task_done()

    def _reply(self, text: str, reply_id: str) -> None:
        # A localhost URL is unusable by remote viewers and must never be sent.
        text = re.sub(r"https?://(?:localhost|127\.0\.0\.1|\[::1\])\S*", "", str(text), flags=re.IGNORECASE)
        text = " ".join(text.split())[:450]
        if not text or self._stop.is_set():
            return
        try:
            self._outbox.put_nowait((text, reply_id))
        except queue.Full:
            with self._lock:
                self._error = "Reply queue is full; some replies were not published."

    def _send(self) -> None:
        while not self._stop.is_set():
            try:
                text, reply_id = self._outbox.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._post(text, reply_id)
            finally:
                self._outbox.task_done()

    def _post(self, text: str, reply_id: str) -> None:
        with self._send_lock:
            wait = max(0.0, 1.1 - (time.monotonic() - self._last_reply))
            if self._stop.wait(wait):
                return
            self._last_reply = time.monotonic()
            body = {"broadcaster_id": self._user_id, "sender_id": self._user_id, "message": text}
            if reply_id:
                body["reply_parent_message_id"] = reply_id
            try:
                result = self._http("POST", "https://api.twitch.tv/helix/chat/messages", body)
                if not result.get("data", [{}])[0].get("is_sent", False):
                    with self._lock:
                        self._error = "Twitch did not publish a reply; check channel chat settings."
            except _AuthorizationError:
                self._fatal()
            except Exception:
                with self._lock:
                    self._error = "Twitch could not publish a reply."
