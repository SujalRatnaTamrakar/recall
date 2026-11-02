"""Local, parameterized FTS5 retrieval. Search text is never SQL or FTS code."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .schema import Record, tokenize
from .store import StreamMemory

_SYNONYMS = {
    "changed": ["change", "swapped", "replaced"],
    "change": ["changed", "switch", "swapped"],
    "swapped": ["replaced", "changed"],
    "fixed": ["fix", "solved", "working"],
    "failed": ["failure", "broken", "error"],
    "tool": ["scissors", "brush", "knife"],
    "next": ["plan", "going", "after"],
    "why": ["because", "reason", "since"],
    "escape": ["escaped", "getaway", "lost", "pursuers"],
    "escaped": ["escape", "getaway", "lost", "pursuers"],
    "police": ["cops", "officer", "pursuers", "chase"],
    "cops": ["police", "officer", "pursuers", "chase"],
    "chase": ["pursuit", "pursuers", "police", "cops"],
    "pursuit": ["chase", "pursuers"],
    "vehicle": ["car", "van", "truck"],
}


def search_terms(query: str) -> list[str]:
    terms = list(dict.fromkeys(tokenize(query)))[:24]
    if "get away" in query.lower() or "got away" in query.lower():
        terms.extend(["escape", "escaped", "getaway", "lost", "pursuers"])
    for token in list(terms):
        terms.extend(t for t in _SYNONYMS.get(token, []) if t not in terms)
    return terms[:48]


class EvidenceIndex:
    """One locked SQLite connection per index; safe across request threads."""
    def __init__(self, path: str | Path = ":memory:"):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS evidence USING fts5("
                             "evidence_id UNINDEXED, run_id UNINDEXED, completion UNINDEXED, text, tokenize='porter unicode61')")

    def index(self, records: list[Record], run_id: str = "") -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM evidence WHERE run_id = ?", (run_id,))
            self._db.executemany("INSERT INTO evidence VALUES (?, ?, ?, ?)",
                                 [(r.evidence_id, run_id, r.completion_s,
                                   r.searchable_text()) for r in records])

    def search(self, question: str, run_id: str = "", through_s: float = float("inf"),
               limit: int = 16) -> list[str]:
        terms = search_terms(question)
        if not terms:
            return []
        expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        with self._lock:
            rows = self._db.execute(
                "SELECT evidence_id FROM evidence WHERE evidence MATCH ? "
                "AND run_id = ? AND CAST(completion AS REAL) <= ? "
                "ORDER BY bm25(evidence), CAST(completion AS REAL), evidence_id LIMIT ?",
                (expression, run_id, through_s, max(1, min(int(limit), 16)))).fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()


def retrieve(memory: StreamMemory, question: str, limit: int = 16, *, kinds: set[str] | None = None) -> list[Record]:
    snapshot = memory.bounded()
    from .story import profile_at
    import re
    profile = profile_at(snapshot.profile_versions,snapshot.end_time())['data']
    aliases = [person['name'] for person in profile['cast'] if any(
        re.search(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',question,re.I) for alias in person['aliases'])]
    question = ' '.join([question,*aliases])
    index = EvidenceIndex()
    try:
        index.index([r for r in snapshot.records if kinds is None or r.kind in kinds], snapshot.run_id)
        ids = index.search(question, snapshot.run_id, snapshot.end_time(), limit)
        if memory.semantic and profile['semantic_search']:
            semantic_ids = memory.semantic.search(question,
                [r for r in snapshot.records if kinds is None or r.kind in kinds],limit)
            # Reciprocal rank fusion preserves exact-name hits while allowing
            # differently worded speech to compete. Both sets obey the cutoff.
            scores = {}
            for ranked in (ids,semantic_ids):
                for rank,eid in enumerate(ranked):
                    scores[eid] = scores.get(eid,0) + 1/(60+rank)
            ids = sorted(scores,key=scores.get,reverse=True)[:limit]
        by_id = {r.evidence_id: r for r in snapshot.records}
        return [by_id[eid] for eid in ids if eid in by_id]
    finally:
        index.close()
