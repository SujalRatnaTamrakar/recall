"""Trust boundaries independent of authored story fixtures or model services."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tubelive.ask import ask, answer_with_model
from tubelive.schema import VideoMeta, SceneSegment, TranscriptSegment, Entity, TubeTrack, GameEvent
from tubelive.store import StreamMemory
from tubelive.retrieval import EvidenceIndex, retrieve


def memory(records=None, **kwargs):
    return StreamMemory(VideoMeta("test", "Owned recording", 100), records or [], **kwargs)


def test_complete_source_window_not_utterance_start_controls_visibility():
    m = memory([TranscriptSegment(1, 2, text="Next we add blue paper.", payload={"window_end": 10})])
    assert not m.bounded(9).records
    assert ask(m, "what next?", now=9).status == "insufficient_evidence"
    assert ask(m, "what next?", now=10).status == "answered"
    assert m.bounded(10).records[0].completion_s == 10


def test_every_route_has_no_access_to_future_evidence():
    m = memory([SceneSegment(20, 30, text="secret future", caption="bank robbery secret future", entities=["Future"]),
                TranscriptSegment(30, 35, text="Next we use the secret future.", entities=["Future"]),
                GameEvent(40, 40, type="alive_count", payload={"allies_alive": 1, "enemies_alive": 5}),
                GameEvent(50, 50, type="round_end", payload={"result": "win"})],
               entities=[Entity("Future")],
               tracks=[TubeTrack(1, "secret", 20, 50, ["secret zone"], "Future")])
    for question in ["what next?", "what happened in the last 5 minutes?", "what are they doing right now?",
                     "when did the bank robbery happen?", "what happened to Future after robbery?",
                     "how many subs?", "was there a 1v5 win?", "secret future"]:
        answer = ask(m, question, now=5)
        assert not answer.citations, (question, answer.to_dict())
        assert answer.status == "insufficient_evidence", question
        assert answer.through_s == 5
    data = m.to_dict(through_s=5)
    assert not data["records"] and not data["tracks"] and not data["entities"]
    assert data["video"]["duration_s"] == 5


def test_silence_advances_watermark_without_inventing_events():
    m = memory(through_s=0)
    m.set_processed_through(20)
    m.set_processed_through(10)
    assert m.bounded().end_time() == 20
    assert not m.bounded().records
    assert ask(m, "what happened in the last 5 minutes?").status == "insufficient_evidence"


def test_existing_watermark_limits_serialization_and_explicit_cutoff():
    m = memory([SceneSegment(5, 5, caption="blue"), SceneSegment(15, 15, caption="red")], through_s=10)
    assert len(m.to_dict()["records"]) == 1
    assert len(m.bounded(90).records) == 1
    assert m.bounded(90).end_time() == 10


def test_ids_are_stable_and_scoped_to_replay_run():
    a = memory([SceneSegment(5, 5, caption="blue")], run_id="one")
    b = memory([SceneSegment(5, 5, caption="blue")], run_id="one")
    c = memory([SceneSegment(5, 5, caption="blue")], run_id="two")
    assert a.records[0].evidence_id == b.records[0].evidence_id
    assert a.records[0].evidence_id != c.records[0].evidence_id
    assert a.bounded(5).records[0].evidence_id == a.records[0].evidence_id


def test_latest_plan_is_past_quote_and_never_a_prediction():
    m = memory([TranscriptSegment(1, 2, text="Next we add blue paper."),
                TranscriptSegment(10, 11, text="Next we switch to red.")])
    early = ask(m, "what is the plan?", now=5)
    late = ask(m, "what is the plan?", now=15)
    assert "blue" in early.summary and "red" not in early.summary
    assert "red" in late.summary and late.source_label == "STATED"
    assert ask(memory([SceneSegment(1, 1, caption="Walking toward a shop")]),
               "where are they going next?").status == "insufficient_evidence"


def test_game_kills_do_not_cross_round_boundary():
    m = memory([GameEvent(5, 5, type="alive_count", payload={"allies_alive": 1, "enemies_alive": 5}),
                GameEvent(8, 8, type="kill", text="first round kill", payload={"actor_side": "allies"}),
                GameEvent(10, 10, type="round_end", payload={"result": "win"}),
                GameEvent(12, 12, type="round_start"),
                GameEvent(15, 15, type="kill", text="later round kill", payload={"actor_side": "allies"})])
    answer = ask(m, "was there a 1v5 win?")
    assert answer.status == "answered"
    assert all(c.t <= 10 for c in answer.citations)
    assert answer.clips[0].t_end == 10


def test_later_supported_clutch_win_is_not_hidden_by_earlier_loss():
    m = memory([GameEvent(5, 5, type="alive_count", payload={"allies_alive": 1, "enemies_alive": 5}),
                GameEvent(10, 10, type="round_end", payload={"result": "loss"}),
                GameEvent(12, 12, type="round_start"),
                GameEvent(15, 15, type="alive_count", payload={"allies_alive": 1, "enemies_alive": 5}),
                GameEvent(20, 20, type="round_end", payload={"result": "win"})])
    assert ask(m, "was there a 1v5 win?", now=11).status == "insufficient_evidence"
    answer = ask(m, "was there a 1v5 win?", now=21)
    assert answer.status == "answered" and min(c.t for c in answer.citations) == 15


def test_fts_queries_are_safe_and_do_not_cross_runs_or_cutoff():
    m = memory([SceneSegment(1, 1, caption="Blue paper"), SceneSegment(10, 10, caption="Red paper")])
    idx = EvidenceIndex()
    try:
        idx.index(m.records, "a")
        assert idx.search('paper" OR 1=1 --', "a", through_s=5) == [m.records[0].evidence_id]
        assert not idx.search("paper", "b", 100)
        assert idx.search("blue", "a", 100)
    finally:
        idx.close()
    assert len(retrieve(m.bounded(5), "paper")) == 1


class _Model:
    def __init__(self, invalid=False):
        self.invalid = invalid
        self.evidence = []

    def answer_from_evidence(self, question, evidence):
        self.evidence = evidence
        return {"claims": [{"text": "The paper is blue.", "evidence_ids": [
            "invented" if self.invalid else evidence[0]["evidence_id"]]}], "insufficient": False}


def test_model_citations_resolve_real_records_and_keep_provenance():
    m = memory([SceneSegment(5, 5, caption="Blue paper", payload={"frame_url": "/api/frames/blue"}),
                SceneSegment(20, 20, caption="Future red paper")], run_id="run")
    model = _Model()
    answer = answer_with_model(m, "what color paper?", model, now=10, run_id="run")
    assert answer.status == "answered" and len(model.evidence) == 1
    citation = answer.citations[0]
    assert citation.source_type == "observed" and citation.t == 5
    assert citation.frame_url == "/api/frames/blue"
    assert citation.excerpt == "Blue paper" and answer.run_id == "run"
    assert answer_with_model(m, "paper?", _Model(True), now=10).status == "error"
    assert answer_with_model(m, "paper?", model, run_id="expired").status == "insufficient_evidence"


def test_why_without_spoken_reason_never_calls_model():
    class MustNotCall:
        def answer_from_evidence(self, *args):
            raise AssertionError("no reason exists")
    m = memory([SceneSegment(1, 1, caption="The paper changed from red to blue")])
    answer = answer_with_model(m, "why did the paper change?", MustNotCall())
    assert answer.status == "insufficient_evidence" and not answer.citations


def test_missing_evidence_cannot_establish_negative_or_total():
    assert ask(memory(), "was there a 1v5 win?").status == "insufficient_evidence"
    assert ask(memory(), "how many subs?").status == "insufficient_evidence"
    assert answer_with_model(memory(), "how many objects?", _Model()).status == "insufficient_evidence"


def test_invalid_source_windows_fail_closed():
    m = memory([TranscriptSegment(1, 2, text="next secret", payload={"window_end": "bad"}),
                TranscriptSegment(1, 2, text="next secret", payload={"window_end": float("nan")})])
    assert not m.bounded(100).records


def test_valid_citation_id_cannot_publish_model_invented_causal_prose():
    class InventedMotive:
        def answer_from_evidence(self, question, evidence):
            return {"claims": [{"text": "They fled because they intended to avoid suspicion and evade justice.",
                                 "evidence_ids": [evidence[0]["evidence_id"]]}], "insufficient": False}
    source = "Cops are outside, we need to go out the back alley, the getaway car is waiting."
    m = memory([TranscriptSegment(5, 8, text=source)])
    answer = answer_with_model(m, "what did they say about cops?", InventedMotive())
    assert answer.status == "answered" and source in answer.summary
    assert "because" not in answer.summary and "avoid suspicion" not in answer.summary
    assert "Spoken transcript" in answer.summary and answer.source_label == "EXTRACTIVE"


def test_get_away_police_paraphrase_retrieves_escape_and_chase_context():
    m = memory([TranscriptSegment(5, 8, text="Cops are outside; the getaway car is waiting."),
                TranscriptSegment(10, 13, text="We lost them near the canals; stash the car.")])
    hits = retrieve(m, "How did they get away from the police?")
    assert {r.t_start for r in hits} == {5, 10}


def test_studio_suggested_questions_use_available_evidence_without_keyword_overlap():
    m = memory([SceneSegment(5, 5, caption="Blue paper on a workbench"),
                TranscriptSegment(6, 8, text="I moved the lamp.")])
    for question, expected in [("What has happened so far?", "RECAP_WINDOW"),
                               ("What are they doing right now?", "CURRENT_ACTIVITY")]:
        model = _Model()
        answer = answer_with_model(m, question, model, now=10)
        assert model.evidence and answer.status == "answered" and answer.query_type == expected
    assert ask(m, "What has happened so far?").query_type == "RECAP_WINDOW"


def test_general_first_person_plan_requires_completed_source_and_past_activity_is_not_a_plan():
    source = "Next I will add captions to this design."
    m = memory([TranscriptSegment(5, 8, text=source, payload={"window_end": 10})])
    assert ask(m, "What did they say they would do next?", now=9).status == "insufficient_evidence"
    answer = ask(m, "What did they say they would do next?", now=10)
    assert answer.status == "answered" and source in answer.summary
    past = memory([TranscriptSegment(2, 3, text="We hit the bank yesterday.")])
    assert ask(past, "What is the next plan?").status == "insufficient_evidence"


def test_unrelated_spoken_because_cannot_answer_another_why_question():
    class MustNotCall:
        def answer_from_evidence(self, *args):
            raise AssertionError("The available reason concerns a different subject.")
    m = memory([TranscriptSegment(2, 4, text="I enabled the preview because it was blank."),
                SceneSegment(5, 5, caption="Green circle on a blue background.")])
    assert answer_with_model(m, "Why did they choose a green circle?", MustNotCall()).status == "insufficient_evidence"


def test_reason_paraphrase_uses_interpreted_subject_without_inventing_causation():
    class ReasonModel(_Model):
        def interpret_question(self, question):
            return {"intent": "reason", "modality": "spoken",
                    "terms": ["restarted server"], "window_s": 0}
    source = "I restarted the server because it froze."
    m = memory([TranscriptSegment(2, 4, text=source)])
    model = ReasonModel()
    answer = answer_with_model(m, "What prompted the reboot?", model)
    assert answer.status == "answered" and source in answer.summary
    assert len(model.evidence) == 1
    assert "The paper is blue" not in answer.summary


def test_what_did_he_say_requires_speech_not_a_bird_picture():
    m = memory([SceneSegment(1,1,caption="Migratory birds in the water"),
                TranscriptSegment(5,8,text="Those migratory birds come from South America.",payload={"window_end":10})])
    model = _Model()
    answer = answer_with_model(m,"What did he say about migratory birds?",model,now=10)
    assert answer.status == 'answered' and all(c.source_type == 'spoken' for c in answer.citations)
    assert 'South America' in answer.summary
    early = answer_with_model(m,"What did he say about migratory birds?",_Model(),now=9)
    assert not early.citations and early.status == 'insufficient_evidence'


def test_model_search_rewrite_is_only_a_hint_and_stays_in_bounded_speech():
    class Paraphrase(_Model):
        def expand_query(self, question): return ['migratory birds']
    m = memory([TranscriptSegment(5,8,text='Migratory birds come from South America.'),
                TranscriptSegment(90,99,text='Winter migration secret future')])
    result=answer_with_model(m,'What did he mention about seasonal visitors?',Paraphrase(),now=10)
    assert result.status == 'answered' and all(c.t <= 10 for c in result.citations)


def test_natural_current_questions_receive_recent_context_without_keyword_overlap():
    m = memory([SceneSegment(5,5,caption='A boat travels between tall grasses.'),
                TranscriptSegment(6,8,text='These birds migrate in winter.',payload={'window_end':10}),
                SceneSegment(90,90,caption='Future waterfall')],through_s=10)
    for q in ["whats happening", "what's happening?", "What’s going on?", "what is happening",
              "What am I watching?", "Describe the scene"]:
        model = _Model()
        result = answer_with_model(m,q,model)
        assert result.status == 'answered' and result.query_type == 'CURRENT_ACTIVITY',q
        assert {e['source_type'] for e in model.evidence} == {'spoken','observed'}
        assert all(e['t_end'] <= 10 for e in model.evidence)
        assert ask(m,q,now=10).status == 'answered'


def test_current_context_does_not_treat_old_speech_as_live():
    m = memory([TranscriptSegment(1,3,text='We are cooking now.'),
                SceneSegment(95,95,caption='A boat on a river.')],through_s=100)
    model = _Model()
    answer_with_model(m,"what's happening?",model)
    assert len(model.evidence) == 1 and model.evidence[0]['t_start'] == 95
    assert answer_with_model(memory([TranscriptSegment(1,3,text='Old speech')]),
                             'What is he saying?',_Model(),now=100).status == 'insufficient_evidence'


def test_saying_question_and_sign_question_use_the_correct_modality():
    m = memory([SceneSegment(5,5,caption='The sign says Exit.'),
                TranscriptSegment(6,8,text='We will follow the river.',payload={'window_end':10})])
    model = _Model()
    spoken = answer_with_model(m,'What is he saying?',model,now=10)
    assert spoken.status == 'answered' and all(c.source_type == 'spoken' for c in spoken.citations)
    assert answer_with_model(m,'What is he saying?',_Model(),now=9).status == 'insufficient_evidence'
    visual = answer_with_model(m,'What does the sign say?',_Model(),now=10)
    assert visual.status == 'answered' and all(c.source_type == 'observed' for c in visual.citations)


def test_interpreted_paraphrase_and_no_overlap_fallback_stay_bounded():
    class Interpreted(_Model):
        def interpret_question(self,q):
            return {'intent':'search','modality':'spoken','terms':['migratory birds'],'window_s':0}
    m = memory([TranscriptSegment(5,8,text='Migratory birds arrive every winter.'),
                TranscriptSegment(90,95,text='Future migratory birds are blue.')])
    result = answer_with_model(m,'Tell me about the seasonal visitors',Interpreted(),now=10)
    assert result.status == 'answered' and all(c.t <= 10 for c in result.citations)
    class Abstains(_Model):
        def answer_from_evidence(self,q,evidence):
            self.evidence = evidence
            return {'claims':[],'insufficient':True}
    model = Abstains()
    result = answer_with_model(m,'Explain quantum teleportation',model,now=10)
    assert model.evidence and result.status == 'insufficient_evidence' and not result.citations
    assert any('fallback' in step for step in result.tool_trace)


def test_stated_quantity_is_retrieved_without_counting_sparse_frames():
    m = memory([TranscriptSegment(5,8,text='I added two eggs.'),SceneSegment(9,9,caption='A mixing bowl')])
    result = answer_with_model(m,'How many eggs did she say she added?',_Model(),now=10)
    assert result.status == 'answered' and 'two eggs' in result.summary
    assert answer_with_model(memory([SceneSegment(9,9,caption='A mixing bowl')]),
                             'How many times was the bowl stirred?',_Model(),now=10).status == 'insufficient_evidence'


def test_next_to_is_spatial_and_past_next_is_not_a_future_plan():
    m = memory([SceneSegment(5,5,caption='Tall grasses next to the boat.'),
                SceneSegment(10,10,caption='The boat passes a wooden bridge.')])
    result = answer_with_model(m,"What is next to the boat?",_Model(),now=15)
    assert result.status == 'answered' and result.query_type != 'NEXT'
    assert ask(m, "What is next to the boat?", now=15).query_type != 'STATED_PLAN'
    result = answer_with_model(m,'What happened next?',_Model(),now=15)
    assert result.status == 'answered' and result.query_type == 'SEQUENCE'


def test_current_answer_keeps_the_latest_selected_evidence_first():
    class SelectAll(_Model):
        def answer_from_evidence(self, question, evidence):
            return {"claims": [{"text": "Observed context", "evidence_ids": [e["evidence_id"] for e in evidence]}],
                    "insufficient": False}
    m = memory([SceneSegment(5,5,caption='Earlier red car'),
                SceneSegment(10,10,caption='Latest blue boat'),
                TranscriptSegment(1,2,text='Earlier words')])
    answer = answer_with_model(m, "What's happening?", SelectAll(), now=10)
    assert answer.status == 'answered' and answer.citations[0].t == 10
    assert answer.summary.startswith('Generated visual description [0:10]')


def test_weak_visual_keyword_match_does_not_hide_relevant_spoken_context():
    class MeetingModel(_Model):
        def interpret_question(self, question):
            return {'intent':'search', 'modality':'any', 'terms':['meeting'], 'window_s':0}
        def answer_from_evidence(self, question, evidence):
            self.evidence = evidence
            spoken = [e for e in evidence if e['source_type'] == 'spoken']
            assert len(spoken) == 1 and 'meet with Jason' in spoken[0]['excerpt']
            return {'claims':[{'text':'The speaker mentioned Jason.', 'evidence_ids':[spoken[0]['evidence_id']]}],
                    'insufficient':False}
    m = memory([SceneSegment(1,1,caption='Speaker inset and phone contact list on screen.'),
                TranscriptSegment(2,8,text='I am about to meet with Jason.',payload={'window_end':10}),
                TranscriptSegment(11,18,text='Future meeting secret',payload={'window_end':20})])
    result = answer_with_model(m, 'Who is the speaker meeting?', MeetingModel(), now=10)
    assert result.status == 'answered' and 'Jason' in result.summary
    assert all(c.source_type == 'spoken' and c.t_end <= 10 for c in result.citations)


def test_before_question_receives_preceding_context_without_keyword_overlap():
    m = memory([SceneSegment(5,5,caption='They fold the sheet of paper'),
                SceneSegment(10,10,caption='They paint the background blue'),
                SceneSegment(15,15,caption='They add a red circle')])
    model = _Model()
    answer = answer_with_model(m, 'What happened before they painted the background blue?', model, now=20)
    assert answer.query_type == 'SEQUENCE'
    assert {r['t_start'] for r in model.evidence} == {5,10}


def test_recap_seconds_and_interpreter_failure_use_correct_context():
    class BrokenInterpreter(_Model):
        def interpret_question(self,q): raise RuntimeError('unavailable')
    m = memory([SceneSegment(10,10,caption='Earlier blue paper'),SceneSegment(95,95,caption='Recent green circle')])
    model = BrokenInterpreter()
    result = answer_with_model(m,'Summarize the last 10 seconds',model,now=100)
    assert result.status == 'answered' and len(model.evidence) == 1 and model.evidence[0]['t_start'] == 95
    result = answer_with_model(m,'Tell me about the green circle',BrokenInterpreter(),now=100)
    assert result.status == 'answered' and result.citations[0].t == 95
