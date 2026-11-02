"""Multimodal stream-memory schema for Recall (video-only v0).

Design notes
------------
* One stream clock: every record carries ``t_start`` / ``t_end`` in seconds
  since the start of the video (which, in the live version, will be seconds
  since ``stream.online``).  All modalities share that clock, so a single
  window query can fuse video, transcript, chat and events.
* Style mirrors the original Tube project: Tube's geometry tools are pure
  functions over an in-memory dataset returning ranked ``Hit`` rows; here
  records are plain dataclasses with JSON (de)serialisation, and the query
  engine in :mod:`tubelive.ask` is pure functions over a ``StreamMemory``.
* Everything is stdlib-only.
"""
from __future__ import annotations

import re
import math
from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def mmss(t: float) -> str:
    """Format seconds as ``m:ss`` (e.g. 843 -> '14:03')."""
    t = max(0, int(round(float(t))))
    return f"{t // 60}:{t % 60:02d}"


_STOPWORDS = {
    "a", "an", "the", "is", "was", "were", "did", "do", "does", "what",
    "when", "where", "who", "how", "many", "much", "there", "their",
    "they", "them", "he", "she", "his", "her", "it", "its", "in", "on",
    "at", "to", "of", "for", "after", "before", "in", "into", "with",
    "and", "or", "be", "been", "has", "have", "had", "this", "that",
}


def tokenize(text: str) -> list[str]:
    """Lower-case word tokens with stopwords removed (for simple scoring)."""
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


# ---------------------------------------------------------------------------
# Video metadata + entity registry
# ---------------------------------------------------------------------------

@dataclass
class VideoMeta:
    id: str
    title: str
    duration_s: float
    fps: float = 30.0
    width: int = 0
    height: int = 0
    source: str = ""          # file path / URL the bundle was built from
    game: str = ""            # e.g. "GTA V RP (NoPixel-style)", "Valorant"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VideoMeta":
        return cls(
            id=d["id"], title=d.get("title", d["id"]),
            duration_s=float(d.get("duration_s", 0.0)),
            fps=float(d.get("fps", 30.0)), width=int(d.get("width", 0)),
            height=int(d.get("height", 0)), source=d.get("source", ""),
            game=d.get("game", ""))


@dataclass
class Entity:
    """A person / character that can be tracked across modalities."""
    name: str                                  # canonical name, e.g. "Jean Paul"
    aliases: list[str] = field(default_factory=list)   # ["JP", "Jean"]
    kind: str = "character"                    # character | streamer | viewer

    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Entity":
        return cls(name=d["name"], aliases=list(d.get("aliases", [])),
                   kind=d.get("kind", "character"))


# ---------------------------------------------------------------------------
# Records — one base dataclass, typed subclasses per modality
# ---------------------------------------------------------------------------

@dataclass
class Record:
    """Base record. ``kind`` is the serialisation discriminator."""
    t_start: float
    t_end: float
    modality: str = ""                 # video | transcript | chat | event | game
    type: str = ""                     # modality-specific type, e.g. "sub"
    entities: list[str] = field(default_factory=list)
    text: str = ""
    confidence: float = 1.0
    payload: dict[str, Any] = field(default_factory=dict)
    kind: str = "record"
    evidence_id: str = ""
    source_type: str = ""

    # -- serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Record":
        sub = _KIND_TO_CLASS.get(d.get("kind", ""), Record)
        fields = sub.__dataclass_fields__
        kwargs = {k: d[k] for k in fields if k in d}
        kwargs.setdefault("t_start", 0.0)
        kwargs.setdefault("t_end", kwargs["t_start"])
        return sub(**kwargs)  # type: ignore[arg-type]

    # -- convenience --------------------------------------------------------
    @property
    def mid(self) -> float:
        return (self.t_start + self.t_end) / 2.0

    @property
    def completion_s(self) -> float:
        """A chunk is evidence only after its ENTIRE input window completed."""
        try:
            values = [float(self.t_start), float(self.t_end),
                      float(self.payload.get("window_end", self.t_end))]
            return max(values) if all(math.isfinite(v) for v in values) else math.inf
        except (TypeError, ValueError):
            return math.inf

    @property
    def provenance(self) -> str:
        return self.source_type or {"scene": "observed", "transcript": "spoken",
                                   "chat": "chat", "stream_event": "stream_event",
                                   "game_event": "game_event"}.get(self.kind, "unknown")

    def searchable_text(self) -> str:
        bits = [self.text, self.type, " ".join(self.entities)]
        for attr in ("speaker", "user", "location", "activity", "caption"):
            val = getattr(self, attr, None)
            if val:
                bits.append(str(val))
        bits.append(" ".join(str(v) for v in self.payload.values()))
        return " ".join(bits)


@dataclass
class TranscriptSegment(Record):
    """One utterance / caption chunk. ``speaker`` is a label or 'unknown'."""
    speaker: str = "unknown"
    kind: str = "transcript"
    modality: str = "transcript"
    type: str = "utterance"


@dataclass
class ChatMessage(Record):
    user: str = ""
    emotes: list[str] = field(default_factory=list)
    kind: str = "chat"
    modality: str = "chat"
    type: str = "message"


@dataclass
class StreamEvent(Record):
    """Twitch-level event: follow/sub/raid/ban/automod_hold/hype_train/
    ad_break/clip_created."""
    kind: str = "stream_event"
    modality: str = "event"


@dataclass
class GameEvent(Record):
    """Game-state event: kill/death/round_start/round_end/clutch/
    alive_count_change/money/wanted_level.  Counts live in ``payload``,
    e.g. {"allies_alive": 1, "enemies_alive": 5}."""
    kind: str = "game_event"
    modality: str = "game"


@dataclass
class SceneSegment(Record):
    """What the video shows for a span — in v0 this stands in for the
    VLM/vision pipeline (SAM/YOLO tubes + captions in the live version)."""
    location: str = ""        # zone name, e.g. "Fleeca Bank"
    activity: str = ""        # e.g. "bank_robbery", "police_chase"
    caption: str = ""         # human-readable description of the span
    kind: str = "scene"
    modality: str = "video"
    type: str = "scene"


_KIND_TO_CLASS: dict[str, type[Record]] = {
    "record": Record,
    "transcript": TranscriptSegment,
    "chat": ChatMessage,
    "stream_event": StreamEvent,
    "game_event": GameEvent,
    "scene": SceneSegment,
}


# ---------------------------------------------------------------------------
# Tube tracks (summaries) — shape borrowed from Tube's tubes.json
# ---------------------------------------------------------------------------

@dataclass
class TubeTrack:
    """Summary of one tracked entity/object through time.

    In the original Tube, a track in tubes.json has ``id``, ``class``,
    ``first_frame``/``last_frame`` and per-frame contours.  On a stream
    clock (seconds, not frames) the v0 summary keeps the identity, the
    time span, and the zones visited — geometry tools in v0 run over
    ``zones_visited`` + SceneSegments rather than per-frame contours.
    """
    track_id: int
    label: str                                   # Tube called this "class"
    first_t: float
    last_t: float
    zones_visited: list[str] = field(default_factory=list)
    entity: str | None = None                    # canonical entity name
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TubeTrack":
        return cls(track_id=int(d["track_id"]),
                   label=d.get("label", d.get("class", "")),
                   first_t=float(d.get("first_t", d.get("first_frame", 0))),
                   last_t=float(d.get("last_t", d.get("last_frame", 0))),
                   zones_visited=list(d.get("zones_visited", [])),
                   entity=d.get("entity"),
                   payload=dict(d.get("payload", {})))
