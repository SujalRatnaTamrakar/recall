"""Query engine: ``ask(memory, question) -> Answer``.

Rule-based planner (no LLM, no API key in v0).  The planner *classifies* the
question and dispatches to deterministic tools — the same discipline as the
original Tube, where an LLM planned geometry tool calls and code did the
measuring.  Swapping this planner for an LLM later changes only the
classification step; the tools and the Answer contract stay.

Every Answer carries citations (timestamps) and a ``tool_trace`` — the list
of tools that ran, with args and hit counts.  The trace is a product
feature: viewers/mods can audit *how* an answer was produced.
"""
from __future__ import annotations

import re
import math
from dataclasses import dataclass, field

from .schema import Record, mmss, tokenize
from .segment import chat_spikes, describe_record, summarise_records
from .store import StreamMemory

# ---------------------------------------------------------------------------
# Answer contract
# ---------------------------------------------------------------------------

@dataclass
class Citation:
    t: float
    label: str
    t_end: float = 0.0
    evidence_id: str = ""
    source_type: str = "unknown"
    excerpt: str = ""
    frame_url: str | None = None

    def to_dict(self) -> dict:
        return {"t": self.t, "mmss": mmss(self.t), "label": self.label,
                "t_end": self.t_end, "evidence_id": self.evidence_id,
                "source_type": self.source_type, "excerpt": self.excerpt,
                "frame_url": self.frame_url}


@dataclass
class Clip:
    t_start: float
    t_end: float
    label: str

    def to_dict(self) -> dict:
        return {"t_start": self.t_start, "t_end": self.t_end,
                "label": self.label}


@dataclass
class Answer:
    summary: str
    query_type: str
    citations: list[Citation] = field(default_factory=list)
    tool_trace: list[str] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    confidence: float | None = None  # Legacy fixture scores are not calibrated probabilities.
    source_label: str | None = None       # "STATED" | "INFERRED" for NEXT
    run_id: str = ""
    through_s: float = 0.0
    status: str = "answered"
    coverage_gaps: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"summary": self.summary, "query_type": self.query_type,
                "confidence": self.confidence, "source_label": self.source_label,
                "run_id": self.run_id, "through_s": self.through_s, "status": self.status,
                "citations": [c.to_dict() for c in self.citations],
                "tool_trace": list(self.tool_trace),
                "clips": [c.to_dict() for c in self.clips], "coverage_gaps": self.coverage_gaps,
                'context': self.context}


def _cite(r: Record, label: str | None = None) -> Citation:
    frame_url = r.payload.get("frame_url")
    if (not isinstance(frame_url, str) or not frame_url.startswith(("/api/frames/", "/frames/"))
            or ".." in frame_url or "\\" in frame_url or "?" in frame_url or "#" in frame_url):
        frame_url = None
    return Citation(t=float(r.t_start),
                    label=label or describe_record(r)[:90], t_end=float(r.t_end),
                    evidence_id=r.evidence_id, source_type=r.provenance,
                    excerpt=describe_record(r)[:1400], frame_url=frame_url)


def _clip_around(r: Record, pad: float = 10.0, duration: float | None = None) -> Clip:
    end = r.t_end
    return Clip(t_start=max(0.0, r.t_start - min(pad, 5.0)), t_end=end,
                label=describe_record(r)[:60])


# ---------------------------------------------------------------------------
# Tools (deterministic, pure functions over StreamMemory)
# ---------------------------------------------------------------------------

_EVENT_SYNONYMS = {
    "robbed": ["rob", "robbery", "bank", "heist"],
    "rob": ["rob", "robbery", "bank", "heist"],
    "robbery": ["rob", "robbery", "bank", "heist"],
    "chase": ["chase", "pursuit", "police"],
    "chased": ["chase", "pursuit", "police"],
    "arrested": ["arrest", "arrested", "cuffed"],
    "escaped": ["escape", "escaped", "getaway"],
}


def expand_event_terms(text: str) -> list[str]:
    terms: list[str] = []
    for tok in tokenize(text):
        terms.append(tok)
        for syn in _EVENT_SYNONYMS.get(tok, []):
            if syn not in terms:
                terms.append(syn)
    return terms


def find_anchor_event(memory: StreamMemory, terms: list[str],
                      trace: list[str]) -> Record | None:
    """Best scene/transcript matching the event terms (the anchor moment)."""
    query = " ".join(terms)
    raw_hits = memory.search(query, kinds={"scene", "transcript", "game_event"})
    trace.append(f"find_anchor_event(terms={terms}) -> "
                 f"{len(raw_hits)} candidates")
    if not raw_hits:
        return None
    term_set = set(terms)
    def adjusted(rs: tuple[Record, float]) -> float:
        r, score = rs
        # A scene whose *activity* names the event (bank_robbery,
        # police_chase) is the event itself, not talk about it — this is
        # what stops the 'plans to rob the bank' scene winning over the
        # actual robbery scene.
        activity_tokens = set(tokenize(getattr(r, "activity", "") or ""))
        bonus = 2.0 * len(activity_tokens & term_set)
        if r.type in term_set:
            bonus += 1.0
        return score + bonus
    hits = sorted(raw_hits,
                  key=lambda rs: (-adjusted(rs),
                                  0 if rs[0].kind == "scene" else 1,
                                  rs[0].t_start))
    anchor = hits[0][0]
    trace.append(f"anchor = {describe_record(anchor)[:70]!r} @ {mmss(anchor.t_start)}")
    return anchor


def entity_timeline(memory: StreamMemory, entity_name: str, after: float,
                    trace: list[str]) -> list[Record]:
    ent = memory.resolve_entity(entity_name)
    if ent is None:
        trace.append(f"entity_timeline(entity={entity_name!r}) -> unresolved")
        return []
    recs = memory.by_entity(ent, after=after)
    trace.append(f"entity_timeline(entity={ent.name!r}, after={mmss(after)}) "
                 f"-> {len(recs)} records")
    return recs


_PLAN_PATTERNS = [
    r"after this", r"next we", r"we'?re going to", r"going to",
    r"let'?s go to", r"head(?:ing)? to", r"next up",
    r"tomorrow we", r"plan is",
    r"\b(?:i|we)(?:'ll| will| shall| plan to| intend to)\b",
    r"\bnext[, :]?\s+(?:i|we)\b",
]


def find_stated_plans(memory: StreamMemory, trace: list[str],
                      after: float | None = None) -> list[Record]:
    """Transcript lines that explicitly state a future plan."""
    plans = []
    for r in memory.of_kind("transcript"):
        if after is not None and r.t_start < after:
            continue
        low = r.text.lower()
        if any(re.search(p, low) for p in _PLAN_PATTERNS):
            plans.append(r)
    trace.append(f"find_stated_plans(after={mmss(after) if after else '0:00'})"
                 f" -> {len(plans)} plan statements")
    return plans


# ---------------------------------------------------------------------------
# Per-type handlers
# ---------------------------------------------------------------------------

def _answer_recap(memory: StreamMemory, t0: float, t1: float,
                  now: float) -> Answer:
    trace = [f"classify -> RECAP_WINDOW [{mmss(t0)}-{mmss(t1)}]"]
    # A record belongs to the recap if its midpoint lies in the window
    # (prevents a scene that merely ends at the window edge leaking in).
    recs = [r for r in memory.by_window(t0, t1) if r.mid >= t0]
    trace.append(f"by_window({mmss(t0)}, {mmss(t1)}) -> {len(recs)} records")
    lines = summarise_records(recs, limit=5)
    spikes = [(t, c) for t, c in chat_spikes(memory) if t0 <= t <= t1]
    if spikes:
        trace.append(f"chat_spikes -> {len(spikes)} spike bucket(s) in window")
    chat_n = len([r for r in recs if r.kind == "chat"])
    if not lines:
        summary = (f"Nothing notable is recorded between {mmss(t0)} and "
                   f"{mmss(t1)}.")
        return Answer(summary=summary, query_type="RECAP_WINDOW",
                      tool_trace=trace, confidence=0.4)
    summary = (f"Between {mmss(t0)} and {mmss(t1)}: " +
               "; ".join(lines[:3]) + ".")
    if spikes:
        summary += f" Chat spiked ({max(c for _, c in spikes)} msgs/min)."
    citations = []
    clips = []
    for r in sorted(recs, key=lambda r: r.t_start):
        if r.kind in ("scene", "game_event", "stream_event", "transcript"):
            citations.append(_cite(r))
        if r.kind == "scene":
            clips.append(_clip_around(r))
        if len(citations) >= 5:
            break
    return Answer(summary=summary, query_type="RECAP_WINDOW",
                  citations=citations, tool_trace=trace, clips=clips[:3])


def _answer_after_event(memory: StreamMemory, question: str,
                        entity_phrase: str, after_clause: str) -> Answer:
    trace = ["classify -> ENTITY_AFTER_EVENT"]
    ent = memory.resolve_entity(entity_phrase)
    entity_name = ent.name if ent else entity_phrase.strip(" ?.")
    terms = expand_event_terms(after_clause)
    anchor = find_anchor_event(memory, terms, trace)
    if anchor is None:
        return Answer(summary=f"I couldn't find the event you mentioned "
                              f"('{after_clause.strip()}') in this stream's memory.",
                      query_type="ENTITY_AFTER_EVENT", tool_trace=trace,
                      confidence=0.2)
    after_t = float(anchor.t_end)
    timeline = entity_timeline(memory, entity_name, after=after_t, trace=trace)
    scenes = [r for r in timeline if r.kind == "scene"]
    # Build the narrative from post-anchor scenes, then add the strongest
    # transcript/event evidence (e.g. the stated next plan).
    parts: list[str] = []
    citations: list[Citation] = []
    clips: list[Clip] = []
    for s in scenes[:5]:
        parts.append(getattr(s, "caption", s.text))
        citations.append(_cite(s))
        clips.append(_clip_around(s))
    # Add the entity's latest stated plan after the anchor, if any — it is
    # usually the most valuable part of a storyline answer ('what next').
    all_plans = find_stated_plans(memory, trace, after=after_t)
    plans_after = ([p for p in all_plans if memory.matches_entity(p, ent)]
                   if ent else all_plans)
    for r in plans_after[-1:]:
        parts.append(f'stated plan — {describe_record(r)}')
        citations.append(_cite(r))
    if not parts:
        return Answer(summary=f"After {describe_record(anchor)[:60]} at "
                              f"{mmss(anchor.t_start)}, nothing further is "
                              f"recorded for {entity_name}.",
                      query_type="ENTITY_AFTER_EVENT", tool_trace=trace,
                      confidence=0.3)
    summary = (f"After the anchor event at {mmss(anchor.t_start)}-"
               f"{mmss(anchor.t_end)} ({describe_record(anchor)[:60]}), "
               f"{entity_name}: " + "; ".join(parts) + ".")
    citations.sort(key=lambda c: c.t)
    return Answer(summary=summary, query_type="ENTITY_AFTER_EVENT",
                  citations=citations, tool_trace=trace, clips=clips)


def _answer_search_moment(memory: StreamMemory, question: str) -> Answer:
    trace = ["classify -> SEARCH_MOMENT"]
    # Strip the "when did/was" framing so search terms are content words.
    body = re.sub(r"^\s*when (did|was|does|is)\b", "", question.lower())
    body = re.sub(r"\b(start|begin|happen)\b", "", body)
    terms = expand_event_terms(body)
    anchor = find_anchor_event(memory, terms, trace)
    if anchor is None:
        hits = memory.search(body)
        trace.append(f"search({body.strip()!r}) -> {len(hits)} hits")
        if not hits:
            return Answer(summary="I couldn't find that moment in this "
                                  "stream's memory.",
                          query_type="SEARCH_MOMENT", tool_trace=trace,
                          confidence=0.2)
        anchor = hits[0][0]
    summary = (f"That happened at {mmss(anchor.t_start)}"
               + (f"-{mmss(anchor.t_end)}" if anchor.t_end > anchor.t_start else "")
               + f": {describe_record(anchor)}.")
    return Answer(summary=summary, query_type="SEARCH_MOMENT",
                  citations=[_cite(anchor)], tool_trace=trace,
                  clips=[_clip_around(anchor)])


def _answer_next(memory: StreamMemory, now: float) -> Answer:
    trace = ["classify -> STATED_PLAN/NEXT"]
    plans = find_stated_plans(memory, trace)
    if plans:
        chosen = plans[-1]
        spk = getattr(chosen, "speaker", "unknown")
        return Answer(
            summary=f'STATED plan: {spk} said at {mmss(chosen.t_start)}: "{chosen.text}" '
                    '— the latest recorded plan statement; it may since have changed.',
            query_type="STATED_PLAN", citations=[_cite(chosen)], tool_trace=trace,
            clips=[_clip_around(chosen)], source_label="STATED")
    return Answer(summary="No already-spoken explicit plan is available in the observed evidence.",
                  query_type="STATED_PLAN", tool_trace=trace,
                  status="insufficient_evidence", source_label="UNKNOWN", confidence=0.0)


def _answer_current(memory: StreamMemory, now: float) -> Answer:
    trace = ["classify -> CURRENT_ACTIVITY"]
    scenes = [r for r in memory.of_kind("scene") if r.t_end >= max(0, now - 60)]
    current = None
    for s in scenes:
        if s.t_start <= now <= s.t_end:
            current = s
            break
    if current is None and scenes:
        past = [s for s in scenes if s.t_start <= now]
        current = past[-1] if past else scenes[0]
    if current is None:
        recent_speech = [r for r in memory.of_kind("transcript") if r.t_end >= max(0, now - 60)]
        if recent_speech:
            spoken = recent_speech[-1]
            return Answer(summary=f'Spoken transcript [{mmss(spoken.t_start)}]: "{spoken.text}"',
                          query_type="CURRENT_ACTIVITY", citations=[_cite(spoken)], tool_trace=trace,
                          source_label="EXTRACTIVE")
        return Answer(summary="No scene data recorded for the current moment.",
                      query_type="CURRENT_ACTIVITY", tool_trace=trace,
                      confidence=0.2)
    trace.append(f"scene_at(now={mmss(now)}) -> {describe_record(current)[:60]!r}")
    recent = memory.by_window(max(0.0, now - 60), now,
                              kinds={"transcript"})
    extra = f' Latest dialogue: "{recent[-1].text}"' if recent else ""
    summary = (f"Latest completed visual observation at {mmss(current.t_start)}: "
               f"{describe_record(current)}. This does not establish activity between sampled frames.{extra}")
    return Answer(summary=summary, query_type="CURRENT_ACTIVITY",
                  citations=[_cite(current)] + ([_cite(recent[-1])] if recent else []), tool_trace=trace,
                  clips=[_clip_around(current)])


def _answer_game_event(memory: StreamMemory, question: str) -> Answer:
    trace = ["classify -> GAME_EVENT"]
    m = re.search(r"1\s*v\s*(\d+)", question.lower())
    n_target = int(m.group(1)) if m else None
    game_events = [r for r in memory.of_kind("game_event")]
    trace.append(f"scan_game_events -> {len(game_events)} game events")
    # Find a 1-vs-N alive-count state.
    clutch_start: Record | None = None
    candidates = []
    for r in game_events:
        p = r.payload
        allies, enemies = p.get("allies_alive"), p.get("enemies_alive")
        if allies == 1 and isinstance(enemies, int) and enemies >= 3:
            if n_target is None or enemies == n_target:
                candidates.append(r)
    # A failed earlier round must not hide a later supported positive result.
    for candidate in candidates:
        next_start = next((r.t_start for r in game_events if r.type == "round_start"
                           and r.t_start > candidate.t_start), memory.end_time() + 0.001)
        outcome = next((r for r in game_events if r.type == "round_end"
                        and candidate.t_start <= r.t_start < next_start), None)
        if outcome and outcome.payload.get("result") == "win":
            clutch_start = candidate
            break
    clutch_start = clutch_start or (candidates[0] if candidates else None)
    if clutch_start is None:
        return Answer(summary="I cannot verify that: no matching structured game evidence is available. "
                              "Missing events do not establish that it did not happen.",
                      query_type="GAME_EVENT", tool_trace=trace,
                      confidence=0.0, status="insufficient_evidence")

    start = float(clutch_start.t_start)
    enemies = clutch_start.payload.get("enemies_alive")
    trace.append(f"clutch_situation @ {mmss(start)}: 1v{enemies}")
    # What happened next: kills by the streamer + the round outcome.
    next_round = next((r for r in game_events if r.type == "round_start" and r.t_start > start), None)
    boundary = next_round.t_start if next_round else memory.end_time() + 0.001
    round_end = next((r for r in game_events
                      if r.type == "round_end" and start <= r.t_start < boundary), None)
    kill_end = round_end.t_start if round_end else boundary
    kills = [r for r in game_events
             if r.type == "kill" and start <= r.t_start <= kill_end
             and r.payload.get("actor_side") == "allies"]
    trace.append(f"kills_after_clutch -> {len(kills)}; round_end -> "
                 f"{round_end.payload if round_end else None}")
    won = bool(round_end and round_end.payload.get("result") == "win")
    citations = [_cite(clutch_start)] + [_cite(k) for k in kills]
    if round_end is not None:
        citations.append(_cite(round_end))
    kill_times = ", ".join(mmss(k.t_start) for k in kills)
    if won:
        summary = (f"Yes — a 1v{enemies} win. The streamer was left alone at "
                   f"{mmss(start)} and won the round, with kills at "
                   f"{kill_times}, before round_end (win) at "
                   f"{mmss(round_end.t_start)}.")
        conf = 0.95
    else:
        summary = (f"There was a 1v{enemies} situation at {mmss(start)}, but "
                   f"the observed evidence does not establish a round win.")
        conf = 0.7
    clips = [Clip(t_start=max(0.0, start - 5),
                  t_end=float(round_end.t_end) if round_end else min(start, memory.end_time()),
                  label=f"1v{enemies} clutch")]
    return Answer(summary=summary, query_type="GAME_EVENT",
                  citations=citations, tool_trace=trace, clips=clips,
                  confidence=conf, status="answered" if won else "insufficient_evidence")


def _answer_count(memory: StreamMemory, question: str) -> Answer:
    trace = ["classify -> COUNT"]
    low = question.lower()
    # Stream-event counts: subs / follows / raids ... (word boundaries, so
    # 'bank' does not match the 'ban' event type).
    _EVENT_WORDS = {
        "sub": r"\bsubs?\b|\bsubscriptions?\b|\bsubscribers?\b",
        "follow": r"\bfollows?\b|\bfollowers?\b",
        "raid": r"\braids?\b",
        "ban": r"\bbans?\b|\bbanned\b",
        "hype_train": r"\bhype trains?\b",
        "clip_created": r"\bclips?\b",
    }
    for etype, pattern in _EVENT_WORDS.items():
        if re.search(pattern, low):
            hits = memory.by_type(etype, kind="stream_event")
            trace.append(f"by_type({etype!r}, kind='stream_event') -> {len(hits)}")
            return Answer(summary=f"{len(hits)} {etype} event(s) are recorded "
                                  f"in the available evidence; this is not a complete stream total.",
                          query_type="COUNT",
                          citations=[_cite(h) for h in hits[:8]],
                          tool_trace=trace)
    # Zone-visit counts via Tube tracks: 'how many times did X enter the
    # bank?' = how often X's track visited a zone matching the content words.
    ents = memory.entity_names_in(question)
    generic = {"times", "time", "enter", "entered", "enters", "visit",
               "visited", "go", "went", "gone", "stream", "video"}
    qtokens = set(tokenize(question)) - generic
    if ents:
        ent = ents[0]
        for name in ent.all_names():
            qtokens -= set(tokenize(name))
        zone_hits: list[tuple[str, int]] = []
        for track in memory.tracks:
            if track.entity != ent.name:
                continue
            for zone in track.zones_visited:
                if set(tokenize(zone)) & qtokens:
                    zone_hits.append((zone, track.zones_visited.count(zone)))
        if zone_hits:
            zone, n = zone_hits[0]
            trace.append(f"count_zone_visits(entity={ent.name!r}, "
                         f"zone={zone!r}) -> {n}")
            zone_scenes = [r for r in memory.of_kind("scene")
                           if getattr(r, "location", "") == zone]
            return Answer(summary=f"{ent.name}'s track visited {zone} "
                                  f"{n} time(s) in this stream.",
                          query_type="COUNT",
                          citations=[_cite(s) for s in zone_scenes[:3]],
                          tool_trace=trace, confidence=0.85)
    # Otherwise: count matching scenes (entity + content words).
    hits = memory.search(question, kinds={"scene"}, limit=50)
    if ents:
        ent = ents[0]
        hits = [(r, s) for r, s in hits if memory.matches_entity(r, ent)]
        trace.append(f"count_scenes(entity={ent.name!r}) -> {len(hits)}")
    else:
        trace.append(f"count_scenes(query) -> {len(hits)}")
    n = len(hits)
    return Answer(summary=f"{n} matching scene(s) recorded for that question.",
                  query_type="COUNT",
                  citations=[_cite(r) for r, _ in hits[:8]],
                  tool_trace=trace, confidence=0.6 if n else 0.3)


def _answer_fallback(memory: StreamMemory, question: str) -> Answer:
    trace = ["classify -> FALLBACK (token search)"]
    hits = memory.search(question, limit=5)
    trace.append(f"search({question!r}) -> {len(hits)} hits")
    if not hits:
        return Answer(summary="I don't have a confident answer for that in "
                              "this stream's memory — no records matched.",
                      query_type="FALLBACK", tool_trace=trace, confidence=0.1)
    lines = [describe_record(r) for r, _ in hits[:3]]
    summary = ("Closest matches I can ground in the memory: " +
               "; ".join(lines) + ".")
    return Answer(summary=summary, query_type="FALLBACK",
                  citations=[_cite(r) for r, _ in hits[:3]],
                  tool_trace=trace, clips=[_clip_around(hits[0][0])],
                  confidence=0.4)


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

def _parse_recap_window(memory: StreamMemory, low: str,
                        now: float) -> tuple[float, float] | None:
    seconds = re.search(r"(?:last|past)\s+(\d+(?:\.\d+)?)\s*(?:seconds?|secs?)\b", low)
    if seconds:
        return max(0.0, now - float(seconds.group(1))), now
    m = re.search(r"between\s+(\d+(?:\.\d+)?)\s*(?:and|-)\s*(\d+(?:\.\d+)?)\s*min", low)
    if m:
        return float(m.group(1)) * 60, float(m.group(2)) * 60
    m = re.search(r"(?:last|past)\s+(\d+(?:\.\d+)?)\s*min", low)
    if m:
        span = float(m.group(1)) * 60
        return max(0.0, now - span), now
    m = re.search(r"first\s+(\d+(?:\.\d+)?)\s*min", low)
    if m:
        return 0.0, float(m.group(1)) * 60
    if re.search(r'\bso far\b|\b(?:entire|whole|full)\b|\bsince (?:the )?(?:start|beginning)\b', low):
        return 0.0, now
    if "last" in low or "recent" in low:
        return max(0.0, now - 300), now
    return None


def _ask_bounded(memory: StreamMemory, question: str, now: float | None = None) -> Answer:
    """Answer ``question`` from ``memory``. ``now`` defaults to video end."""
    q = question.strip()
    low = q.lower()
    if now is None:
        now = memory.end_time()

    # 1. GAME_EVENT — explicit clutch pattern or the word 'clutch'.
    if re.search(r"1\s*v\s*\d+", low) or "clutch" in low:
        return _answer_game_event(memory, q)

    # 2. NEXT / CURRENT — distinguish intentions from spatial/sequence wording.
    if _is_plan(q):
        return _answer_next(memory, now)
    if ("right now" in low or "currently" in low or "doing now" in low
            or low.strip().rstrip("?").endswith("doing")):
        return _answer_current(memory, now)

    # 3. ENTITY_AFTER_EVENT — 'what happened to X after ...'
    m = re.search(r"what happened to\s+(.+?)\s+after\s+(.+)", low)
    if m:
        # Entity phrase: recover original casing from the question.
        orig = re.search(r"what happened to\s+(.+?)\s+after\s+", q,
                         flags=re.IGNORECASE)
        entity_phrase = orig.group(1) if orig else m.group(1)
        return _answer_after_event(memory, q, entity_phrase, m.group(2))
    m2 = re.search(r"what did\s+(.+?)\s+do after\s+(.+)", low)
    if m2:
        orig = re.search(r"what did\s+(.+?)\s+do after\s+", q,
                         flags=re.IGNORECASE)
        return _answer_after_event(memory, q,
                                   orig.group(1) if orig else m2.group(1),
                                   m2.group(2))

    # 4. RECAP_WINDOW — explicit or 'last N minutes' windows.
    if re.search(r"what(?: has)? happened|recap|summar|catch me up|so far", low):
        win = _parse_recap_window(memory, low, now)
        if win is not None:
            return _answer_recap(memory, win[0], win[1], now)
        if "so far" in low or "catch me up" in low:
            return _answer_recap(memory, max(0.0, now - 300), now, now)

    # 5. COUNT
    if low.startswith("how many") or low.startswith("how often"):
        return _answer_count(memory, q)

    # 6. SEARCH_MOMENT
    if low.startswith("when did") or low.startswith("when was"):
        return _answer_search_moment(memory, q)

    # 7. FALLBACK
    return _answer_fallback(memory, q)


def _finish(answer: Answer, memory: StreamMemory) -> Answer:
    """Final integrity boundary shared by offline and model-backed answers."""
    allowed = {r.evidence_id: r for r in memory.records}
    answer.run_id = memory.run_id
    answer.through_s = memory.end_time()
    answer.coverage_gaps = memory.bounded().coverage_gaps
    answer.citations = [c for c in answer.citations if c.evidence_id in allowed]
    for clip in answer.clips:
        clip.t_start = min(max(0.0, clip.t_start), answer.through_s)
        clip.t_end = min(max(clip.t_start, clip.t_end), answer.through_s)
    if not answer.citations and answer.status == "answered":
        answer.status = "insufficient_evidence"
        answer.summary = "I don't have sufficient observed evidence to answer that through " + mmss(answer.through_s) + "."
        answer.source_label = "UNKNOWN"
        answer.confidence = 0.0
    return answer


def _is_plan(question: str) -> bool:
    if re.search(r"\bnext to\b|\b(?:happened|changed|did .+) next\b", question, re.I):
        return False
    return bool(re.search(r"\bnext\b|\b(?:stated |spoken )?plans?\b", question, re.I))


def ask(memory: StreamMemory, question: str, now: float | None = None) -> Answer:
    """Offline query tools; every route sees only completed evidence as of now."""
    snapshot = memory.bounded(memory.end_time() if now is None else now)
    if _context_intent(_question_text(question)) == "current":
        modality = _question_modality(_question_text(question))
        kinds = {"transcript"} if modality == "spoken" else {"scene"} if modality == "visual" else {"scene", "transcript"}
        context = StreamMemory(snapshot.video, [r for r in snapshot.records if r.kind in kinds],
                               run_id=snapshot.run_id, through_s=snapshot.through_s)
        return _finish(_answer_current(context, snapshot.end_time()), snapshot)
    if _is_plan(question):
        return _finish(_answer_next(snapshot, snapshot.end_time()), snapshot)
    if question.strip().lower().startswith("why"):
        from .retrieval import retrieve
        content_terms = set(tokenize(question)) - {"why", "reason", "because", "since"}
        candidates = [r for r in retrieve(snapshot, question) if r.kind == "transcript"
                      and content_terms.intersection(tokenize(r.text))
                      and re.search(r"\bbecause\b|\breason\b|\bso that\b", r.text, re.I)]
        if not candidates:
            return _finish(Answer("No explicitly stated reason is available.", "STATED_REASON",
                                  status="insufficient_evidence"), snapshot)
        chosen = candidates[0]
        return _finish(Answer("Recorded spoken reason: " + describe_record(chosen),
                              "STATED_REASON", citations=[_cite(chosen)], source_label="SPOKEN"), snapshot)
    return _finish(_ask_bounded(snapshot, question, snapshot.end_time()), snapshot)


def _sample(records: list[Record], limit: int = 16) -> list[Record]:
    ordered = sorted(records, key=lambda r: (r.t_start, r.t_end))
    if len(ordered) <= limit:
        return ordered
    return [ordered[round(i * (len(ordered) - 1) / (limit - 1))] for i in range(limit)]


def _question_text(question: str) -> str:
    low = question.lower().replace("’", "'").strip().rstrip("?!. ")
    return re.sub(r"\bwhat'?s\b", "what is", low)


def _context_intent(low: str) -> str | None:
    """Fast paths for broad context requests, never literal evidence searches."""
    if re.search(r"\bright now\b|\bcurrently\b", low) or re.fullmatch(
        r"(?:please )?(?:what is (?:happening|going on)(?: (?:now|here|in (?:the|this) (?:stream|video)))?"
        r"|what (?:are|is) (?:they|we|he|she|the speaker|the streamer) (?:doing|saying|talking about)(?: now)?"
        r"|what am i (?:watching|seeing)|what (?:can (?:you|we) see|is on (?:the )?screen)"
        r"|describe (?:the |this |current |latest )?(?:scene|frame|view))", low):
        return "current"
    if not re.search(r"\bafter\b|\bbefore\b|\bnext\b|what happened to\b", low) and re.search(
        r"\brecap\b|\bsummari[sz]e\b|\bsummary\b|catch me up|so far|what(?: has)? happened(?:$| in | during | recently)", low):
        return "recap"
    return None


def _question_modality(low: str) -> str:
    if re.search(r"\b(?:sign|label|screen|text|caption|subtitle|poster)\b.*\b(?:say|read)|\bwritten (?:on|in)\b", low):
        return "visual"
    if re.search(r"\b(?:say|says|said|saying|mention|mentioned|speaking|talk(?:ing|ed)? about|tell us|told us)\b", low):
        return "spoken"
    return "any"


def _balanced_context(records: list[Record], limit: int = 16) -> list[Record]:
    """Keep speech represented even when frames greatly outnumber utterances."""
    speech = [r for r in records if r.kind == "transcript"]
    other = [r for r in records if r.kind != "transcript"]
    half = limit // 2
    spoken_n = min(len(speech), half + max(0, half - len(other)))
    selected = _sample(speech, spoken_n) if spoken_n > 1 else speech[-spoken_n:] if spoken_n else []
    remaining = limit - len(selected)
    selected += _sample(other, remaining) if remaining > 1 else other[-remaining:] if remaining else []
    return sorted(selected, key=lambda r: (r.t_start, r.t_end))


def _recap_context(records: list[Record]) -> list[Record]:
    # Repeated scenery must not displace one-off dialogue in an overview.
    unique, counts = {}, {}
    for record in records:
        key=(record.kind,getattr(record,'speaker',''),(record.text or getattr(record,'caption','')).casefold().strip())
        unique.setdefault(key,record)
        counts[key]=counts.get(key,0)+1
    # Repeated old speech can otherwise dominate a recap even after visual
    # deduplication. Preserve important plans/introductions via story anchors,
    # and retain the latest utterances separately below.
    selected=[r for key,r in unique.items() if r.kind!='transcript' or counts[key]<3]+records[-2:]
    return _balanced_context(sorted({r.evidence_id:r for r in selected}.values(),key=lambda r:r.t_start))


def _interpret(question: str, model, fast: str | None) -> dict:
    plan = {"intent": fast or "search", "modality": "any", "terms": [], "window_s": 0}
    if fast or not callable(getattr(model, "interpret_question", None)):
        return plan
    try:
        candidate = model.interpret_question(question)
        if (isinstance(candidate, dict) and candidate.get("intent") in {
                "current", "recap", "search", "sequence", "reason", "plan", "prediction", "count"}
                and candidate.get("modality") in {"any", "spoken", "visual"}
                and isinstance(candidate.get("terms"), list)
                and all(isinstance(t, str) for t in candidate["terms"])
                and type(candidate.get("window_s")) in {int, float}
                and math.isfinite(candidate["window_s"]) and 0 <= candidate["window_s"] <= 3600):
            return {**candidate, "terms": [t[:60] for t in candidate["terms"][:6]]}
    except Exception:
        pass  # Literal retrieval and a bounded context fallback remain usable.
    return plan


def answer_with_model(memory: StreamMemory, question: str, model,
                      now: float | None = None, run_id: str = "", *,
                      answer_style: str = "natural", allow_predictions: bool = True,
                      frame_loader=None, conversation: list[dict] | None = None) -> Answer:
    debug = {}
    answer = _answer_with_model(memory,question,model,now,run_id,answer_style=answer_style,
        allow_predictions=allow_predictions,frame_loader=frame_loader,conversation=conversation,debug=debug)
    answer.context = debug
    return answer


def _answer_with_model(memory: StreamMemory, question: str, model,
                      now: float | None = None, run_id: str = "", *,
                      answer_style: str = "natural", allow_predictions: bool = True,
                      frame_loader=None, conversation=None, debug=None) -> Answer:
    """Retrieve locally and resolve model claims to actual bounded records only.

    The caller serializes use of the model alongside frame ingestion. This
    method never executes generated tools, URLs, SQL, actions or timestamps.
    """
    from .localai import LocalAIError, validate_answer
    from .retrieval import retrieve

    snapshot = memory.bounded(memory.end_time() if now is None else now)
    if run_id and run_id != snapshot.run_id:
        return Answer("This question belongs to an expired replay run.", "EVIDENCE_QA",
                      run_id=run_id, through_s=snapshot.end_time(), status="insufficient_evidence")
    low = _question_text(question)
    natural = answer_style == "natural" and callable(getattr(model, "verify_claims", None))
    if (_is_plan(question) and not natural) or re.search(r"1\s*v\s*\d+|\bclutch\b", low):
        return ask(snapshot, question)
    fast = _context_intent(low)
    if re.search(r"\bpredict\b|\bwhat (?:might|will|could|may) happen|\bgoing to happen|\bgonna happen", low):
        fast = "prediction"
    plan = _interpret(question, model, fast)
    intent = plan["intent"]
    if intent == "plan" and not natural:
        return _finish(_answer_next(snapshot, snapshot.end_time()), snapshot)
    explicit_modality = _question_modality(low)
    modality = explicit_modality if explicit_modality != "any" else plan["modality"]
    kinds = {"transcript"} if modality == "spoken" else {"scene"} if modality == "visual" else {"transcript", "scene", "game_event", "stream_event"}
    pool = [r for r in snapshot.records if r.kind in kinds]
    window = _parse_recap_window(snapshot, low, snapshot.end_time())
    trace = [f"as_of({snapshot.end_time():.2f})", f"question_intent={intent}; modality={modality}"]
    recent_start = max(0, snapshot.end_time() - (plan["window_s"] or 60))
    if intent in {"current", "prediction"}:
        recent_start = max(0, snapshot.end_time() - (plan["window_s"] or 60))
        recent = [r for r in pool if r.t_end >= recent_start]
        candidates = sorted([r for r in recent if r.kind == "scene"][-3:] +
                            [r for r in recent if r.kind == "transcript"][-6 if natural else -3:], key=lambda r: (r.t_start, r.t_end))
        if natural and candidates:
            # Older statements supply context, not proof that an activity is ongoing.
            older = [r for r in pool if r.t_end < recent_start]
            terms = " ".join(describe_record(r) for r in candidates if r.kind == "transcript")[:2000]
            historical = retrieve(StreamMemory(snapshot.video, older), terms, 5, kinds=kinds) if terms else []
            plans = find_stated_plans(snapshot, [])[-3:]
            candidates = list({r.evidence_id:r for r in candidates + historical + plans if r.kind in kinds}.values())[:16]
        query_type = "PREDICTION" if intent == "prediction" else "CURRENT_ACTIVITY"
        trace.append("recent completed speech and frames; no keyword match required")
    elif intent == "recap":
        t0, t1 = window or (max(0.0, snapshot.end_time() - (plan["window_s"] or 300)), snapshot.end_time())
        pool = [r for r in pool if r.t_end >= t0 and r.t_start <= t1]
        candidates = _recap_context(pool)
        query_type = "RECAP_WINDOW"
        trace.append(f"context_window({t0:.2f}, {min(t1, snapshot.end_time()):.2f})")
    else:
        if plan["window_s"]:
            pool = [r for r in pool if r.t_end >= snapshot.end_time() - plan["window_s"]]
        if window and re.search(r"(?:last|past|first)\s+\d|between\s+\d", low):
            pool = [r for r in pool if r.t_end >= window[0] and r.t_start <= window[1]]
        scoped = StreamMemory(snapshot.video, pool, run_id=snapshot.run_id, through_s=snapshot.through_s,
                              profile_versions=snapshot.profile_versions,semantic=snapshot.semantic)
        interpreted = retrieve(scoped, " ".join(plan["terms"]), 16, kinds=kinds) if plan["terms"] else []
        literal = retrieve(scoped, question, 16, kinds=kinds)
        candidates = list({r.evidence_id: r for r in interpreted + literal}.values())[:16]
        if not candidates and callable(getattr(model, "expand_query", None)):
            try:
                terms = model.expand_query(question)
                if isinstance(terms, list) and all(isinstance(t, str) for t in terms):
                    candidates = retrieve(scoped, " ".join(terms[:6])[:300], 16, kinds=kinds)
            except Exception:
                pass  # A failed rewrite cannot replace an evidence-based answer.
        query_type = "SEARCH_MOMENT" if low.startswith(("when", "find")) else "EVIDENCE_QA"
        trace.append(f"local_fts5 -> {len(candidates)} candidates")
        if not candidates and pool:
            # Give semantic reasoning a bounded chance even with no lexical
            # overlap. Absence of a keyword is not absence of source evidence.
            recent = _balanced_context(pool[-32:], 8)
            older = _balanced_context(pool, 8)
            candidates = list({r.evidence_id: r for r in recent + older}.values())
            trace.append("bounded context fallback; model must still establish relevance")
        elif len(candidates) < 8 and not (snapshot.semantic and snapshot.semantic.ready) and intent != "sequence" and not re.search(r"\b(?:after|before)\b|happened next", low):
            # A weak lexical hit must not hide a differently worded statement.
            # Retain the direct hits, then let the model assess a small mix of
            # speech and visual context. These records are still bounded above.
            context = _balanced_context(pool[-32:], 8) + _balanced_context(pool, 8)
            candidates = list({r.evidence_id: r for r in candidates + context}.values())[:16]
            trace.append("sparse search supplemented with bounded speech and visual context")
        if (re.search(r"\b(?:after|before)\b", low) or intent == "sequence" or "happened next" in low) and candidates:
            # Include context on the requested side of the matching anchor.
            anchor = candidates[0]
            preceding = [r for r in pool if r.t_end <= anchor.t_start and r.evidence_id != anchor.evidence_id][-8:]
            following = [r for r in pool if r.t_start >= anchor.t_end][:8]
            if re.search(r"\bbefore\b", low) and not re.search(r"\bafter\b", low):
                neighbors = preceding
            elif re.search(r"\bafter\b|happened next", low) and not re.search(r"\bbefore\b", low):
                neighbors = following
            else:
                neighbors = preceding[-4:] + following[:4]
            candidates = list({r.evidence_id: r for r in candidates[:8] + neighbors}.values())
            query_type = "SEQUENCE"
    followup = bool(conversation and re.search(r'\b(?:he|she|they|that|those|it|him|her)\b',low))
    if followup:
        last = conversation[-1]
        ids = set(last.get('evidence_ids',[]))
        references = [r for r in snapshot.records if r.evidence_id in ids]
        related = retrieve(snapshot,question+' '+last.get('question',''),8,kinds=kinds)
        candidates = list({r.evidence_id:r for r in references+related+candidates}.values())[:24]
        trace.append('follow-up references resolved only within this conversation and cutoff')
    if low.startswith(("how many", "how often")) or intent == "count":
        quantity = r"\b(?:\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|dozen|hundred)\b"
        candidates = [r for r in candidates if re.search(quantity, r.text or getattr(r, "caption", ""), re.I)]
        query_type = "STATED_QUANTITY"
        trace.append("only explicitly recorded quantities; no exhaustive count inferred")
    is_why = low.startswith("why") or intent == "reason"
    reason_terms = set(tokenize(" ".join([question, *plan["terms"]]))) - {
        "why", "reason", "because", "since"}
    if followup: reason_terms.update(tokenize(conversation[-1].get('question','')))
    if is_why and not any(r.kind == "transcript" and reason_terms.intersection(tokenize(r.text)) and re.search(
            r"\bbecause\b|\breason\b|\bso that\b", r.text, re.I) for r in candidates):
        return _finish(Answer("I haven’t heard them explain why yet.",
                              "STATED_REASON", status="insufficient_evidence"), snapshot)
    if not candidates:
        message = ("I’m still catching up—there isn’t enough recent footage processed to tell yet." if intent == "current" else
                   "I haven’t heard them talk about that in the part processed so far." if modality == "spoken" else
                   "I can’t tell from the part of the stream processed so far.")
        return _finish(Answer(message, query_type,
                              status="insufficient_evidence"), snapshot)
    evidence = [{"evidence_id": r.evidence_id, "t_start": r.t_start, "t_end": r.t_end,
                 "source_type": r.provenance, "excerpt": (getattr(r, 'caption', '') or r.text or describe_record(r))[:850],
                 "context_role": "background" if intent in {"current", "prediction"} and r.t_end < recent_start else "recent"}
                for r in candidates[:16]]
    enhanced = bool(getattr(model,'supports_stream_context',False))
    context, images = {}, []
    if enhanced:
        from .story import assemble_context
        context_memory = snapshot
        if intent == 'recap' and window and window[0] > 0:
            context_memory = StreamMemory(snapshot.video,[r for r in snapshot.records if r.t_end >= window[0]],
                run_id=snapshot.run_id,through_s=snapshot.through_s,profile_versions=snapshot.profile_versions,
                story_events=[e for e in snapshot.story_events if e['end_s'] >= window[0]],
                story_through_s=snapshot.story_through_s,semantic=snapshot.semantic)
        candidates, evidence, context, details = assemble_context(context_memory,question,intent,candidates,recent_start,
            modality=modality,neighbor_pool=pool)
        context['conversation'] = (conversation or [])[-3:]
        if debug is not None:
            debug.update(details, intent=intent, frames=[])
        if frame_loader and context['creator_profile']['inspect_frames'] and modality != 'spoken':
            visual = [r for r in candidates if r.kind=='scene']
            if intent in {'current','prediction'}:
                visual = sorted(visual,key=lambda r:r.t_end,reverse=True)
            if intent == 'recap':
                from .story import spread
                visual = spread(sorted(visual,key=lambda r:r.t_start),3)
            for record in visual[:3]:
                try:
                    raw = frame_loader(record)
                    if raw:
                        row = next(e for e in evidence if e['evidence_id']==record.evidence_id)
                        row['image_index'] = len(images)
                        images.append(raw)
                        if debug is not None: debug['frames'].append({'evidence_id':record.evidence_id,'time_s':record.t_start})
                except Exception:
                    trace.append('original frame inspection unavailable; using recorded description')
        trace.append(f"stream memory: profile v{context['profile_version']}; story through {context['story_through_s']:.2f}s; {len(images)} original frames")
    trace.append(f"local_model_context -> {len(evidence)} records")
    if snapshot.coverage_gaps:
        trace.append('partial coverage: skipped analysis is not evidence of absence')
        for item in evidence:
            item['coverage_incomplete'] = True
    if getattr(model, "diagnostics", None):
        model.diagnostics.event("question_context_ready", **model.diagnostic_context(),
            operation=query_type, kind=modality, candidate_count=len(evidence),
            processed_through_s=snapshot.end_time(), frame_count=len(images),
            profile_version=context.get('profile_version',0),story_through_s=context.get('story_through_s',0),
            context_characters=(debug or {}).get('context_characters',0))
    try:
        inference = allow_predictions and intent == "prediction"
        extra = {'stream_context':context,'images':images} if enhanced else {}
        draft = (model.answer_from_evidence(question[:1000], evidence, allow_inference=inference,**extra) if natural else
                 model.answer_from_evidence(question[:1000], evidence,**extra))
        result = validate_answer(draft,
                                 {e["evidence_id"] for e in evidence})
        if result["insufficient"]:
            return _finish(Answer("I can’t tell from what’s been said or shown so far.",
                                  query_type, status="insufficient_evidence", tool_trace=trace), snapshot)
        by_id = {r.evidence_id: r for r in candidates}
        if natural:
            accepted = model.verify_claims(question, result["claims"], evidence, allow_inference=inference,**extra)
            if debug is not None: debug['accepted_claims'] = len(accepted) if isinstance(accepted,list) else 0
            if not isinstance(accepted, list) or any(type(i) is not int or not 0 <= i < len(result["claims"]) for i in accepted):
                raise LocalAIError("Invalid claim verification")
            from .story import valid_contextual_claim
            claims = [claim for i, claim in enumerate(result["claims"]) if i in accepted and
                      (claim['kind'] == 'fact' or
                       (claim['kind'] == 'contextual' and enhanced and valid_contextual_claim(claim,evidence,context)) or
                       (claim['kind'] == 'inference' and inference and re.search(r'\b(?:might|may|could|probably|likely)\b',claim['text'],re.I)))]
            claims = [claim for claim in claims if any(by_id[eid].kind == 'transcript' for eid in claim['evidence_ids']) or
                      not re.search(r'\b(?:said|says|saying|told|mentioned|speaker|reported|talking)\b',claim['text'],re.I)]
            if enhanced:
                from .story import unsupported_speaker_attribution
                claims = [claim for claim in claims if not unsupported_speaker_attribution(claim['text'],
                    [e for e in evidence if e['evidence_id'] in claim['evidence_ids']],context['creator_profile'])]
            # Source IDs and full windows are resolved in code; the model cannot
            # create replay times or reach into unprocessed/future footage.
            if not claims:
                if intent == 'current':
                    recent_speech = [r for r in candidates if r.kind == 'transcript' and r.t_end >= recent_start and len(r.text.split()) >= 5]
                    if recent_speech:
                        record = max(recent_speech,key=lambda r:r.t_end)
                        return _finish(Answer(f'Most recently, they said: “{record.text[:240]}”',query_type,
                            citations=[_cite(record)], source_label='EXTRACTIVE', tool_trace=trace + ['source quote fallback']),snapshot)
                elif intent != 'prediction':
                    # A rejected paraphrase can still point to a useful exact
                    # statement. Require a direct spoken search hit and retain
                    # its wording rather than asserting the rejected relation.
                    direct = {r.evidence_id for r in retrieve(snapshot, question, 8, kinds={'transcript'})}
                    quoted = next((by_id[eid] for claim in result['claims'] for eid in claim['evidence_ids']
                                   if eid in direct and len(by_id[eid].text.split()) >= 5), None)
                    if quoted:
                        return _finish(Answer(f'The closest thing they said was: “{quoted.text[:300]}”',query_type,
                            citations=[_cite(quoted)], source_label='EXTRACTIVE', tool_trace=trace + ['source quote fallback']),snapshot)
                return _finish(Answer("I’m not sure enough to answer that yet. Try asking about a specific moment.",
                    query_type, status="insufficient_evidence", tool_trace=trace + ["draft claims rejected"]), snapshot)
            result['claims'] = claims[:3 if intent=='recap' else 2]
        cited_ids = list(dict.fromkeys(eid for claim in result["claims"] for eid in claim["evidence_ids"]))
        if is_why and any(not any(by_id[eid].kind == "transcript" and re.search(
                r"\bbecause\b|\breason\b|\bso that\b", by_id[eid].text, re.I)
                for eid in claim["evidence_ids"]) for claim in result["claims"]):
            raise LocalAIError("Reason claim lacks a spoken reason citation")
        if natural:
            return _finish(Answer(" ".join(claim['text'] for claim in result['claims']), query_type,
                citations=[_cite(by_id[eid]) for eid in cited_ids],
                tool_trace=trace + ["local draft -> separate source check -> validated citations"],
                source_label=("INFERENCE" if any(c['kind']=='inference' for c in result['claims']) else
                              "CONTEXTUAL" if any(c['kind']=='contextual' for c in result['claims']) else "GROUNDED")), snapshot)
        # The model selects evidence, but NEVER supplies public factual prose.
        # Even a valid source ID does not prove its generated paraphrase follows.
        selected = sorted((by_id[eid] for eid in cited_ids), key=lambda r: (r.t_start, r.t_end),
                          reverse=query_type == "CURRENT_ACTIVITY")[:4]
        labels = {"spoken": "Spoken transcript", "observed": "Generated visual description",
                  "chat": "Chat message", "game_event": "Recorded game event",
                  "stream_event": "Recorded stream event"}
        lines = []
        summary_records = selected if query_type in {"RECAP_WINDOW", "SEQUENCE"} else selected[:2]
        for record in summary_records:
            source = (getattr(record, "caption", "") or record.text).strip()
            excerpt = source if len(source) <= 220 else source[:220] + "…"
            label = labels.get(record.provenance, "Recorded excerpt")
            lines.append(f'{label} [{mmss(record.t_start)}]: "{excerpt}"')
        if len(selected) > len(summary_records):
            lines.append(f"{len(selected) - len(summary_records)} additional supporting moments are highlighted below.")
        return _finish(Answer("\n".join(lines), query_type,
                              citations=[_cite(record) for record in selected],
                              tool_trace=trace + ["local_model -> evidence selection; deterministic source excerpts"],
                              source_label="EXTRACTIVE"), snapshot)
    except Exception as exc:
        return _finish(Answer("The local model could not produce a valid evidence-linked answer. "
                              "Please try again.", query_type, status="error",
                              tool_trace=trace + [type(exc).__name__]), snapshot)
