"""Speech facts must survive visual context and fragmented retrieval."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from tubelive.ask import answer_with_model
from tubelive.localai import LocalAI
from tubelive.schema import SceneSegment, TranscriptSegment, VideoMeta
from tubelive.store import StreamMemory
from tubelive.story import transcript_neighbors, valid_contextual_claim


class ScheduleModel:
    supports_stream_context=True
    def __init__(self,window=0):self.rows=[];self.window=window
    def interpret_question(self,question):
        return {'intent':'search','modality':'spoken','terms':['stage'],'window_s':self.window}
    def answer_from_evidence(self,question,evidence,**kwargs):
        self.rows=evidence
        assert kwargs['stream_context']['modality']=='spoken'
        subject=next((r for r in evidence if 'second stage' in r['excerpt']),None)
        clock=next((r for r in evidence if '6:50' in r['excerpt']),None)
        if not subject or not clock:return {'claims':[],'insufficient':True}
        return {'claims':[{'text':'They said the second stage was at 6:50 a.m. Pacific time.','kind':'fact',
            'evidence_ids':[subject['evidence_id'],clock['evidence_id']]}],'insufficient':False}
    def verify_claims(self,*args,**kwargs):return [0]


def split_memory():
    memory = StreamMemory(VideoMeta('split','A spacecraft broadcast',50),[
        TranscriptSegment(16,19.9,text='The second stage happened this morning.',payload={'window_end':20}),
        TranscriptSegment(20,24,text='At 6:50 a.m. Pacific time.',payload={'window_end':30}),
        SceneSegment(25,25,caption='The word stage appears on a diagram.'),
    ],run_id='split',through_s=30)
    memory.semantic=SimpleNamespace(ready=True)
    return memory


def only_subject(memory,*args,**kwargs):
    return [r for r in memory.records if 'second stage' in r.text]


def test_schedule_reads_adjacent_fragment_when_search_only_returns_subject():
    model=ScheduleModel()
    with patch('tubelive.retrieval.retrieve',side_effect=only_subject):
        result=answer_with_model(split_memory(),'When was the second stage?',model,now=30)
    assert result.status=='answered' and '6:50' in result.summary
    assert len(result.citations)==2
    assert all(r['source_type']=='spoken' for r in model.rows)
    assert [r['t_start'] for r in model.rows]==sorted(r['t_start'] for r in model.rows)


def test_sentence_continuation_needs_its_entire_audio_window_completed():
    model=ScheduleModel()
    with patch('tubelive.retrieval.retrieve',side_effect=only_subject):
        result=answer_with_model(split_memory(),'When was the second stage?',model,now=25)
    assert result.status=='insufficient_evidence' and not result.citations
    assert all('6:50' not in r['excerpt'] for r in model.rows)


def test_neighbors_do_not_cross_audio_coverage_holes_or_explicit_search_window():
    m=split_memory();seed=m.records[0]
    m.add_coverage_gap(19.9,20,'audio','test_gap')
    assert [r.evidence_id for r in transcript_neighbors(m,[seed])]==[seed.evidence_id]
    m.coverage_gaps=[]
    assert [r.evidence_id for r in transcript_neighbors(m,[seed],eligible=[seed])]==[seed.evidence_id]


def test_spoken_schema_excludes_visual_interpretations_even_with_background_scene():
    client=LocalAI();calls=[]
    def request(path,payload):
        calls.append(payload)
        return {'message':{'content':json.dumps({'claims':[{'text':'They reported 152 kilometers.',
            'kind':'fact','evidence_ids':['speech']}],'insufficient':False})}}
    client._request=request
    records=[{'evidence_id':'speech','source_type':'spoken','excerpt':'A hundred and fifty two kilometers.',
        't_start':0,'t_end':5},{'evidence_id':'frame','source_type':'observed','excerpt':'A control room.',
        't_start':5,'t_end':5}]
    result=client.answer_from_evidence('How many kilometers did they mention?',records,
        stream_context={'intent':'count','modality':'spoken','recording_context':{'title':'Spacecraft'}})
    schema=calls[0]['format']['properties']['claims']['items']['properties']['kind']
    assert schema['enum']==['fact'] and result['claims'][0]['kind']=='fact'
    assert not valid_contextual_claim({'text':'It appears to be a mission.','evidence_ids':['frame']},
        records,{'modality':'spoken','recording_context':{'title':'Spacecraft'}})


def test_verifier_assesses_support_before_accepting_spoken_fact():
    client=LocalAI();calls=[]
    def request(path,payload):
        calls.append(payload)
        return {'message':{'content':json.dumps({'assessment':'The cited speech explicitly states the distance.','accepted':[0]})}}
    client._request=request
    claims=[{'text':'They reported 152 kilometers.','kind':'fact','evidence_ids':['speech']}]
    evidence=[{'evidence_id':'speech','source_type':'spoken','excerpt':'A hundred and fifty two kilometers away from'}]
    assert client.verify_claims('How far?',claims,evidence,stream_context={'modality':'spoken'})==[0]
    assert 'assessment' in calls[0]['format']['required']
    assert calls[0]['options']['num_predict']<=240
