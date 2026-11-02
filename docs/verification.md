# Implementation verification — October 31, 2026

Latest recovery update: **172 backend tests passed**, production build passed,
and browser UI checks passed, including the lighter preset, preserved memory
on resume and visible coverage gaps. Added regressions cover stale-source
suspension, video backpressure, in-flight cancellation, transient and repeated
failures, missing first frames, bounded/exported coverage and partial completion.
The real five-minute check reached EOF with one declared visual-analysis hole
after CUDA ran out of memory. [Measurements and logs](performance-recovery-2026-10-31.md).

Implemented: local multimodal import and incremental replay, bounded evidence
memory, natural grounded replies with an optional source-quote mode, Three.js time cube, local file picker, evidence
player, shared controls, Twitch EventSub adapter, setup and launch scripts.
The local watch-page simulation includes labelled scripted demo chat, ordinary
chat messages, `/ask` replies with up to three moment links, separate bounded
moment playback, trimming, MP4 download, browser sharing with a download
fallback, and local-only links that expire on restart.

## Measured on this laptop

- NVIDIA GeForce RTX 5080 Laptop GPU, about 16 GB VRAM.
- Ollama 0.40.12 with the already installed local qwen3.5:9b Q4 model.
- Faster-whisper small, CPU int8, Python 3.12.
- An isolated loopback Ollama service had cloud features disabled. The real
  replay verification also blocked non-loopback outbound Python connections.
- A generated **32.05-second synthetic clip**, played at 1x, yielded 8 sampled
  visual observations and 15 total transcript/visual records.
- Peak observed replay processing lag: **17.69 seconds**; final lag: zero.
- A visual-only green-circle question returned observed-source citations in
  **4.62 seconds**. The explicit next-plan question returned the transcript quote.
- An unstated motive and unrelated outside-knowledge question both returned
  insufficient evidence. Before playback, a future-plan question also returned
  insufficient evidence. Restart produced a new empty run.
- There were no uncaught browser errors in that real-provider replay check.
- Separate full-recording import of the synthetic clip completed in **34.61
  seconds**. This is an offline-import measurement, not the replay lag metric.
- Initial cold vision startup was approximately 43 seconds. Warm individual
  vision checks were roughly 2–3 seconds. Startup time is not covered by the
  warmed-answer target.

The machine-readable replay measurements and answers are in
[local-smoke-results.json](local-smoke-results.json). The screenshot is actual
rendered UI using the synthetic clip, not a proposed mockup.

## Supplied boat recording

The supplied boat recording is **179.238 seconds** long. A repair pass recovered
**three automatic transcript segments** and retained **37 sampled visual
observations**. The question “What did he say about migratory birds?” returned
the spoken passage at **0.94–8.34 seconds** in **2.34 seconds**. These are results
for this recording and question, not a general accuracy or throughput estimate.

The original speech detector rejected the noisy audio. The repaired path uses
a **180–3800 Hz bandpass**, keeps voice activity detection enabled with a
**0.25 threshold**, and discards low-confidence segments. The recovered wording
is still imperfect and must be checked against the audio; the pass does not
establish that all speech in the recording was recognized.

## Automated checks

October 30 backend run: **143 tests passed**. The production viewer build and browser
contract checks passed, including three-match replies, independent moment tabs,
bounded playback, MP4 download, native-share failure fallback and source isolation.
The decoder scrub test also passed. A real boat moment link covered 0.94–60.94
seconds, and a two-second trim exported as a 2.002-second MP4.

Diagnostics verification: JSONL rotation, content/credential exclusion, retained
history export, browser event validation, active-job visibility and per-run metric
reset pass. Browser tests cover the diagnostics panel/download and fresh replay
toolbar Play. A real local answer logged 13.61 seconds total, including 7.21
seconds of model load and 5.78 seconds of generation. This is one cold-call
measurement, not a sustained throughput claim. Truncated structured output has
a tested one-retry limit. Vision now uses a compact retry for truncated or
malformed JSON; failed retries still stop without inventing observations.

Natural-language QA regression coverage includes broad current-context questions
without keyword overlap, latest-first evidence, speech versus on-screen text,
semantic subject/reason paraphrases, bounded fallback retrieval, explicit
quantities, spatial “next to”, preceding/following context, stale speech and
completed-window cutoffs. Interpreter failures preserve literal retrieval.
Chat displays the grounded answer before up to three moment links, with output
escaping and a short-message limit.

Real Qwen checks on October 30 used the retained 15.174-second memory from the
612.7-second recording. “whats happening” returned current visual evidence in
9.28 seconds, “What is he saying?” returned recent transcript passages in 3.48
seconds, and “Who is the speaker meeting?” retrieved the stated meeting passage
in 3.67 seconds. An unsupported lottery question returned uncertainty, and a
zero-second cutoff returned no evidence. These are individual local checks, not
an accuracy evaluation or sustained throughput benchmark. Those earlier checks
used memory from an interrupted run; they did not test ingestion recovery.

The subsequent concurrent-worker recovery check processed 1:00–2:00 of the
previously played recording in 61.14 seconds including startup. CPU speech and
GPU vision overlapped, prior evidence IDs remained present, and no new gap or
future evidence appeared. It is not a sustained 1× replay benchmark. Detailed
hardware findings and primary sources are in [local-performance.md](local-performance.md).
Regression tests cover bounded multi-minute backlog, EOF drain, retry without
memory deletion, concurrent workers, model selection/authentication and cache
reset. Browser checks cover model selection, retry controls, concurrent-work
display, growing cube length, bounded overview textures and precise frame scrub.

At the reported 1:23 interruption, the source was paused, processing was 3.75
seconds behind, the queue was empty and no model error was recorded. Later, a
different 612.7-second recording had a vision error near 0:30. Earlier logs cannot
establish the exact cause of either interruption. The new diagnostics distinguish
browser playback, source connection, model transport and structured-output errors.

The backend suite covers completed-window cutoffs, future evidence/track/entity
filtering, provenance, model-output validation, safe source excerpts, generic
plans, missing evidence, FTS queries, source lifecycle and queue fairness,
stale work, media EOF, HTTP origin/token checks, video ranges, upload handling,
Twitch commands, reconnects and run boundaries.

The browser suite checks actual Three.js canvas rendering, API session tokens,
source/evidence separation, JPEG source capture, bounded evidence seeking,
shared pause, keyboard timeline controls, 390-pixel layout and upload reset.
Browser contract tests use explicit test doubles; the separate local-replay
test uses the actual local models and real HTTP endpoints.

Run the current test count yourself:

```powershell
.venv\Scripts\python.exe -m pytest -q
Push-Location viewer
npm.cmd run build
npm.cmd run test:ui
Pop-Location
```

## Pending acceptance work

- One real boat recording has been supplied and checked as described above.
  The two-category evaluation still needs suitable recordings from distinct
  categories, a changed held-out sequence and the 20 human-labelled questions.
  The boat check and synthetic tests do not satisfy that acceptance gate.
- An authorized Twitch account/app token and real viewer chat round-trip.
  The transport/command code is implemented and tested with offline doubles;
  no live posts were made during implementation.
- Real OBS Browser Source video/audio and on-stream layout verification.
- Public YouTube demo, repository publication, Devpost submission and Codex
  build session ID. None has been published or submitted automatically.

Valid evidence IDs do not establish semantic correctness. Natural replies now
undergo a separate local claim check against their cited records. A code-level
gate resolves source IDs and completed windows, limits inference to prediction
questions with predictions enabled, and rejects visual-only claims presented
as speech. Relevant exact speech can be returned if a paraphrase is rejected.
The checker is also a model: unsupported details can still pass, and speech
recognition and visual descriptions can be wrong. Human-labelled real-footage
evaluation remains necessary; this is not a factual-accuracy guarantee.

## Test mode and conversational replies — October 31

Before the recovery update: **163 backend tests passed**, production TypeScript/Vite
build passed, and the browser UI suite passed, including the new test settings.
The final local-provider check ran four questions per model (4B and 9B): a
controlled current-context question, cautious prediction, real meeting lookup,
and real current-context question. All returned bounded source links. In the
controlled 4B example, the answer correctly described a planned Zeek/cafe/heist
discussion; the future cancellation record stayed excluded. The 9B model used
an exact relevant quote when its meeting paraphrase was rejected. Some replies
still omitted useful speech or added over-specific interpretation, so these
checks do not satisfy the human-labelled accuracy gate.

The settings panel configures the installed vision/answer model, downloaded
speech model, CPU/GPU speech device, thread count, frame interval/resolution,
reply style and cautious predictions. Providers are validated before old memory
is cleared. Each test gets a new run, and its content-free timing record is
retained locally. Browser checks cover preset selection, unavailable models,
GPU speech availability, starting a test, and downloading results.

New backend checks cover configuration validation, provider failure preserving
old memory, busy-session rejection, configured sampling cutoffs, source-linked
natural replies, rejected paraphrases, prediction opt-out/hedging, earlier
context labels, complete source windows, and invalid verification output.
FTS stemming now matches grammatical variants such as “meeting” and “meet.”

Short real-provider comparisons used the supplied recording with Qwen3.5 4B/9B
and Whisper base/small. Q&A used the same already processed memory for both
models; speech and vision timing samples were measured separately. These are
smoke checks, not a full replay benchmark or the planned held-out evaluation.
See [local-performance.md](local-performance.md) for measurements.


Earlier on 2026-10-29: 99 backend tests passed after disconnected-source recovery and exact
visual/text reuse changes. Browser checks pass for the watch-page layout,
separate studio navigation, chat, clip downloads, responsive layout, and source
isolation. Exact-repeat reuse is tested; no moving-scene speedup is claimed.
