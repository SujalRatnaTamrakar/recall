"""Segmentation / summarisation helpers shared by the query tools.

In v0 these operate over hand-authored (or SRT-imported) records.  In v1 the
same helpers will consume real pipeline output: scenes from a VLM pass,
chat buckets from the EventSub chat log, transcripts from Whisper.
"""
from __future__ import annotations

from collections import Counter

from .schema import Record
from .store import StreamMemory


def scenes_in(memory: StreamMemory, t0: float, t1: float) -> list[Record]:
    return [r for r in memory.by_window(t0, t1, kinds={"scene"})]


def chat_buckets(memory: StreamMemory, bucket_s: float = 60.0) -> list[tuple[float, int]]:
    """(bucket_start, message_count) per bucket — the raw chat-velocity signal."""
    counts: Counter[int] = Counter()
    for r in memory.of_kind("chat"):
        counts[int(r.t_start // bucket_s)] += 1
    if not counts:
        return []
    last = max(counts)
    return [(i * bucket_s, counts.get(i, 0)) for i in range(last + 1)]


def chat_spikes(memory: StreamMemory, bucket_s: float = 60.0,
                factor: float = 2.0) -> list[tuple[float, int]]:
    """Buckets whose chat volume is >= ``factor`` x the mean bucket volume."""
    buckets = chat_buckets(memory, bucket_s)
    if not buckets:
        return []
    mean = sum(c for _, c in buckets) / len(buckets)
    if mean == 0:
        return []
    return [(t, c) for t, c in buckets if c >= factor * mean and c >= 4]


def describe_record(r: Record) -> str:
    """One short human-readable line for a record (used in summaries)."""
    if r.kind == "scene":
        cap = getattr(r, "caption", "") or r.text
        loc = getattr(r, "location", "")
        return f"{cap} ({loc})" if loc else cap
    if r.kind == "transcript":
        spk = getattr(r, "speaker", "unknown")
        return f'{spk}: "{r.text}"'
    if r.kind == "stream_event":
        return r.text or f"{r.type} event"
    if r.kind == "game_event":
        return r.text or f"{r.type} event"
    if r.kind == "chat":
        return f'chat: {getattr(r, "user", "")}: "{r.text}"'
    return r.text or r.type


def summarise_records(records: list[Record], limit: int = 6) -> list[str]:
    """Deterministic extractive summary lines, in time order.

    Priority: scenes first, then game/stream events, then transcript lines;
    chat is summarised as volume, not quoted line-by-line.
    """
    prio = {"scene": 0, "game_event": 1, "stream_event": 2, "transcript": 3}
    ordered = sorted(records, key=lambda r: (r.t_start, prio.get(r.kind, 9)))
    lines: list[str] = []
    seen_scenes: set[float] = set()
    for r in ordered:
        if r.kind == "chat":
            continue
        if r.kind == "scene":
            if r.t_start in seen_scenes:
                continue
            seen_scenes.add(r.t_start)
        lines.append(describe_record(r))
        if len(lines) >= limit:
            break
    return lines
