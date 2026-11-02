# Processing recovery and five-minute check

## What the original run establishes

The retained diagnostics for `969e123a06fd4b1493d9aa299395184f` confirm the
slowdown. Successful vision jobs had a 2.00-second median in the first 25 video
seconds, versus 10.84 seconds for samples from seconds 35–200. Speech windows
in those ranges had medians of 3.28 and 15.60 seconds respectively.

Speech and vision were already concurrent: the first audio window and the frame
at 10.335 seconds both started at session elapsed 179.922 seconds. Summing both
workers' processing durations overstates wall time. The slower worker still
determines how quickly combined memory becomes available.

There are **zero `job_failed` events** for that run. At session elapsed 435.875
seconds, the source disconnected at playhead 240.014 seconds. When it returned
ahead of the permitted clock, the old forward-seek guard paused the run. The
subsequent burst of `success:false` job endings came from work being discarded
after entering that gap. The 97.84-second audio job was also discarded, not
reported as a timeout. Ollama's HTTP timeout does not impose a total timeout
on Whisper transcription. The former logging did not distinguish these outcomes.

The original logs cannot identify the sixfold slowdown's hardware cause. CPU
temperature/frequency was not measured, GPU power/clock readings were absent,
and GPU utilization was cached for 20 seconds. Windows reported the Balanced
power scheme during inspection; that alone does not establish causation.

## Changes

- Hardware sampling about every two seconds: GPU clocks, memory clock, watts,
  reported power limit, clock-event bitmask, CPU utilization and AC connection.
- Explicit completed/cancelled/failed outcomes and separate queue depths.
- Suspend queued processing when a playing source loses its heartbeat. In-flight
  work may finish; reconnection refills from saved progress without duplicates.
- Forward jumps and **Resume from playhead** preserve earlier memory and record
  missing analysis. An epoch guard prevents older in-flight work from committing
  after skipping. Backward seeks still create a new run.
- Optional **Stay near live** mode retains at most three pending video frames.
  It preserves the independent audio worker. Dropped frames and isolated failed
  jobs become coverage holes; three consecutive lane failures pause for inspection.
- Brief video descriptions: one short sentence, 64 output tokens including JSON,
  with a 96-token retry only on truncation. A literal 40-token total risks cutting
  off JSON. Detailed descriptions remain available.
- Lighter preset: Qwen3.5 4B, Whisper base, four CPU threads for each, 768-pixel
  samples every ten seconds, brief descriptions and live catch-up.
- Coverage holes survive bounded snapshots and exports, appear beside answers,
  and prevent test results from claiming complete analysis.

NVIDIA documents distinct flags for idle clocks, software power caps, thermal
slowdown and hardware power braking. A low clock by itself cannot distinguish
those causes. [NVIDIA clock-event reference](https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlClocksEventReasons.html)

## Five-minute real-provider check

The first 300 seconds of the supplied recording were re-encoded to 720p/30fps,
approximately 3.5 Mbps H.264 plus 128 kbps AAC: **129.24 MiB**. The original is
untouched. The installed FFmpeg NVENC encoder required API 13.1 while the driver
provided 13.0; the copy was made with CPU x264 instead of changing the driver.

The isolated run used the lighter settings above and a 1× source clock, with
the model warmed first. It ran the actual two workers and asked two questions.
It did **not** include browser playback/decoding, so this is not a full watch-page
performance guarantee. The temporary model service was stopped after the run.

| Measurement | Result |
|---|---:|
| Recording duration | 300.00 s |
| Total replay plus draining time | 311.33 s |
| Peak observed processing lag | 15.70 s |
| Final processing lag | 0 s |
| Successful vision jobs | 30 |
| Successful audio windows | 30 |
| Median successful vision job | 0.65 s |
| Median successful audio window | 1.05 s |
| Two answer times | 2.19 s / 1.86 s |
| Failed jobs | 1, final vision sample |
| Declared missing visual-analysis interval | 290–300 s |

This was **finished with partial coverage**, not a flawless complete-analysis
run. The final vision request returned HTTP 500 and Ollama explicitly logged
`CUDA error: out of memory`. Around elapsed 298–300 seconds, total GPU memory
use jumped from 7,592 to 14,815 MiB and available system RAM fell from 7,806 to
17.8 MiB, later reaching 8 MiB. These are system-wide readings; the responsible
consumer was not established. The app recorded the hole and finished without
getting trapped in a permanent gap.

AC power was connected in all 149 samples. Five samples reported the software
power-cap bit, with GPU clocks still around 2,325–2,587 MHz. This does not establish
power capping as the cause of the original sixfold slowdown. The new run demonstrates
lower workload and recovery under a real failure, while exposing memory pressure
as a concrete concern for subsequent tests. Close competing heavy workloads before
the next comparison; do not infer that the laptop cannot run the smaller models.

Local artifacts (excluded from the source archive):

- `.tubelive/performance-test-5min-720p.mp4`
- `.tubelive/performance-check-2026-10-31/logs/diagnostics.jsonl`
- `.tubelive/performance-check-2026-10-31/ollama.log`
- `.tubelive/performance-check-2026-10-31/result.json`

The raw result was generated before the final result-label change: its legacy
`complete:true` means the processing cursor reached EOF. Its explicit
`coverage_gaps` and failed-job count retain the failure. New test results use
separate `finished` and `complete` fields to avoid that ambiguity.
