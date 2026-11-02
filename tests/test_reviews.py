"""Role workspaces preserve source bounds, human provenance and public privacy."""
import json
from test_server import _Running, _owned_video
from tubelive.reviews import Reviews
from tubelive.runtime import ReplaySession
from tubelive.schema import TranscriptSegment, VideoMeta
from tubelive.store import StreamMemory


def create(server, role='moderator', **changes):
    _, _, board = server.json(f'/api/reviews?role={role}')
    source = server.session.memory.records[0]
    return server.json('/api/reviews', payload={'run_id': board['run_id'], 'role': role,
        'revision': board['revision'], 'action': 'create', 'title': 'Inspect this statement',
        'evidence_ids': [source.evidence_id], **changes})


def test_role_notes_persist_separately_and_never_enter_content_index():
    with _Running() as server:
        before = [r.to_dict() for r in server.session.memory.records]
        code, _, board = create(server, notes='Human interpretation, not a source fact.')
        assert code == 200 and len(board['items']) == 1
        item = board['items'][0]
        assert item['status'] == 'needs_review'
        assert item['sources'][0]['evidence_id'] == before[0]['evidence_id']
        assert not server.json('/api/reviews?role=campaign')[2]['items']
        assert [r.to_dict() for r in server.session.memory.records] == before
        assert Reviews(server.session).get('moderator')['items'] == board['items']
        file = server.session.cache_dir / 'runs' / board['run_id'] / 'reviews-moderator.json'
        assert json.loads(file.read_text())['items'][0]['notes'].startswith('Human')


def test_review_auth_run_revision_and_status_checks():
    with _Running() as server:
        _, _, board = create(server)
        payload = {'run_id': board['run_id'], 'role': 'moderator', 'revision': board['revision'],
                   'action': 'update', 'id': board['items'][0]['id'], 'status': 'reviewed', 'notes': 'Checked footage.'}
        assert server.json('/api/reviews', payload=payload, authorized=False)[0] == 403
        assert server.json('/api/reviews', payload={**payload, 'run_id':'old'})[0] == 400
        assert server.json('/api/reviews', payload={**payload, 'status':'ban'})[0] == 400
        code, _, saved = server.json('/api/reviews', payload=payload)
        assert code == 200 and saved['items'][0]['status'] == 'reviewed'
        assert server.json('/api/reviews', payload=payload)[0] == 400
        assert server.json('/api/reviews?role=unknown')[0] == 400
        # Removal changes only the board.
        code, _, empty = server.json('/api/reviews', payload={**payload, 'revision':saved['revision'], 'action':'remove'})
        assert code == 200 and not empty['items']


def test_campaign_brief_and_human_confirmation_are_role_specific():
    with _Running() as server:
        _, _, board = server.json('/api/reviews?role=campaign')
        base = {'run_id':board['run_id'],'role':'campaign','revision':0}
        code, _, board = server.json('/api/reviews', payload={**base, 'action':'brief',
            'brief':{'name':'Boat launch','terms':'Flask','checklist':['Show flask','Explain sponsor']}})
        assert code == 200 and board['brief']['terms'] == 'Flask'
        code, _, board = create(server, role='campaign', requirement='Show flask')
        assert code == 200 and board['items'][0]['status'] == 'needs_review'
        code, _, board = server.json('/api/reviews', payload={**base, 'revision':board['revision'],
            'action':'update','id':board['items'][0]['id'],'status':'confirmed'})
        assert code == 200 and board['human_review'] is True
        assert not any(key in board for key in ('impressions','conversions','roi'))


def test_whole_window_cutoff_and_restarted_run_hide_review_evidence(tmp_path):
    memory = StreamMemory(VideoMeta('bounds','Bounded recording',60),[
        TranscriptSegment(2,3,text='A name',payload={'window_end':10}),
        TranscriptSegment(40,41,text='Later',payload={'window_end':50})])
    app = ReplaySession(memory=memory,cache_dir=tmp_path)
    with _Running(session=app) as server:
        assert create(server, through_s=9)[0] == 400
        assert create(server, evidence_ids=['invented'])[0] == 400
        assert create(server, through_s=10)[0] == 200
        old = app.run_id
        with app.lock:
            app.playback_s = 9
        assert not server.json('/api/reviews?role=moderator')[2]['items']
        with app.lock:
            app.run_id = 'fresh-run'
        assert not server.json('/api/reviews?role=moderator')[2]['items']
        assert (tmp_path/'runs'/old/'reviews-moderator.json').exists()


def test_private_question_does_not_replace_shared_answer():
    with _Running() as server:
        _, _, public = server.json('/api/ask',payload={'question':'What happened in the last minute?'})
        prior = server.session.shared()
        code, _, answer = server.json('/api/ask',payload={'question':'What did they plan earlier?',
            'conversation_id':'moderator-local','share':False})
        assert code == 200 and answer['run_id'] == public['run_id']
        assert server.session.shared() == prior
        assert server.json('/api/ask',payload={'question':'what happened?','share':'no'})[0] == 400


def test_private_context_clip_ignores_global_answer_and_stays_bounded():
    with _owned_video() as (video, folder):
        memory = StreamMemory(VideoMeta('clip','Owned short video',8),[
            TranscriptSegment(2,3,text='A brand mention',payload={'window_end':4})])
        app=ReplaySession(memory=memory,video=video,cache_dir=folder/'cache')
        with _Running(session=app) as server:
            record=memory.records[0]
            data={'run_id':app.run_id,'evidence_id':record.evidence_id,'through_s':6}
            assert server.json('/api/reviews/moment',payload={**data,'through_s':3})[0] == 400
            code, _, moment=server.json('/api/reviews/moment',payload=data)
            assert code == 200 and moment['end_s'] == 6 and moment['start_s'] == 0
            assert app.answer is None
            code, headers, clip=server.request('/api/clip',payload={'run_id':app.run_id,'moment_id':moment['id'], 'start_s':1,'end_s':4})
            assert code == 200 and len(clip)>100 and headers['Content-Type']=='video/mp4'
            assert server.json('/api/clip',payload={'run_id':app.run_id,'moment_id':moment['id'], 'start_s':1,'end_s':7})[0] == 400
            with app.lock:
                app.run_id='new-run'
            assert server.json(f"/api/moments/{moment['id']}")[0] == 400
