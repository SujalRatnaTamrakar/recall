# Stream-wide memory and creator context

The October 31 upgrade adds history selection, meaning-based retrieval and
creator labels. It keeps the full processed record archive while choosing a
bounded set of relevant sources for each Qwen request. It does not feed every
frame or every word from a long stream into one prompt.

## How to use it

1. Load the recording in Studio.
2. Open **Stream context**. Set the streamer, controlled character if applicable,
   activity and relevant cast names or aliases. Optionally enter a clearer
   recording title and description/background. Otherwise the original filename
   supplies the title automatically. Save.
3. Start replay in the Viewer. Ask `/recall what's happening?`, a specific historical
   question, or `/recall summarize the entire stream so far`.
4. Inspect the citations and **What the bot used** to see which original excerpts,
   frames and creator labels informed the answer.

For a known speaker, select a transcript excerpt in Stream context and label it.
That label applies only to that excerpt. Automatic speaker diarization, voice
recognition, person tracking and face recognition are not enabled. An unlabelled
voice remains “the speaker”; a cast list alone is not permission to assign speech
to its members. Creator-confirmed character names can replace a generic controlled
avatar description when the visual evidence explicitly establishes that role.

Context changes are versioned at the available processing time. An earlier-cutoff
question cannot use a name learned later. Static creator information is remembered
for the same source file on replay restart; individual excerpt labels reset.

## Visual answers before speech is available

Frame observation and Q&A both receive the recording title, description, channel
and activity. A title supplies the broadcast topic, not proof of every event it
mentions. The frame reader still describes visible details. The answer may make
a hedged interpretation of the present scene, marked `CONTEXTUAL` in answer data,
when both the background and cited frames support it. These interpretations do
not require future predictions to be enabled. Similar frames should contribute
to one coherent description, rather than repeated captions.

For example, mission coverage and visible monitoring consoles can support
“Staff appear to be monitoring the mission.” They do not establish that everyone
is an engineer, works for a specific agency, or is preparing a launch. A title
cannot override contradictory footage. Source text and metadata are untrusted
data, never instructions. Question cutoffs apply to saved context versions too.
An exact-frame cache entry is reused only if the background is unchanged.

Transcript questions use a separate factual contract: a stated distance, clock
time or announced plan is a reported fact, not a visual interpretation. Search
also brings in adjacent speech fragments within two seconds of top transcript
hits, keeping their original IDs and source-window boundaries. Selected excerpts
are shown to the model in chronological order; audio coverage gaps and explicit
search windows limit which neighboring fragments are eligible. Verification
briefly assesses support before selecting accepted claims.

November 2 transcript regression check (Qwen 3.5 9B, existing source evidence
through 4:00): the model recovered 152 kilometers, the announcement that the next
burns were about an hour away, and the phase-adjust burn at 6:50 a.m. Pacific.
An unsupported Mars-landing question returned uncertainty with no citations.
These four checks are examples, not a general accuracy score. The description
“phase 2” itself has not been established as an alias for the phase-adjust burn.

November 2 verification: Qwen 3.5 4B, using only the existing recording's evidence
through 0:50 and three eligible original frames, answered “During this SpaceX
Dragon spacecraft tour, workers appear to be monitoring operations at computer
stations.” Its citations were at 0:20 and 0:30, with no later transcript supplied.
This is one observed example, not a general accuracy measurement. Automated
regressions cover metadata versioning, title preservation on replay restart,
visual-only support, uncertainty, future boundaries and cache invalidation.

## How history reaches the answer

- Speech and visual descriptions remain timestamped in the source archive.
- SQLite word search is combined with an optional local MiniLM meaning index.
  This allows a question about “migratory birds” to retrieve a statement about
  birds leaving northern nesting grounds for warmer countries.
- Every completed minute, a lower-priority job writes short, source-linked story
  notes. Important spoken introductions, plans and changes are also retained as
  exact source anchors. Repeated unchanged scenery is reduced in these notes.
- Current-state questions include recent activity plus relevant earlier plans,
  introductions and changes. A stated cancellation can supersede the older plan.
- “Entire stream” and “so far” summaries cover the whole available history;
  explicitly requested recent windows stay limited to those windows.
- The model may inspect up to three selected original frames again. Those frames
  are eligible past evidence, not arbitrary later footage.
- Each answer uses a bounded input (up to 32 selected records and about 10,000
  characters of source context). Draft claims are checked against original
  evidence, followed by code checks for IDs, times and unsupported speaker naming.

Notes and verification can still be wrong: they are produced by a small local
model, and same-model verification is not an independent truth guarantee. Missing
or ambiguous evidence should produce uncertainty. Predictions remain labelled
inferences; they are not knowledge of future events.

## Local setup and storage

`Setup.ps1` prepares the CPU meaning-search model. For an existing environment:

```powershell
.venv\Scripts\python.exe -m tubelive.cli prepare-semantic
```

This explicit setup downloads a pinned quantized MiniLM ONNX model and tokenizer
into `.tubelive/semantic-model/`. Runtime search requires no internet. The model
uses two CPU threads and does not compete with Qwen for GPU memory. If its files
are unavailable, the app falls back to word search; answer details identify the
active search method. `Setup.ps1 -SkipSemanticDownload` skips the setup download.

Qwen still handles vision, story notes and answers. Whisper transcribes on its
separate audio worker. Story jobs yield to pending questions and frame jobs;
failed story enrichment is recorded and raw evidence remains available.

Normal-launch paths:

- `.tubelive/runs/<run-id>/memory.json`: archived evidence, profile versions and
  story notes, saved as the replay progresses.
- `.tubelive/profiles/`: source-specific creator setup.
- `.tubelive/semantic-model/`: local semantic model files.
- Diagnostic reports retain timing and job metadata. The answer's source excerpts
  are visible in the local interface; they are not added to shared debug logs.

Old memory bundles still load. New fields are additive. Export through Studio to
keep the memory bundle together with its available frame assets.

## Verification and measured limits

Regression checks cover early introductions recalled late in a stream, revised
plans, whole-stream recap routing, alias cutoff rules, unknown speaker attribution,
source-ID validation, future-frame exclusion, conversation references, failed
story updates and old/new bundle compatibility.

A 90-second replay on this laptop with Qwen 3.5 4B and Whisper base completed all
90 seconds of audio, visual processing and story notes. Peak processing delay was
11.05 seconds; the pipeline drained 4.52 seconds after source playback finished.
The question took 2.015 seconds and inspected three original frames. There were
no coverage or story-update gaps in that check. Browser video decoding was excluded,
so these are a bounded pipeline check, not a general live-throughput guarantee.

A later repeat of the synthetic Qwen checks showed substantial timing variation:
answers ranged from 2.25 to 267.59 seconds. Ollama itself logged a 260-second
request during the slow answer. The cause of that stall was not isolated in this
check; the faster replay result must not be treated as a guaranteed answer time.
The app's normal question timeout can reject an answer that takes this long.

Synthetic local-model checks exercise historical bird speech, a cancelled plan,
an earlier cutoff, unsupported details and a red-to-blue visual change. These
fixtures were used during development, not as a held-out accuracy benchmark.
The planned human-labelled questions across two real content categories remain
necessary before claiming broader accuracy.
