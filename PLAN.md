# Recall implementation status

## Implemented

- General-purpose local stream memory: speech, sampled visual observations,
  explicit source windows, evidence IDs and run-scoped public snapshots.
- Find moments, explain observed sequences through source excerpts, recap
  windows and retrieve already-spoken plans. Missing support produces unknown.
- A Three.js frame/time cube with search highlights, topic-term recurrence,
  orbit controls, a synchronized keyboard timeline and an independent player.
- Actual local Qwen3.5 vision and source selection, CPU int8 speech processing,
  safe SQLite FTS5 retrieval and a controlled inference queue.
- Empty-to-growing replay memory, source ownership, pause/restart, bounded
  jobs, stale-answer cancellation, gap reporting, local upload and frame export.
- Twitch installed-app EventSub chat connector, public replies and shared cube
  commands, reconnect/authentication handling and moderator control switches.
- Windows setup/launch, an automatically processed synthetic example,
  automated backend/browser tests, measured local replay and submission docs.

## Acceptance still pending

- Two permitted real recordings from distinct categories and a changed held-out
  sequence; the user will supply these later.
- The 20 human-labelled questions including six unsupported/ambiguous cases.
- Real Twitch authorization/viewer chat and real OBS Browser Source audio/video.
- Repository/video publication and actual hackathon submission/session ID.

See [verification](docs/verification.md) for measured results and limitations,
[Twitch setup](docs/twitch-setup.md), and the
[demo/submission checklist](docs/demo-and-submission.md).

## Deferred

Arbitrary broadcaster live capture, independently controlled remote cubes,
automatic enforcement, physical/identity tracking, exact game statistics,
automatic publishing and public Extension release.
