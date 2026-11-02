"""Long-history selection, creator context, persistence and time boundaries."""
import copy
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from tubelive.ask import answer_with_model
from tubelive.schema import TranscriptSegment, SceneSegment, VideoMeta
from tubelive.store import StreamMemory
from tubelive.story import EMPTY_PROFILE, profile_at, validate_profile, validate_episode
from tubelive.runtime import ReplaySession


class Capture:
    supports_stream_context = True
    def __init__(self): self.evidence=[]; self.context={}; self.images=[]
    def answer_from_evidence(self,question,evidence,**kwargs):
        self.evidence=evidence; self.context=kwargs['stream_context']; self.images=kwargs['images']
        return {'claims':[],'insufficient':True}
    def verify_claims(self,*args,**kwargs): return []


def long_memory():
    records=[TranscriptSegment(2,5,text='My name is River. My character is called Ash.'),
             TranscriptSegment(25,29,text='These migratory birds fly south when winter approaches.'),
             TranscriptSegment(120,124,text='We plan to meet Zeek at the cafe about the heist.'),
             TranscriptSegment(800,805,text='The meeting with Zeek is cancelled. We are going home instead.')]
    for t in range(10,1201,10):
        records += [SceneSegment(t,t,caption='A character applies a bandage.' if t>=1140 else 'A street with buildings.'),
                    TranscriptSegment(t-3,t-1,text='I need a bandage.' if t>=1140 else 'The weather is cloudy.')]
    return StreamMemory(VideoMeta('long','Synthetic 20 minutes',1200),records,run_id='long',through_s=1200)


def add_event(memory, index, category, known, supersedes=None):
    record=next(r for r in memory.records if r.t_start==index and r.kind=='transcript')
    result={'events':[{'summary':record.text,'category':category,'evidence_ids':[record.evidence_id], 'supersedes':supersedes or []}]}
    event=validate_episode(result,memory,max(0,index-1),known)[0]
    memory.story_events.append(event); memory.story_through_s=max(memory.story_through_s,known)
    return event


def test_current_preserves_opening_identity_and_later_cancelled_plan():
    memory=long_memory()
    add_event(memory,2,'introduction',60)
    plan=add_event(memory,120,'plan',180)
    add_event(memory,800,'change',840,[plan['id']])
    model=Capture(); answer=answer_with_model(memory,'whats happening',model)
    texts=' '.join(e['excerpt'] for e in model.evidence)
    assert 'called Ash' in texts and 'cancelled' in texts and 'bandage' in texts
    assert all(e['context_role']=='background' for e in model.evidence if e['t_end']<1140)
    assert answer.context['selected_records']==len(model.evidence)
    assert any(e['category']=='change' for e in model.context['story_notes'])


def test_so_far_recap_covers_whole_history_but_explicit_window_stays_bounded():
    memory=long_memory(); model=Capture()
    answer=answer_with_model(memory,'Summarize the entire stream so far',model)
    assert any('context_window(0.00, 1200.00)' in item for item in answer.tool_trace)
    assert min(e['t_start'] for e in model.evidence)<60
    assert any(e['t_start']==120 for e in model.evidence)
    assert any(e['t_start']==800 for e in model.evidence)
    assert max(e['t_end'] for e in model.evidence)==1200
    answer_with_model(memory,'Summarize the last 30 seconds',model)
    assert all(e['t_end']>=1170 for e in model.evidence)


def test_future_profile_alias_story_and_images_cannot_leak():
    memory=long_memory()
    profile={**copy.deepcopy(EMPTY_PROFILE),'streamer':'FutureName','character':'FutureAlias'}
    memory.profile_versions=[{'version':1,'known_s':1100,'data':profile}]
    add_event(memory,800,'change',840)
    seen=[]
    model=Capture()
    answer=answer_with_model(memory,'whats happening',model,now=100,
                            frame_loader=lambda r: seen.append(r.completion_s) or 'image')
    assert model.context['creator_profile']['streamer']==''
    assert not model.context['story_notes']
    assert all(t<=100 for t in seen)
    assert 'FutureAlias' not in str(model.evidence)+str(model.context)
    assert all(r['end_s']<=100 for r in answer.context['records'])


def test_creator_speaker_label_only_applies_to_selected_excerpt_and_after_save():
    memory=long_memory()
    record=next(r for r in memory.records if r.t_start==120 and r.kind=='transcript')
    profile=validate_profile({**copy.deepcopy(EMPTY_PROFILE),'streamer':'River','character':'Ash',
        'speaker_bindings':[{'evidence_id':record.evidence_id,'name':'River'}]},memory.records)
    memory.profile_versions=[{'version':1,'known_s':130,'data':profile}]
    model=Capture(); answer_with_model(memory,'Who are they planning to meet?',model,now=200)
    labelled=[r for r in model.evidence if r.get('speaker')=='River']
    assert len(labelled)==1 and labelled[0]['evidence_id']==record.evidence_id
    assert labelled[0]['speaker_basis']=='creator_label_for_this_excerpt_only'
    assert profile_at(memory.bounded(125).profile_versions,125)['data']['streamer']==''


def test_episode_rejects_invented_future_ids_and_unknown_supersession():
    memory=long_memory(); original=next(r for r in memory.records if r.t_start==120 and r.kind=='transcript')
    for ids, supersedes in [(['invented'],[]),([original.evidence_id],['unknown'])]:
        with pytest.raises(ValueError):
            validate_episode({'events':[{'summary':'A plan','category':'change','evidence_ids':ids,'supersedes':supersedes}]},memory,100,180)
    with pytest.raises(ValueError):
        validate_episode({'events':[{'summary':'A plan','category':'plan','evidence_ids':[original.evidence_id]}]},memory,0,60)


def test_old_bundle_and_new_story_round_trip(tmp_path):
    memory=long_memory(); event=add_event(memory,2,'introduction',60)
    memory.profile_versions=[{'version':1,'known_s':20,'data':copy.deepcopy(EMPTY_PROFILE)}]
    loaded=StreamMemory.load(memory.save(tmp_path/'memory.json'))
    assert loaded.story_events==memory.story_events and loaded.profile_versions==memory.profile_versions
    assert not loaded.bounded(30).story_events
    assert loaded.bounded(60).story_events[0]['id']==event['id']
    raw=memory._raw_dict(); raw.pop('story_events');raw.pop('profile_versions');raw.pop('story_through_s')
    import json
    (tmp_path/'legacy.json').write_text(json.dumps(raw))
    legacy=StreamMemory.load(tmp_path/'legacy.json')
    assert not legacy.story_events and len(legacy.records)==len(memory.records)


def test_followup_uses_only_conversation_references_within_cutoff():
    memory=long_memory(); old=next(r for r in memory.records if r.t_start==25 and r.kind=='transcript')
    future=next(r for r in memory.records if r.t_start==800 and r.kind=='transcript')
    model=Capture()
    answer_with_model(memory,'What did they say about that?',model,now=100,
        conversation=[{'question':'What about migratory birds?','answer':'untrusted previous answer',
                       'evidence_ids':[old.evidence_id,future.evidence_id],'through_s':80}])
    assert any(e['evidence_id']==old.evidence_id for e in model.evidence)
    assert not any(e['evidence_id']==future.evidence_id for e in model.evidence)


def test_background_story_failure_does_not_stop_processing(tmp_path):
    class Failing(Capture):
        def summarize_episode(self,*args): raise RuntimeError('bad model output')
    memory=long_memory()
    app=ReplaySession(memory=memory,cache_dir=tmp_path,model=Failing())
    try:
        with app.condition:
            assert app.condition.wait_for(lambda:app.memory.story_through_s>=60,timeout=4)
        assert app.gap is None and app.error is None
        assert app.memory.records
    finally: app.close()


def test_profile_persists_and_bindings_are_cleared_on_restart(tmp_path):
    with patch('tubelive.runtime.probe',return_value={'duration_s':1200,'has_audio':True}):
        video=tmp_path/'owned.mp4';video.write_bytes(b'test')
        app=ReplaySession(memory=long_memory(),video=video,cache_dir=tmp_path/'cache')
        try:
            record=next(r for r in app.memory.records if r.kind=='transcript')
            result=app.set_stream_context({'run_id':app.run_id,'profile':{**copy.deepcopy(EMPTY_PROFILE),
                'streamer':'River','speaker_bindings':[{'evidence_id':record.evidence_id,'name':'River'}]}})
            old_run=app.run_id
            assert result['profile']['streamer']=='River'
            assert (tmp_path/'cache'/'runs'/old_run/'memory.json').is_file()
            app.control('restart')
            assert app.run_id!=old_run and not app.memory.records and not app.memory.story_events
            assert app.stream_context()['profile']['streamer']=='River'
            assert not app.stream_context()['profile']['speaker_bindings']
            with pytest.raises(ValueError): app.set_stream_context({'run_id':old_run,'profile':EMPTY_PROFILE})
        finally: app.close()


def test_semantic_results_cannot_reintroduce_future_records():
    class Semantic:
        ready=True
        def search(self,question,records,limit):
            assert all(r.completion_s<=60 for r in records)
            return ['future-id',next(r.evidence_id for r in records if 'migratory birds' in r.text)]
    from tubelive.retrieval import retrieve
    memory=long_memory();memory.semantic=Semantic()
    hits=retrieve(memory.bounded(60),'seasonal migration')
    assert hits and all(r.completion_s<=60 for r in hits)


def test_roster_cannot_assign_unknown_speech_even_if_model_verifier_accepts():
    class Incorrect(Capture):
        def answer_from_evidence(self,question,evidence,**kwargs):
            self.evidence=evidence
            source=next(e for e in evidence if 'migratory birds' in e['excerpt'])
            return {'claims':[{'text':'Ash said migratory birds fly south.','kind':'fact',
                                'evidence_ids':[source['evidence_id']]}],'insufficient':False}
        def verify_claims(self,*args,**kwargs): return [0]
    memory=long_memory()
    memory.profile_versions=[{'version':1,'known_s':0,'data':{**copy.deepcopy(EMPTY_PROFILE),'character':'Ash'}}]
    answer=answer_with_model(memory,'What did they say about migratory birds?',Incorrect())
    assert 'Ash said' not in answer.summary
    assert answer.source_label=='EXTRACTIVE'


def test_alias_search_uses_only_names_known_at_cutoff():
    from tubelive.retrieval import retrieve
    memory=long_memory()
    memory.profile_versions=[{'version':1,'known_s':200,'data':{**copy.deepcopy(EMPTY_PROFILE),
        'cast':[{'name':'Zeek','role':'character','aliases':['Bluebird']}]} }]
    assert retrieve(memory,'Bluebird')
    assert not retrieve(memory.bounded(150),'Bluebird')


def test_verbatim_plan_survives_empty_summary_and_repeat_scenery_is_omitted():
    from tubelive.story import review_episode
    class Empty:
        def summarize_episode(self,evidence,previous,profile):
            assert not any(e['source_type']=='observed' for e in evidence)
            return {'events':[]}
    memory=long_memory()
    events=review_episode(memory.bounded(180),Empty(),120,180)
    assert any(e['category']=='plan' and e.get('verbatim') and 'Zeek' in e['summary'] for e in events)


def test_context_endpoint_validates_run_and_preserves_record_count():
    from test_server import _Running
    with _Running() as server:
        status,_,body=server.json('/api/context')
        assert status==200
        before=len(server.session.memory.records)
        status,_,saved=server.json('/api/context',payload={'run_id':body['run_id'],
            'profile':{**body['profile'],'streamer':'Test broadcaster','character':'Test avatar',
                       'title':'A cooking stream','description':'An introduction to making bread.'}})
        assert status==200 and saved['profile']['character']=='Test avatar'
        assert saved['profile']['description']=='An introduction to making bread.'
        assert saved['profile']['title']=='A cooking stream' and 'recording_title' in saved
        assert len(server.session.memory.records)==before
        status,_,_=server.json('/api/context',payload={'run_id':'expired','profile':body['profile']})
        assert status==400
        status,_,_=server.json('/api/context',payload={'run_id':body['run_id'],'profile':body['profile']},authorized=False)
        assert status==403
