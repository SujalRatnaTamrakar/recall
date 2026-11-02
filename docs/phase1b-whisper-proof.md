# Historical baseline proof — automatic transcription (2026-10-26)

This is the supplied baseline's historical record. Current implementation and
measurement results are in [verification.md](verification.md).

Goal: prove the video-only pipeline works on a *real* audio track with no
hand-supplied transcript: video → ffmpeg audio → faster-whisper → memory
bundle → query engine answers.

## Setup
- Test video: `examples/narration/narration.mp4` (64 s). Narration was
  synthesized with ffmpeg's `flite` filter (voice `slt`) — a streamer-style
  first-person script of the Jean Paul storyline (plan → bank → chase →
  stash → Marty's split → pull-over → stated Paleto plan). The picture is a
  test pattern; the *audio* is the test subject. A real VOD is still the
  next corpus step — this proves the provider chain and the gold queries.
- Environment: venv at `tube-live/.venv`, packages pinned in
  `requirements-whisper.txt`. **Pin that matters:** PyAV 19 removed the
  `metadata_errors` kwarg that faster-whisper 1.2.1 passes to `av.open`;
  `av==14.2.0` works. First ingest attempt failed on exactly this and the
  failure was recorded as a meta notice, not a crash — by design.
- Model: faster-whisper `tiny`, CPU, float32.

## Gold vs recovered (tiny model, robotic TTS voice — a hard case)
| Gold line | Recovered | Key terms kept |
|---|---|---|
| "tonight we rob the Fleeca bank… Marty is bringing the thermite" | "tonight we are off the flea cabin con the boulevard, Marty is bringing the thermite" | Marty, thermite |
| "inside the bank now, thermite on the vault door, grab the cash trays" | near-verbatim ("cash trace") | bank, thermite, vault |
| "Cops are outside… back alley, getaway car" | near-verbatim | cops, alley, getaway car |
| "three police units behind us, the chase is on" | "free Billy's units behind us, the chase is on" | chase |
| "lost them near the canals, stash the car, lay low" | "near the canels, stash the car, lay below" | stash, car |
| "Marty, here is your split from the bank job" | near-verbatim | Marty, bank job |
| "driving my friend to the hospital, I swear" | verbatim | hospital |
| "After this we hit Paleto… Paleto Bay bank is next" | "after this we hit Palio… Palio Baybank is next" | plan structure, "Bay bank" |

Named-entity spelling is the main loss (Fleeca→"flea cabin", Paleto→"Palio").
Mitigations for real VODs: larger model (base/small) on GPU or sampled CPU,
plus an entity alias/fuzzy-match layer in the planner (already alias-aware
for chat-provided names).

## Gold-answer check on the auto-generated bundle (no hand edits)
- "where are they going next?" → **STATED**, confidence 0.90, quoting the
  0:54 line with the Paleto plan structure intact.
- "when did they rob the bank?" → **0:08–0:16**, the vault-door segment.
- "what happened in the last minute?" → recap covering plan, bank, escape.

## Still open for Phase 1b
- Real GTA RP / Valorant VOD clip as corpus (needs Sujal's pick) with
  hand-written gold answers, base-model run, and a VLM scene-caption
  provider feeding SceneSegments (currently interface/stub level).
