import './testmode.css';

interface Config {
  enabled: boolean; model: string; speech_model: string; speech_device: string;
  cpu_threads: number; frame_interval_s: number; frame_width: number;
  answer_style: string; allow_predictions: boolean;
  catch_up:string; vision_detail:string; model_cpu_threads:number;
}
interface Setup {
  active: Config; speech_models: {name:string;ready:boolean}[];
  cuda_speech_available: boolean; max_cpu_threads: number;
  models: {models:{name:string;compatible:boolean;reason:string}[]};
}
interface Result {
  run_id:string; config:Config; complete:boolean; processed_through_s:number;
  finished?:boolean; coverage_gap_count?:number;
  peak_lag_s:number; last_answer_s:number|null; frames_processed:number;
  audio_windows_processed:number; audio_seconds:number; vision_seconds:number;
}

export function installTestMode(request: <T>(path:string,data?:unknown)=>Promise<T>) {
  const panel = document.createElement('details');
  panel.id = 'testMode'; panel.className = 'diagnostics-panel test-mode';
  panel.innerHTML = `<summary><span>Test mode <small>Try a lighter setup &amp; compare speed</small></span></summary>
  <div class="diagnostics-body"><p>Use the same recording to compare settings. Each test starts at the beginning with empty memory. Speech and video run separately, then feed the same conversation.</p>
  <div class="diagnostics-actions"><button class="button" id="testLight">Lighter · 4B + base</button><button class="button" id="testBalanced">Balanced · 9B + small</button><button class="button" id="testRefresh">Refresh settings</button></div>
  <form id="testForm"><fieldset disabled><div class="test-fields">
  <label>Video &amp; answer model<select name="model"></select></label>
  <label>Speech model<select name="speech_model"></select></label>
  <label>Speech device<select name="speech_device"><option value="cpu">CPU · leaves GPU for video</option><option value="cuda">GPU · needs CUDA speech libraries</option></select></label>
  <label>CPU threads<input name="cpu_threads" type="number" min="1" value="8"></label>
  <label>Video model CPU threads<input name="model_cpu_threads" type="number" min="1" value="4"></label>
  <label>When processing falls behind<select name="catch_up"><option value="complete">Analyze all sampled frames</option><option value="live">Stay near live · skip stale frames</option></select></label>
  <label>Frame description<select name="vision_detail"><option value="brief">Brief · one sentence</option><option value="detailed">Detailed · text and objects</option></select></label>
  <label>Analyze a frame every<select name="frame_interval_s"><option value="2">2 seconds · more detail</option><option value="5">5 seconds · balanced</option><option value="10">10 seconds · less work</option></select></label>
  <label>Frame detail<select name="frame_width"><option value="480">480 pixels · lighter</option><option value="720">720 pixels · balanced</option><option value="768">768 pixels · live preset</option><option value="960">960 pixels · finer text</option></select></label>
  <label>Bot replies<select name="answer_style"><option value="natural">Natural conversation</option><option value="quotes">Direct source quotes</option></select></label>
  <label class="test-check"><input name="allow_predictions" type="checkbox" checked>Allow cautious predictions when asked</label>
  </div><p class="test-explain">Stay near live keeps at most three frames waiting and marks skipped analysis as coverage gaps. Speech keeps its own worker. Brief descriptions and larger sampling intervals reduce work but can miss details.</p>
  <p id="testDeviceNote"></p><p><strong>Starting a test clears the current replay memory.</strong> Pause playback and let processing finish first. Your video file is kept.</p>
  <button class="button primary" type="submit">Start new test from beginning</button></fieldset></form>
  <p id="testFeedback" role="status">Open to load available settings.</p>
  <div class="test-result-header"><h3>Recent test runs</h3><button class="button" id="testResultsRefresh">Refresh results</button><button class="button" id="testDownload">Download results</button></div>
  <div id="testResults" aria-live="polite"></div><p class="test-explain">Timings are measured locally. Results contain settings and timings, without your questions, transcript or video.</p></div>`;
  document.querySelector('#modelSettings')!.before(panel);
  const form = panel.querySelector<HTMLFormElement>('form')!;
  const fieldset = form.querySelector('fieldset')!;
  const feedback = panel.querySelector<HTMLElement>('#testFeedback')!;
  const field = (name:string) => form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement;
  let setup:Setup|null = null;
  let busy = false;
  function fill(config:Config) {
    for (const [key,value] of Object.entries(config)) {
      const input = field(key); if (!input) continue;
      if (input instanceof HTMLInputElement && input.type === 'checkbox') input.checked = !!value;
      else input.value = String(value);
    }
  }
  async function results() {
    const data = await request<{results:Result[]}>('/api/test/results');
    const container = panel.querySelector('#testResults')!; container.replaceChildren();
    if (!data.results.length) { container.textContent = 'No test runs yet. Start a test, play the recording and ask a question.'; return data; }
    for (const row of data.results.slice(-5).reverse()) {
      const item = document.createElement('p'); item.className = 'test-result';
      const label = document.createElement('strong');
      label.textContent = `${row.config.model} + Whisper ${row.config.speech_model} · ${row.coverage_gap_count ? `${row.finished ? 'Finished' : 'Partial run'} · ${row.coverage_gap_count} skipped intervals` : row.complete ? 'Completed' : 'Partial run'}`;
      const detail = document.createElement('span');
      const avg = (total:number,count:number) => count ? `${(total/count).toFixed(1)}s` : '—';
      detail.textContent = `${Math.floor(row.processed_through_s)}s processed · peak lag ${row.peak_lag_s.toFixed(1)}s · speech/window ${avg(row.audio_seconds,row.audio_windows_processed)} · video/frame ${avg(row.vision_seconds,row.frames_processed)} · last answer ${row.last_answer_s == null ? '—' : row.last_answer_s.toFixed(1)+'s'}`;
      item.append(label,detail); container.append(item);
    }
    return data;
  }
  async function load() {
    if (busy) return; busy = true; fieldset.disabled = true;
    feedback.textContent = 'Checking installed local models…';
    try {
      setup = await request<Setup>('/api/test/config');
      const options = (name:string, rows:{name:string;disabled:boolean;label:string}[]) => {
        const select = field(name) as HTMLSelectElement; select.replaceChildren();
        for (const row of rows) { const option = new Option(row.label,row.name); option.disabled = row.disabled; select.add(option); }
      };
      options('model', setup.models.models.map(m=>({name:m.name,disabled:!m.compatible,label:`${m.name} · ${m.reason}`})));
      options('speech_model',setup.speech_models.map(m=>({name:m.name,disabled:!m.ready,label:`Whisper ${m.name}${m.ready ? ' · ready' : ' · not downloaded'}`})));
      (field('speech_device') as HTMLSelectElement).options[1].disabled = !setup.cuda_speech_available;
      (field('cpu_threads') as HTMLInputElement).max = String(setup.max_cpu_threads);
      (field('model_cpu_threads') as HTMLInputElement).max = String(setup.max_cpu_threads);
      panel.querySelector('#testDeviceNote')!.textContent = setup.cuda_speech_available ? 'GPU speech libraries are available. CPU speech avoids competing with video for GPU memory.' : 'GPU speech libraries are unavailable here. CPU speech works alongside GPU video analysis.';
      fill(setup.active); fieldset.disabled = false;
      feedback.textContent = `Current setup: ${setup.active.model} + Whisper ${setup.active.speech_model}. Choose a preset or adjust the settings.`;
      await results();
    } catch (error) { feedback.textContent = error instanceof Error ? error.message : 'Could not load settings.'; }
    finally { busy = false; }
  }
  function preset(light:boolean) {
    if (!setup || busy) return;
    const model = light ? 'qwen3.5:4b' : 'qwen3.5:9b'; const speech = light ? 'base' : 'small';
    if (!setup.models.models.some(m=>m.name===model && m.compatible) || !setup.speech_models.some(m=>m.name===speech && m.ready)) {
      feedback.textContent = 'This preset needs models that are not installed yet. Choose ready models below.'; return;
    }
    fill({...setup.active,model,speech_model:speech,speech_device:'cpu',cpu_threads:Math.min(light?4:8,setup.max_cpu_threads),model_cpu_threads:Math.min(4,setup.max_cpu_threads),frame_interval_s:light?10:5,frame_width:light?768:960,catch_up:light?'live':'complete',vision_detail:light?'brief':'detailed',answer_style:'natural',allow_predictions:true});
    feedback.textContent = 'Preset selected. Start a new test to apply it.';
  }
  form.addEventListener('submit',async event=>{
    event.preventDefault(); if (busy || !setup) return;
    const config = {...setup.active,enabled:true};
    for (const key of ['model','speech_model','speech_device','answer_style','catch_up','vision_detail'] as const) config[key] = field(key).value;
    for (const key of ['cpu_threads','frame_interval_s','frame_width','model_cpu_threads'] as const) config[key] = Number(field(key).value);
    config.allow_predictions = (field('allow_predictions') as HTMLInputElement).checked;
    busy = true; fieldset.disabled = true; feedback.textContent = 'Preparing the selected models…';
    try {
      await request('/api/test/start',config);
      setup.active = config; feedback.textContent = 'Test ready with empty memory. Press Start local replay if playback has not begun, then try /recall what’s happening in the stream demo.';
      await results();
    } catch (error) { feedback.textContent = error instanceof Error ? error.message : 'Could not start the test.'; }
    finally { busy = false; fieldset.disabled = false; }
  });
  panel.querySelector('#testLight')!.addEventListener('click',()=>preset(true));
  panel.querySelector('#testBalanced')!.addEventListener('click',()=>preset(false));
  panel.querySelector('#testRefresh')!.addEventListener('click',()=>void load());
  panel.querySelector('#testResultsRefresh')!.addEventListener('click',()=>void results().catch(e=>{feedback.textContent=String(e);}));
  panel.querySelector('#testDownload')!.addEventListener('click',async()=>{
    try { const data = await request('/api/test/results'); const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})); const link=document.createElement('a'); link.href=url; link.download='recall-test-results.json'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); }
    catch(error) {feedback.textContent=String(error);}
  });
  panel.addEventListener('toggle',()=>{if(panel.open) void load();});
}
