"""Conversational publication still requires bounded, checked source claims."""
from tubelive.ask import answer_with_model
from tubelive.schema import TranscriptSegment, SceneSegment, VideoMeta
from tubelive.store import StreamMemory

class Conversational:
    def __init__(self, text='They are planning to meet Zeek and discuss a heist.', kind='fact', accepted=None):
        self.text, self.kind = text, kind
        self.accepted = [0] if accepted is None else accepted
        self.evidence = []
        self.inference = None
    def answer_from_evidence(self, question, evidence, *, allow_inference=False):
        self.evidence = evidence
        self.inference = allow_inference
        return {'claims':[{'text':self.text,'kind':self.kind,'evidence_ids':[evidence[0]['evidence_id']]}],'insufficient':False}
    def verify_claims(self, question, claims, evidence, *, allow_inference=False):
        assert evidence == self.evidence
        return self.accepted

def memory():
    return StreamMemory(VideoMeta('test','Synthetic test',150),[
        TranscriptSegment(5,8,text='We plan to meet Zeek to discuss the heist.',payload={'window_end':10}),
        SceneSegment(14,14,caption='A character stands outside a cafe.'),
        TranscriptSegment(120,125,text='Future secret: the heist failed.',payload={'window_end':130}),
    ],run_id='test',through_s=130)

def test_natural_reply_keeps_source_links_and_future_hidden():
    model = Conversational()
    answer = answer_with_model(memory(),'whats happening',model,now=15)
    assert answer.summary == model.text and answer.source_label == 'GROUNDED'
    assert answer.citations and all(c.t <= 15 for c in answer.citations)
    assert all(e['t_end'] <= 15 and 'Future secret' not in e['excerpt'] for e in model.evidence)
    assert not model.inference

def test_verified_rejection_prevents_valid_id_invented_relationship():
    model = Conversational('They met Jason because the police arrived.',accepted=[])
    answer = answer_with_model(memory(),'whats happening',model,now=15)
    assert answer.status == 'answered' and answer.source_label == 'EXTRACTIVE'
    assert 'Jason' not in answer.summary and 'police' not in answer.summary

def test_meeting_retrieves_meet_without_relying_on_model_rewrite():
    from tubelive.retrieval import retrieve
    hits = retrieve(memory().bounded(15),'Who is the speaker meeting?')
    assert any('meet Zeek' in record.text for record in hits)

def test_prediction_requires_opt_in_hedging_and_only_seen_evidence():
    model = Conversational('They might meet Zeek next to discuss the heist.','inference')
    answer = answer_with_model(memory(),'What might happen next?',model,now=15)
    assert answer.status == 'answered' and answer.source_label == 'INFERENCE' and model.inference
    assert all(c.t <= 15 for c in answer.citations)
    disabled = answer_with_model(memory(),'What might happen next?',model,now=15,allow_predictions=False)
    assert not model.inference and disabled.status == 'insufficient_evidence'
    certain = Conversational('The heist will succeed.','inference')
    assert answer_with_model(memory(),'What will happen next?',certain,now=15).status == 'insufficient_evidence'

def test_old_plan_is_background_not_current_activity():
    m = memory()
    m.append(SceneSegment(100,100,caption='An empty beach.'))
    model = Conversational('An empty beach is visible.')
    answer_with_model(m,'whats happening',model,now=100)
    old = [e for e in model.evidence if e['t_start']==5]
    assert old and all(e['context_role']=='background' for e in old)
    assert all(e['t_end'] <= 100 for e in model.evidence)

def test_source_window_and_empty_memory_skip_natural_model():
    model = Conversational()
    assert answer_with_model(memory(),'whats happening',model,now=9).status == 'insufficient_evidence'
    assert not model.evidence

def test_invalid_verifier_indices_fail_closed():
    for invalid in [[True],[99],['0']]:
        assert answer_with_model(memory(),'whats happening',Conversational(accepted=invalid),now=15).status == 'error'

def test_rejected_paraphrase_can_offer_exact_relevant_words_without_repeating_claim():
    model=Conversational('They met Jason and finished the heist.',accepted=[])
    answer=answer_with_model(memory(),'Who are they meeting?',model,now=15)
    assert answer.source_label=='EXTRACTIVE' and 'We plan to meet Zeek' in answer.summary
    assert 'Jason' not in answer.summary and 'finished' not in answer.summary

def test_visual_records_cannot_be_presented_as_speech_even_if_verifier_accepts():
    m=StreamMemory(VideoMeta('v','Synthetic',1),[SceneSegment(0,0,caption='A red car.')])
    answer=answer_with_model(m,'whats happening',Conversational('The speaker said there is a red car.'))
    assert answer.status=='insufficient_evidence' and not answer.citations
