"""Human review boards. Notes stay outside the searchable stream memory."""
from __future__ import annotations
import copy
import json
import secrets
from datetime import datetime, timezone
from .runtime import finite_time
from .story import evidence_row

ROLES = {
    'moderator': ('needs_review', 'reviewed', 'follow_up'),
    'campaign': ('needs_review', 'confirmed', 'follow_up'),
    'editor': ('shortlisted', 'selected', 'rejected'),
}


def text(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'Use text no longer than {limit} characters.')
    return value.strip()


class Reviews:
    def __init__(self, app):
        self.app = app
        self.run = ''
        self.boards = {}

    def _board(self, role):
        if role not in ROLES:
            raise ValueError('Choose moderator, campaign, or editor.')
        if self.run != self.app.run_id:
            self.run, self.boards = self.app.run_id, {}
        if role not in self.boards:
            path = self.app.cache_dir / 'runs' / self.run / f'reviews-{role}.json'
            board = {'run_id': self.run, 'role': role, 'revision': 0,
                     'brief': {'name': '', 'terms': '', 'checklist': []}, 'items': []}
            if path.exists():
                saved = json.loads(path.read_text(encoding='utf-8'))
                if saved.get('run_id') == self.run and saved.get('role') == role:
                    board = saved
            self.boards[role] = board
        return self.boards[role]

    def get(self, role):
        with self.app.lock:
            board = copy.deepcopy(self._board(role))
            cutoff = self.app.processed_through_s
            available = {r.evidence_id for r in self.app.memory.bounded(cutoff).records}
            board['items'] = [i for i in board['items'] if i['through_s'] <= cutoff
                              and all(s['evidence_id'] in available for s in i['sources'])]
            board.update(through_s=cutoff, title=self.app.memory.video.title,
                         coverage_gaps=self.app.memory.bounded(cutoff).coverage_gaps,
                         local_only=True, human_review=True)
            return board

    def mutate(self, data):
        app = self.app
        with app.lock:
            if data.get('run_id') != app.run_id:
                raise ValueError('The replay changed. Refresh this workspace.')
            role = data.get('role')
            original = self._board(role)
            if data.get('revision') != original['revision']:
                raise ValueError('This board changed in another tab. Refresh the board before saving.')
            board = copy.deepcopy(original)
            action = data.get('action')
            if action == 'brief':
                brief = data.get('brief')
                if not isinstance(brief, dict):
                    raise ValueError('Provide a review brief.')
                lines = brief.get('checklist', [])
                if not isinstance(lines, list) or len(lines) > 12:
                    raise ValueError('Use up to 12 checklist items.')
                board['brief'] = {'name': text(brief.get('name', ''), 120),
                                  'terms': text(brief.get('terms', ''), 400),
                                  'checklist': list(dict.fromkeys(text(line, 200) for line in lines if line))}
            elif action == 'create':
                if len(board['items']) >= 150:
                    raise ValueError('This board has 150 items. Export it and remove finished items.')
                ids = data.get('evidence_ids')
                if not isinstance(ids, list) or not 1 <= len(ids) <= 12 or not all(isinstance(i, str) for i in ids):
                    raise ValueError('Choose one to twelve source moments.')
                cutoff = min(app.processed_through_s, finite_time(data.get('through_s', app.processed_through_s)))
                records = {r.evidence_id: r for r in app.memory.bounded(cutoff).records}
                if not all(i in records for i in ids):
                    raise ValueError('A source moment is unavailable at this cutoff.')
                sources = [evidence_row(records[i]) for i in dict.fromkeys(ids)]
                board['items'].append({'id': secrets.token_hex(12), 'title': text(data.get('title', 'Saved moment'), 200),
                    'notes': text(data.get('notes', ''), 3000), 'requirement': text(data.get('requirement', ''), 200),
                    'status': ROLES[role][0], 'sources': sources, 'through_s': cutoff,
                    'created_at': datetime.now(timezone.utc).isoformat()})
            elif action in {'update', 'remove'}:
                item = next((i for i in board['items'] if i['id'] == data.get('id')), None)
                if item is None:
                    raise ValueError('This saved moment is unavailable.')
                if action == 'remove':
                    board['items'].remove(item)
                else:
                    state = data.get('status', item['status'])
                    if state not in ROLES[role]:
                        raise ValueError('Choose a valid review status.')
                    item.update(status=state, notes=text(data.get('notes', item['notes']), 3000),
                                updated_at=datetime.now(timezone.utc).isoformat())
            else:
                raise ValueError('Unknown board action.')
            board['revision'] += 1
            path = app.cache_dir / 'runs' / app.run_id / f'reviews-{role}.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            pending = path.with_suffix('.tmp')
            pending.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding='utf-8')
            pending.replace(path)
            self.boards[role] = board
            app.diagnostics.event('review_saved', run_id=app.run_id, operation=action)
            return self.get(role)
