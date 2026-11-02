from unittest.mock import patch
import pytest
from test_runtime import session, Model, Speech, update, JPEG
from tubelive import runtime
from tubelive.testmode import validate_config, save_result, read_results

class Selectable(Model):
    model = 'qwen3.5:9b'
    def select_model(self,name):
        if name not in {'qwen3.5:9b','qwen3.5:4b'}:
            raise ValueError('Unavailable model')
        self.model = name

@pytest.mark.parametrize('settings',[{'remote_url':'https://example.com'}, {'frame_interval_s':0},
    {'cpu_threads':True},{'frame_width':10000},{'speech_device':'auto'},{'allow_predictions':'true'}])
def test_config_rejects_unknown_or_unbounded_settings(settings):
    with pytest.raises(ValueError): validate_config(settings)

def test_start_test_preflights_before_reset_and_applies_all_settings():
    with session(model=Selectable()) as (app,_,_), patch.object(runtime,'speech_options',return_value=[{'name':'base','ready':True}]), patch.object(runtime,'LocalTranscriber') as speech:
        old = app.run_id
        with pytest.raises(ValueError): app.start_test({'speech_model':'base','model':'missing'})
        assert app.run_id == old and not app.pipeline['enabled']
        app.start_test({'model':'qwen3.5:4b','speech_model':'base','frame_interval_s':10,'frame_width':720,'cpu_threads':4,'allow_predictions':False})
        assert app.run_id != old and app.processed_through_s == 0 and not app.memory.records
        assert app.pipeline['model']=='qwen3.5:4b' and app.pipeline['frame_width']==720
        assert app.source_command=='restart' and app.state=='waiting_for_source'
        speech.assert_called_with(app.cache_dir,model='base',device='cpu',cpu_threads=4)
        speech.return_value.prepare.assert_called()

def test_test_start_refuses_active_or_missing_provider_without_clearing():
    with session(model=Selectable()) as (app,_,_):
        old=app.run_id
        app.state='playing'
        with pytest.raises(runtime.BusyError): app.start_test({})
        app.state='paused'
        with patch.object(runtime,'speech_options',return_value=[]), pytest.raises(ValueError): app.start_test({})
        assert app.run_id==old

def test_sampling_settings_bound_deferred_frames_to_source_clock():
    with session(audio=False,duration=30) as (app,_,_):
        with app.condition:
            app.pipeline['frame_interval_s']=10
            app._source_started=True
            app.playback_s=22
            app._schedule_ingest()
            assert [job[2][0] for job in app._ingest]==[0,10,20]
            app._ingest.clear()

def test_results_exclude_content_and_preserve_previous_test(tmp_path):
    with session(model=Selectable()) as (app,_,_):
        with app.lock:
            app.pipeline['enabled']=True
            app._test_started=runtime.time.monotonic()
            app.playback_s=20
            app._test_stats['peak_lag_s']=10
            old=app.run_id
            app._reset()
        row=app.test_results()['results'][0]
        assert row['run_id']==old and row['peak_lag_s']==10
        assert not {'title','question','transcript','source','csrf_token'}.intersection(row)
    for i in range(25):save_result(tmp_path,{'run_id':str(i)})
    assert len(read_results(tmp_path))==20
