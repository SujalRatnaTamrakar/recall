"""Local, run-scoped moment links. A later question does not invalidate a link."""
import copy
import secrets
from .runtime import finite_time


class Moments:
    def __init__(self, app):
        self.app = app
        self.items = {}

    def create(self, data):
        app = self.app
        with app.lock:
            answer = app.answer or {}
            if data.get("run_id") != app.run_id or answer.get("run_id") != app.run_id:
                raise ValueError("The replay changed. Ask again to create a moment.")
            citation = next((c for c in answer.get("citations", [])
                             if c.get("evidence_id") == data.get("evidence_id") and c.get("evidence_id")), None)
            if citation is None or not app.video_path:
                raise ValueError("Choose a supporting moment from the current answer.")
            cutoff = min(app.processed_through_s, finite_time(answer.get("through_s", 0)))
            t = finite_time(citation.get("t", 0))
            if t > cutoff:
                raise ValueError("This moment is not processed yet.")
            start = t if cutoff - t >= 1 else max(0, t - 15)
            end = min(start + 60, cutoff)
            if end <= start:
                raise ValueError("There is not enough processed footage for a moment yet.")
            self.items = {k: v for k, v in self.items.items() if v["run_id"] == app.run_id}
            key = secrets.token_urlsafe(18)
            item = {"id": key, "run_id": app.run_id, "start_s": start, "end_s": end,
                    "evidence_s": t, "through_s": cutoff, "title": app.memory.video.title,
                    "excerpt": str(citation.get("excerpt") or citation.get("label", ""))[:1400],
                    "source_type": citation.get("source_type", "source"),
                    "url": f"/?view=moment&id={key}", "video_url": "/video",
                    "local_only": True, "expires_on_restart": True}
            self.items[key] = item
            if len(self.items) > 256:
                del self.items[next(iter(self.items))]
            return copy.deepcopy(item)

    def get(self, key):
        with self.app.lock:
            item = self.items.get(key)
            if not item or item["run_id"] != self.app.run_id or item["end_s"] > self.app.processed_through_s:
                raise ValueError("This moment expired or the replay changed. Ask again for a new link.")
            return copy.deepcopy(item)

    def from_evidence(self, data):
        """Private review clip, independent of the public/shared answer."""
        app = self.app
        with app.lock:
            if data.get('run_id') != app.run_id:
                raise ValueError('The replay changed. Refresh this workspace.')
            cutoff = min(app.processed_through_s, finite_time(data.get('through_s', app.processed_through_s)))
            record = next((r for r in app.memory.bounded(cutoff).records
                           if r.evidence_id == data.get('evidence_id')), None)
            if record is None or not app.video_path:
                raise ValueError('This source moment is unavailable in the current replay.')
            start = max(0, record.t_start - 15)
            end = min(start + 60, cutoff)
            if end <= start:
                raise ValueError('There is not enough processed footage to preview.')
            self.items = {k: v for k, v in self.items.items() if v['run_id'] == app.run_id}
            key = secrets.token_urlsafe(18)
            item = {'id': key, 'run_id': app.run_id, 'start_s': start, 'end_s': end,
                    'evidence_s': record.t_start, 'through_s': cutoff, 'title': app.memory.video.title,
                    'excerpt': (getattr(record, 'caption', '') or record.text)[:1400],
                    'source_type': record.provenance, 'url': f'/?view=moment&id={key}',
                    'video_url': '/video', 'local_only': True, 'expires_on_restart': True}
            self.items[key] = item
            if len(self.items) > 256:
                del self.items[next(iter(self.items))]
            return copy.deepcopy(item)
