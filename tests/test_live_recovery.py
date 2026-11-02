from unittest.mock import patch
from test_runtime import session,Model,Speech,update,advance,wait_for,JPEG
from tubelive import runtime
from tubelive.ask import answer_with_model
from test_natural import Conversational
from tubelive.store import StreamMemory

def test_live_backpressure_bounds_video_only_and_marks_skipped_analysis():
    model=Model(block_vision=0)
    with session(audio=False,duration=120,model=model) as (app,_,_),patch.object(runtime,'frame_at',return_value=JPEG):
        app.pipeline['catch_up']='live'
        update(app,0,frame=True); assert model.entered.wait(1)
        advance(app,120,ended=True)
        assert len(app._ingest)<=3 and not app.gap
        assert app.memory.coverage_gaps and all(g['lane']=='vision' for g in app.memory.coverage_gaps)
        model.release.set(); wait_for(app,lambda: app.vision_through_s==120)
        assert [t for k,t in model.calls if k=='vision']==[0,110,115,120]
        assert all(g['end_s']<=120 for g in app.memory_dict()['coverage_gaps'])

def test_disconnect_cancels_queued_descriptors_and_reconnect_refills_without_duplicates():
    model=Model(block_vision=0)
    with session(audio=False,duration=20,model=model) as (app,_,_),patch.object(runtime,'frame_at',return_value=JPEG):
        update(app,0,frame=True); assert model.entered.wait(1)
        advance(app,10)
        with app.condition:
            app._last_clock_wall-=20
            app._suspend_if_stale()
            assert not app._ingest and app._source_suspended
        model.release.set();wait_for(app,lambda: app._active_job is None)
        assert len(model.calls)==1
        update(app,10);wait_for(app,lambda: app.vision_through_s==10)
        assert [t for k,t in model.calls if k=='vision']==[0,5,10]

def test_resume_keeps_run_and_old_memory_but_cancels_old_inflight_commit():
    model=Model(block_vision=5)
    with session(audio=False,duration=40,model=model) as (app,_,_),patch.object(runtime,'frame_at',return_value=JPEG):
        update(app,0,frame=True);wait_for(app,lambda: app.metrics['frames_processed']==1)
        saved=app.memory.records[0].evidence_id; run=app.run_id
        advance(app,10);assert model.entered.wait(1)
        app.control('resume_from_playhead')
        model.release.set();wait_for(app,lambda: app._active_job is None and not app._ingest)
        assert app.run_id==run and any(r.evidence_id==saved for r in app.memory.records)
        assert not any(r.t_start==5 for r in app.memory.records)
        assert [r.t_start for r in app.memory.records]==[0,10]
        assert app.test_results()['results']==[]
        cancelled=[r for r in app.diagnostics.history() if r['event']=='job_finished' and r.get('outcome')=='cancelled']
        assert cancelled and app._test_stats['failed_jobs']==0

def test_live_transient_failure_is_a_hole_and_later_work_continues():
    class Failure(Model):
        def observe_frame(self,raw,t):
            if t==5: raise RuntimeError('transient')
            return super().observe_frame(raw,t)
    with session(audio=False,duration=15,model=Failure()) as (app,_,_),patch.object(runtime,'frame_at',return_value=JPEG):
        app.pipeline['catch_up']='live'
        advance(app,10,ended=True)
        wait_for(app,lambda: app.vision_through_s==10)
        assert not app.gap and app._test_stats['failed_jobs']==1
        assert any(g['reason']=='processing_failed' for g in app.memory.coverage_gaps)
        assert all(r.t_start!=5 for r in app.memory.records)

def test_gap_metadata_survives_export_and_cutoff_and_does_not_create_evidence(tmp_path):
    with session(audio=False,duration=120) as (app,_,_),patch.object(runtime,'frame_at',return_value=JPEG):
        update(app,0,frame=True);wait_for(app,lambda: app.metrics['frames_processed']==1)
        update(app,100);wait_for(app,lambda: app._active_job is None and not app._ingest)
        loaded=StreamMemory.load(app.export(tmp_path/'bundle'))
        early=loaded.bounded(50)
        assert early.coverage_gaps and all(g['end_s']<=50 for g in early.coverage_gaps)
        assert all(r.t_end<=50 for r in early.records)
        answer=answer_with_model(early,'whats happening',Conversational(),now=50)
        assert answer.coverage_gaps
        assert all(c.t_end<=50 for c in answer.citations)

def test_finished_with_missing_analysis_is_not_labelled_complete():
    with session(audio=False,duration=10) as (app,_,_):
        with app.lock:
            app.pipeline['enabled']=True; app._test_started=runtime.time.monotonic()
            app._source_ended=True
            app.playback_s=app.audio_through_s=app.vision_through_s=10
            app.memory.add_coverage_gap(5,10,'vision','processing_failed')
            result=app.test_results()['results'][0]
            assert result['finished'] and not result['complete'] and result['coverage_gap_count']==1

def test_failed_first_frame_is_recorded_even_at_zero_time_and_repeated_failures_pause():
    class Failure(Model):
        def observe_frame(self,raw,t):raise RuntimeError('offline')
    with session(audio=False,duration=15,model=Failure()) as (app,_,_),patch.object(runtime,'frame_at',return_value=JPEG):
        app.pipeline['catch_up']='live'
        update(app,0,frame=True)
        wait_for(app,lambda: app._test_stats['failed_jobs']==1)
        assert app.memory.bounded(0).coverage_gaps==[{'start_s':0.0,'end_s':0.0,'lane':'vision','reason':'processing_failed'}]
        advance(app,10)
        wait_for(app,lambda: app.gap and not app._active_job)
        assert app._test_stats['failed_jobs']==3 and app.status()['retry_available']
        assert not app._ingest and not app.memory.records
