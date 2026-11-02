# Local processing investigation — updated October 31, 2026

**Latest:** [recovery changes and the five-minute check](performance-recovery-2026-10-31.md).
That report corrects the interpretation of run `969e12…`, includes GPU clock/power
telemetry, and records the final-frame out-of-memory failure honestly. The short
comparisons below used the earlier settings; the lighter preset now uses brief
768-pixel descriptions every ten seconds and four CPU threads per model.

## Lightweight test profile and short comparison

**Test mode** now provides Qwen3.5 **4B + Whisper base** as the lightweight
preset and **9B + small** as the comparison preset. Both models are installed
on this machine. Speech runs separately on the CPU; video and answers use
the selected local Ollama model. The 4B model was observed fully on the GPU
with an 8,192-token context and approximately 3.4 GB loaded allocation.

A short check on the supplied recording produced these elapsed times:

| Operation | 4B + base, 720px frames | 9B + small, 960px frames |
|---|---:|---:|
| Speech, first 10-second window | 2.27s | 14.92s |
| Speech, next 10-second window | 3.13s | 5.16s |
| First video frame, including model loading | 23.81s | 10.11s |
| Next video frame | 2.55s | 4.08s |
| Six questions on the same existing memory | 2.06–11.89s each | 6.64–14.47s each |

These are two audio windows, two frames and six questions per configuration,
run sequentially. Cold loads, different frame resolutions and background
computer activity affect the comparison. The faster warm samples make the
lightweight profile worth trying; they do not establish sustained throughput
or equal accuracy. Answers included a correct Jason meeting statement and
honest uncertainty about an unrelated lottery question. Review also exposed
over-specific paraphrases and a rejected correct-topic answer. Prompt and
source-quote fallback changes followed; semantic quality is still uncalibrated.

Each question can involve intent interpretation, drafting and a separate
claim-checking call. Broad current-context questions skip interpretation.
This checking adds latency, so the UI records answer time separately. No
additional orchestration model or autonomous agents are required.

## Earlier processing-gap investigation

The observed stop was a failed vision response, not a one-minute lag cutoff.
The diagnostic replay at 65.19 seconds generated truncated JSON twice: first
320 tokens, then 1,000. The application stopped after 21.08 seconds, with only
nine queued jobs. Earlier completed memory remained valid.

There was a separate overload guard at more than 32 queued jobs. That guard is
now replaced by bounded scheduling: at most 16 pending speech windows and 16
pending vision samples. Workers refill those queues from the owned recording
only up to the acknowledged playback clock. A long backlog is delay, not a
reason to discard memory or require a restart. Source jumps and actual model
failures remain explicit errors.

## Two workers with one shared memory

```text
Played audio → Whisper small / CPU → timestamped spoken passages ┐
                                                              ├→ bounded memory → answers + source links
Played frames → selected Ollama model / GPU → observations      ┘
```

The CPU speech worker and GPU vision worker now run concurrently. Question
interpretation and evidence selection share the vision model's queue, so only
one Ollama inference runs at a time. An answer sees the earliest of the requested
cutoff, playback position, completed audio and completed vision. Faster speech
cannot expose evidence past slower vision, or vice versa.

This is deterministic scheduling, not two autonomous chat agents negotiating
with each other. For these fixed tasks, another reasoning/orchestration model
would add inference without creating additional compute capacity. Ollama
supports concurrent requests, but they consume additional context memory;
multiple models also need enough available memory. That makes CPU speech plus
GPU vision the first option to test on this machine. [Ollama concurrency FAQ](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests)

## Hardware and actual measurements

- RTX 5080 Laptop GPU, approximately 16 GB VRAM; Core Ultra 9 275HX, 24 cores;
  approximately 32 GB system RAM.
- Ollama reported Qwen3.5:9b at 100% GPU, with all 34 model layers offloaded,
  approximately 5.8 GB loaded allocation, an 8,192-token context and Flash
  Attention enabled. GPU allocation varies with context and other applications.
- Speech remains faster-whisper small, CPU int8, eight threads. No GPU speech
  setup or automatic cloud fallback was introduced.
- An isolated recovery check restored the earlier recording's memory through
  1:00 and caught up to its previously played 2:00 position in **61.14 seconds**.
  The workers overlapped, no new gap occurred, and old evidence was preserved.
  Speech work totalled 61.11 seconds; vision work totalled 56.02 seconds. Those
  totals overlap and must not be added as wall time. This was a cold recovery
  check, with browser QA also running briefly; it is not a sustained 1× benchmark.

Vision responses now have explicit short schema limits. If JSON is truncated
or invalid, the same frame receives one compact description retry. If that also
fails, the timestamp does not advance. **Retry processing** preserves memory
and refills missing work from the source file, with playback paused.

## Model selection

Open **Local models**, choose an installed vision-capable Ollama model, and
press **Use model** while playback and frame work are idle. The selection applies
to this server session; existing observations retain their original source
information. Switching clears the previous frame-description cache and unloads
the old model from this app's Ollama service. No model is downloaded implicitly.

The installed compatible choices were Qwen3.5:9b, Qwen3.8-27B GGUF and
Qwen3.6:35b-a3b. The last two have larger model files than this GPU's available
VRAM and are poor initial choices for reducing delay. Their vision capability
was checked from local metadata; their throughput and accuracy were not tested.
Text-only and cloud entries are shown as unavailable for the combined video
and answer role. Discovery uses Ollama's local model metadata API.
[Ollama model listing](https://docs.ollama.com/api/tags)

## Next experiments, in order

1. **GPU speech recognition:** benchmark Whisper small on CUDA alongside vision,
   retaining the same recording and accuracy checks. Speech was the slowest
   worker in the recovery check. Faster-whisper supports GPU int8/float16 and
   batching, but needs compatible cuBLAS/cuDNN libraries. Its published batch
   benchmarks are not a promise for short live windows on this laptop. Queuing
   a large batch also adds latency. [Faster-whisper documentation](https://github.com/SYSTRAN/faster-whisper#gpu)
2. **Less vision work:** compare a smaller vision model, reduced input resolution,
   and scene-change sampling with a periodic maximum interval. Validate brief
   actions and readable signs before changing defaults. Similar boat scenery
   alone is insufficient proof that a frame contains nothing new. Exact frame
   reuse is already supported; approximate deduplication is not enabled.
3. **Warm-up and scheduling:** measure cold startup separately, then test a
   continuous replay with questions. Keep retries bounded and display audio,
   vision and answer timing separately. A longer delay allowance cannot increase
   throughput; graceful catch-up prevents delay becoming a terminal failure.
4. **vLLM only after a comparative benchmark:** it is a possible serving backend,
   not a fix for malformed JSON or a blocked CPU worker. Its official GPU setup
   targets Linux; Windows normally requires WSL. The native Windows project
   currently uses Ollama, and no vLLM migration was performed.
   [vLLM installation requirements](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/#requirements)

The growing cube uses a fixed spatial scale of 15 seconds per unit. Its outline,
timeline planes, result slabs and topic ribbons extend with completed memory.
Only 32 overview textures are retained, while the independent video decoder
still supports precise frame scrubbing. Long recordings therefore produce a
longer rectangular volume without loading every frame into GPU memory.
