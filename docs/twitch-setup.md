# Twitch setup for the local replay MVP

Recall runs on your computer. Twitch receives the broadcast video and the
public replies you choose to send; speech, screenshots, memory, and questions
are processed locally. The source is an owned recording replayed progressively,
not an arbitrary Twitch stream downloader. Label the broadcast **recorded replay**.

## 1. Prepare the local app

Follow the README install instructions and run `recall prepare` before the
demo. Preparation can download the local speech model and checks the configured
Ollama model. Runtime uses the isolated local Ollama service with cloud disabled;
it does not change your global Ollama settings. `websocket-client` supplies the
outbound EventSub connection; Python's standard library supplies the Twitch API
requests. No OpenAI API key is required for runtime inference.

## 2. Register your own Twitch application

1. Sign into the [Twitch Developer Console](https://dev.twitch.tv/console/apps)
   with the account that owns your test channel. Verify its email and enable 2FA.
2. Register an application with a unique name and an appropriate application
   category. Set the OAuth redirect URL to exactly `http://localhost:3000` for
   the Twitch CLI's consent flow.
3. Record the client ID and generate a client secret. Keep the secret private;
   do not put it in source, screenshots, a demo video, or the broadcast.

These steps follow Twitch's [app registration guide](https://dev.twitch.tv/docs/authentication/register-app/).

## 3. Obtain a broadcaster user token

Install the official [Twitch CLI](https://dev.twitch.tv/docs/cli/), then configure
it for the application you just registered:

```powershell
twitch configure
twitch token --user-token --scopes 'user:read:chat user:write:chat'
```

Authorize **the broadcaster account**, not the separate viewer account. CLI
configuration must use the same client ID you pass to Recall. Copy the
**User Access Token** from the CLI result, not an app token or refresh token.
The command opens a browser for consent. Do this off camera: its result includes
credentials. [Official token command documentation](https://dev.twitch.tv/docs/cli/token-command/)

In the PowerShell window where you will launch Recall:

```powershell
$env:TWITCH_CLIENT_ID = Read-Host 'Twitch application client ID'
$env:TWITCH_ACCESS_TOKEN = Read-Host 'Broadcaster user access token' -MaskInput
python -m tubelive.cli serve --video 'C:\path\to\owned-recording.mp4' --replay --local-ai --twitch
```

This adapter intentionally uses the installed-broadcaster pattern: token user,
chat reader, chat sender, and target broadcaster are the same identity. It
validates the client ID and both scopes at startup and hourly. It does not
automatically refresh tokens; if the token expires or is revoked, stop the app,
obtain a new user token, and restart. No extra bot/moderation scopes are needed.
[Twitch's installed chatbot authentication](https://dev.twitch.tv/docs/chat/authenticating/)

## 4. Add the broadcast page to OBS

1. Add a Browser Source with URL `http://127.0.0.1:8765/broadcast`; set its size
   to your canvas, for example 1920 by 1080. Change the port if you launched with
   a different `--port` value.
2. Open `http://127.0.0.1:8765/` in your browser for the local operator controls.
   Use one broadcast source so there is one authoritative replay clock.
3. Confirm playback, source audio, frame capture, and the shared cube in OBS
   before streaming. The broadcast page's video clock advances incremental
   processing. Evidence playback uses a separate player and must not seek the
   source recording.
4. Start the replay from empty memory and broadcast through your normal OBS
   Twitch connection. Keep Twitch credentials and operator-only pages off air.

OBS [Browser Source](https://obsproject.com/kb/browser-source) supports a local
webpage including video and audio. Browser playback and audio routing still need
an actual OBS check on your machine. No OBS WebSocket setup, router changes,
port forwarding, tunnel, or public laptop endpoint is part of this MVP.

## 5. Test from another viewer account

Use another account you control, or an explicitly participating tester. The
broadcaster account's own questions and cube commands are ignored to avoid bot
loops; the local operator UI remains available for your own questions.

| Command | Behavior |
|---|---|
| `!recall Where is the red cup?` | Answers using evidence available when the question arrived. |
| `!recall @1:20 What changed here?` | Limits evidence to recording time 1:20, also capped by playback and processed memory. |
| `!cube next` / `!cube prev` | Changes the shared cube selection. |
| `!cube left` / `!cube right` | Rotates the shared cube through the local control callback. |
| `!recall disable` / `!recall enable` | Broadcaster/moderator only; disables or enables viewer cube controls. |

The local stream demo uses `/recall`; connected Twitch chat uses `!recall`.
`!ask` remains a question alias. `enable` and `disable` are reserved moderator
commands and cannot be used as the first word of a `!recall` question.

Questions have a 10-second per-user cooldown and at most one active question
plus one waiting. Shared cube actions have a 3-second channel cooldown. Replies
are public chat replies of at most 450 characters; selected visual evidence is
public in the broadcast. There are no private viewer answers or individually
controlled remote cube pages. Never give viewers a localhost link.

Twitch's playback delay differs between viewers. The answer's recording-time
label identifies its cutoff; it does not promise exact alignment with the frame
every viewer currently sees. Use the visible recording clock and `@m:ss` when
precise timing matters. Ordinary chat is not added to stream memory.

## Troubleshooting and teardown

- **Authorization required:** check that the client ID matches the token, the
  authorized user owns the test channel, and both chat scopes were granted.
  Studio now shows a separate sign-in warning when this happens; local recording
  processing can continue even while chat is disconnected. Obtain a new **User
  Access Token** with `twitch token --user-token --scopes 'user:read:chat user:write:chat'`,
  authorize the broadcaster account, update `TWITCH_ACCESS_TOKEN` in the launch
  terminal, and restart Recall. Use the Twitch token, not an OBS password or a
  refresh token. Starting a fresh replay rebuilds its memory from the beginning.
- **Disconnected/context gap:** the adapter reconnects with backoff and reports
  the gap. Twitch cannot replay missed chat; send the question again.
- **No public reply:** check local status and the channel's chat settings. A
  successful HTTP response can still report that Twitch did not send a message.
- **No processed evidence yet:** wait for a complete input window and local
  inference. Do not preload future evidence to make the demo appear faster.
- Stop the app and stream when finished. Remove the session token with
  `Remove-Item Env:\TWITCH_ACCESS_TOKEN`. Disconnect the app in Twitch's
  connection settings if you no longer need it.

The adapter stops on revoked authorization, rejects unexpected reconnect
domains, deduplicates EventSub message IDs, and separates model work and outgoing
replies from the EventSub reader. Its offline transport tests use no live token
and do not prove that a real channel broadcast has been tested.
