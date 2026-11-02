"""Recall v0 — multimodal stream memory + grounded Q&A over a recorded
stream video.  Pure Python stdlib; ffmpeg/ffprobe used as CLIs for ingest."""

from .ask import Answer, ask
from .schema import (ChatMessage, Entity, GameEvent, Record, SceneSegment,
                     StreamEvent, TranscriptSegment, TubeTrack, VideoMeta,
                     mmss)
from .store import StreamMemory

__version__ = "0.1.0"
__all__ = [
    "Answer", "ask", "StreamMemory", "VideoMeta", "Entity", "Record",
    "TranscriptSegment", "ChatMessage", "StreamEvent", "GameEvent",
    "SceneSegment", "TubeTrack", "mmss", "__version__",
]
