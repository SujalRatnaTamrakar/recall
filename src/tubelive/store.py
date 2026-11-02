"""StreamMemory: the multimodal bundle for one video, plus query helpers.

Bundle JSON shape::

    {
      "video":    {id, title, duration_s, fps, width, height, source, game},
      "entities": [{name, aliases, kind}, ...],
      "records":  [ {kind, t_start, t_end, modality, type, entities, text,
                     confidence, payload, ...kind-specific fields...}, ...],
      "tracks":   [ {track_id, label, first_t, last_t, zones_visited, entity} ]
    }
"""
from __future__ import annotations

import json
import copy
import hashlib
import math
import threading
from pathlib import Path
from typing import Iterable

from .schema import Entity, Record, TubeTrack, VideoMeta, tokenize


class StreamMemory:
    def __init__(self, video: VideoMeta, records: list[Record] | None = None,
                 tracks: list[TubeTrack] | None = None,
                 entities: list[Entity] | None = None, run_id: str = "",
                 through_s: float | None = None, coverage_gaps: list[dict] | None = None,
                 profile_versions: list[dict] | None = None, story_events: list[dict] | None = None,
                 story_through_s: float = 0, semantic=None, story_gaps: list[dict] | None = None):
        self.run_id = run_id or video.id
        self.through_s = through_s
        self._lock = threading.RLock()
        self.video = video
        self.records: list[Record] = sorted(records or [],
                                            key=lambda r: (r.t_start, r.t_end))
        self.tracks: list[TubeTrack] = list(tracks or [])
        self.entities: list[Entity] = list(entities or [])
        self.coverage_gaps = copy.deepcopy(coverage_gaps or [])
        self.profile_versions = copy.deepcopy(profile_versions or [])
        self.story_events = copy.deepcopy(story_events or [])
        self.story_through_s = float(story_through_s)
        self.story_gaps = copy.deepcopy(story_gaps or [])
        self.semantic = semantic
        for record in self.records:
            self._identify(record)

    def _identify(self, record: Record) -> None:
        if not record.source_type:
            record.source_type = record.provenance
        if not record.evidence_id:
            data = record.to_dict()
            data.pop("evidence_id", None)
            raw = json.dumps([self.run_id, data], sort_keys=True,
                             ensure_ascii=False, default=str).encode("utf-8")
            record.evidence_id = "ev_" + hashlib.sha256(raw).hexdigest()[:20]

    def append(self, record: Record) -> None:
        with self._lock:
            self._identify(record)
            if not any(r.evidence_id == record.evidence_id for r in self.records):
                self.records.append(record)
                self.records.sort(key=lambda r: (r.t_start, r.t_end))

    def set_processed_through(self, seconds: float) -> None:
        """Advance on completed chunks, including silence/no-event chunks."""
        value = float(seconds)
        if not math.isfinite(value) or value < 0:
            raise ValueError("processed time must be finite and nonnegative")
        with self._lock:
            self.through_s = max(float(self.through_s or 0), value)

    def add_coverage_gap(self, start: float, end: float, lane: str, reason: str) -> None:
        """Half-open intervals of missing analysis, not missing source footage."""
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end):
            return
        with self._lock:
            # Merge neighboring holes only within the same modality and cause.
            remaining = []
            for gap in self.coverage_gaps:
                if gap['lane']==lane and gap['reason']==reason and gap['start_s'] <= end and gap['end_s'] >= start:
                    start, end = min(start,gap['start_s']), max(end,gap['end_s'])
                else:
                    remaining.append(gap)
            remaining.append({'start_s':start,'end_s':end,'lane':lane,'reason':reason})
            self.coverage_gaps = sorted(remaining,key=lambda g:(g['start_s'],g['end_s']))

    def bounded(self, through_s: float | None = None) -> "StreamMemory":
        """Detached evidence snapshot; never trim a future chunk into a fact."""
        requested = self.end_time() if through_s is None else float(through_s)
        if not math.isfinite(requested):
            requested = 0.0
        cutoff = max(0.0, min(requested, self.end_time(),
                              self.through_s if self.through_s is not None else requested))
        with self._lock:
            records = [copy.deepcopy(r) for r in self.records
                       if math.isfinite(r.t_start) and r.t_start >= 0
                       and r.t_end >= r.t_start and r.completion_s <= cutoff]
            # Track summaries contain future zones; retain only completed tracks.
            tracks = [copy.deepcopy(t) for t in self.tracks
                      if 0 <= t.first_t <= t.last_t <= cutoff]
            entities = []
            for entity in self.entities:
                if any(self.matches_entity(r, entity) for r in records):
                    names_seen = " ".join(r.searchable_text().lower() for r in records)
                    entities.append(Entity(entity.name,
                                           [a for a in entity.aliases if a.lower() in names_seen],
                                           entity.kind))
            video = copy.deepcopy(self.video)
            video.duration_s = cutoff
            from .story import eligible_events
            return StreamMemory(video, records, tracks, entities,
                                run_id=self.run_id, through_s=cutoff,
                                coverage_gaps=[{**g,'end_s':min(g['end_s'],cutoff)} for g in self.coverage_gaps
                                               if g['start_s'] < cutoff or g['start_s']==g['end_s']<=cutoff],
                                profile_versions=[v for v in self.profile_versions if v['known_s'] <= cutoff],
                                story_events=eligible_events(self.story_events,records,self.run_id,cutoff),
                                story_through_s=min(cutoff,self.story_through_s), semantic=self.semantic,
                                story_gaps=[g for g in self.story_gaps if g['end_s'] <= cutoff])

    # -- persistence ---------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "StreamMemory":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            video=VideoMeta.from_dict(data["video"]),
            records=[Record.from_dict(r) for r in data.get("records", [])],
            tracks=[TubeTrack.from_dict(t) for t in data.get("tracks", [])],
            entities=[Entity.from_dict(e) for e in data.get("entities", [])],
            run_id=data.get("run_id", ""), through_s=data.get("through_s"),
            coverage_gaps=data.get('coverage_gaps',[]),
            profile_versions=data.get('profile_versions',[]), story_events=data.get('story_events',[]),
            story_through_s=data.get('story_through_s',0),
            story_gaps=data.get('story_gaps',[]),
        )

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._raw_dict(), indent=1, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def to_dict(self, through_s: float | None = None) -> dict:
        if through_s is not None or self.through_s is not None:
            return self.bounded(self.through_s if through_s is None else through_s)._raw_dict()
        return self._raw_dict()

    def _raw_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "through_s": self.through_s,
            "video": self.video.to_dict(),
            "entities": [e.to_dict() for e in self.entities],
            "records": [r.to_dict() for r in self.records],
            "tracks": [t.to_dict() for t in self.tracks],
            'coverage_gaps': copy.deepcopy(self.coverage_gaps),
            'profile_versions': copy.deepcopy(self.profile_versions),
            'story_events': copy.deepcopy(self.story_events),
            'story_through_s': self.story_through_s,
            'story_gaps': copy.deepcopy(self.story_gaps),
        }

    # -- basics --------------------------------------------------------------
    @property
    def duration_s(self) -> float:
        if self.video.duration_s:
            return float(self.video.duration_s)
        return max((r.t_end for r in self.records), default=0.0)

    def end_time(self) -> float:
        """'now' by default: the end of the video / last record."""
        return min(self.duration_s, self.through_s) if self.through_s is not None else self.duration_s

    # -- entity resolution ----------------------------------------------------
    def resolve_entity(self, name_or_alias: str) -> Entity | None:
        """Match a name/alias (case-insensitive) to a registered entity."""
        q = name_or_alias.strip().lower()
        for ent in self.entities:
            if q in (n.lower() for n in ent.all_names()):
                return ent
        # partial match: alias contained in the query or vice versa
        for ent in self.entities:
            for n in ent.all_names():
                if n.lower() in q or q in n.lower():
                    return ent
        return None

    def entity_names_in(self, text: str) -> list[Entity]:
        """All registered entities mentioned (by name or alias) in text."""
        low = f" {text.lower()} "
        found = []
        for ent in self.entities:
            for n in ent.all_names():
                if f" {n.lower()} " in low or low.strip().startswith(n.lower()):
                    found.append(ent)
                    break
        return found

    def matches_entity(self, record: Record, entity: Entity) -> bool:
        names = {n.lower() for n in entity.all_names()}
        if any(e.lower() in names for e in record.entities):
            return True
        text = record.searchable_text().lower()
        return any(f" {n} " in f" {text} " or text.startswith(f"{n} ")
                   for n in names)

    # -- query helpers ---------------------------------------------------------
    def by_window(self, t0: float, t1: float,
                  kinds: Iterable[str] | None = None) -> list[Record]:
        """Records overlapping [t0, t1], optionally restricted to kinds."""
        ks = set(kinds) if kinds else None
        return [r for r in self.records
                if r.t_end >= t0 and r.t_start <= t1
                and (ks is None or r.kind in ks)]

    def by_entity(self, entity: Entity, after: float | None = None,
                  before: float | None = None) -> list[Record]:
        out = []
        for r in self.records:
            if after is not None and r.t_end <= after:
                continue
            if before is not None and r.t_start >= before:
                continue
            if self.matches_entity(r, entity):
                out.append(r)
        return out

    def by_type(self, type_name: str, kind: str | None = None) -> list[Record]:
        return [r for r in self.records
                if r.type == type_name and (kind is None or r.kind == kind)]

    def of_kind(self, kind: str) -> list[Record]:
        return [r for r in self.records if r.kind == kind]

    def search(self, query: str, kinds: Iterable[str] | None = None,
               after: float | None = None, before: float | None = None,
               limit: int = 10) -> list[tuple[Record, float]]:
        """Full-text search with simple token scoring.

        Score = distinct query tokens found in the record's searchable text,
        +2 bonus for an exact phrase hit, + small boost for scene/transcript
        records (they carry the most narrative signal).  Returns
        ``[(record, score)]`` best-first; ties break by earliest time.
        """
        qtokens = set(tokenize(query))
        if not qtokens:
            return []
        ks = set(kinds) if kinds else None
        scored: list[tuple[Record, float]] = []
        qlow = query.lower()
        for r in self.records:
            if ks is not None and r.kind not in ks:
                continue
            if after is not None and r.t_end <= after:
                continue
            if before is not None and r.t_start >= before:
                continue
            text = r.searchable_text().lower()
            rtokens = set(tokenize(text))
            hits = len(qtokens & rtokens)
            if hits == 0:
                continue
            score = float(hits)
            if len(qtokens) > 1 and " ".join(sorted(qtokens)[:2]) in text:
                score += 0.5
            if qlow in text:
                score += 2.0
            if r.kind in ("scene", "transcript"):
                score += 0.25
            scored.append((r, score))
        scored.sort(key=lambda rs: (-rs[1], rs[0].t_start))
        return scored[:limit]
