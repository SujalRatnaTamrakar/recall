// Browser contract checks use explicitly synthetic QA data, never demo AI output.
// Run a built Recall server, then: UI_BASE_URL=http://127.0.0.1:8765 node tests/ui-smoke.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8765';
  const mediaPath = process.env.QA_VIDEO || path.join(root, '.qa', 'source.mp4');
  const framePath = process.env.QA_FRAME || path.join(root, '.qa', 'frame.jpg');
  fs.mkdirSync(path.join(root, '.qa'), { recursive: true });
  if (!process.env.QA_VIDEO && !fs.existsSync(mediaPath)) execFileSync('ffmpeg', ['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=12', '-t', '8', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-y', mediaPath, '-loglevel', 'error']);
  if (!process.env.QA_FRAME && !fs.existsSync(framePath)) execFileSync('ffmpeg', ['-i', mediaPath, '-frames:v', '1', '-y', framePath, '-loglevel', 'error']);
  assert(fs.existsSync(mediaPath), 'Provide a short synthetic MP4 with QA_VIDEO.');
  assert(fs.existsSync(framePath), 'Provide its JPEG frame with QA_FRAME.');
  const browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'chrome' });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    const jsErrors = [];
    const heartbeats = [];
    const telemetry = [];
    let run = 'qa-run';
    let mode = 'replay';
    let revision = 0;
    let sourceRevision = 0;
    let sourceCommand = null;
    let currentAnswer = null;
    let selection = 0;
    let sourceTime = 8;
    let uploaded = false;
    let statusOverrides = {};
    let retryCount = 0;
    let selectedModel = 'vision:small';
    let testSettings = null;
    let creatorProfile = {channel:'',streamer:'',character:'',activity:'',cast:[],speaker_bindings:[],semantic_search:true,inspect_frames:true};
    const uploadedNames = [];
    const savedMoments = new Map();
    const status = () => ({ run_id: run, mode, title: 'Synthetic QA recording', duration_s: 8, has_video: true,
      playback_s: sourceTime, processed_through_s: uploaded ? 0 : 8, audio_through_s: uploaded ? 0 : 8,
      vision_through_s: uploaded ? 0 : 8, state: 'ready', gap: null, error: null, provider: 'QA test double', csrf_token: 'qa-token', ...statusOverrides });
    const json = (route, body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== new URL(base).origin) return route.abort();
      if (url.pathname === '/video') {
        const bytes = fs.readFileSync(mediaPath);
        const range = request.headers().range?.match(/^bytes=(\d+)-(\d*)$/);
        if (!range) return route.fulfill({ contentType:'video/mp4', body:bytes, headers:{'accept-ranges':'bytes'} });
        const start=Number(range[1]), end=range[2] ? Math.min(Number(range[2]),bytes.length-1) : bytes.length-1;
        return route.fulfill({ status:206, contentType:'video/mp4', body:bytes.subarray(start,end+1), headers:{'accept-ranges':'bytes','content-range':`bytes ${start}-${end}/${bytes.length}`} });
      }
      if (url.pathname.startsWith('/frames/qa')) return route.fulfill({ contentType: 'image/jpeg', body: fs.readFileSync(framePath) });
      if (!url.pathname.startsWith('/api/')) return route.continue();
      if (request.method() === 'POST') assert.equal(request.headers()['x-tube-token'], 'qa-token', 'All mutations carry the local session token.');
      if (url.pathname === '/api/status') return json(route, status());
      if (url.pathname === '/api/test/config') return json(route,{active:{enabled:false,model:'qwen3.5:9b',speech_model:'small',speech_device:'cpu',cpu_threads:8,frame_interval_s:5,frame_width:960,answer_style:'natural',allow_predictions:true},
        speech_models:[{name:'base',ready:true},{name:'small',ready:true},{name:'tiny',ready:false}],cuda_speech_available:false,max_cpu_threads:24,
        models:{models:[{name:'qwen3.5:4b',compatible:true,reason:'Video and answers'},{name:'qwen3.5:9b',compatible:true,reason:'Video and answers'}]}});
      if (url.pathname === '/api/test/results') return json(route,{results:[]});
      if (url.pathname === '/api/test/start') {testSettings=request.postDataJSON(); return json(route,status());}
      if (url.pathname === '/api/models') return json(route,{selected:selectedModel,models:[
        {name:'vision:small',size_bytes:6000000000,compatible:true,reason:'Video and answers'},
        {name:'vision:large',size_bytes:17000000000,compatible:true,reason:'Video and answers'},
        {name:'text:only',size_bytes:4000000000,compatible:false,reason:'No verified vision support'}]});
      if (url.pathname === '/api/model') { selectedModel = request.postDataJSON().model; return json(route,{selected:selectedModel}); }
      if (url.pathname === '/api/diagnostics') return json(route, {
        status: {...status(),state:'paused',source_connected:false,source_age_s:9,queue_depth:0,questions_waiting:0,active_job:null,active_jobs:[{kind:'audio',age_s:1.5},{kind:'vision',age_s:2}],metrics:{last_vision_s:2,last_audio_s:1.7,last_answer_s:2.3}},
        hardware:{sample_age_s:3,memory:{available_mb:11264},gpu:{devices:[{utilization_pct:2,memory_used_mb:3072,memory_total_mb:16384}]}},
        events:[{timestamp:'2026-10-29T12:00:00Z',event:'browser_pause',position_s:83.7}],
      });
      if (url.pathname === '/api/diagnostics/download') return route.fulfill({contentType:'application/json',headers:{'content-disposition':'attachment; filename="recall-debug-report.json"'},body:'{"events":[]}'});
      if (url.pathname === '/api/diagnostics/event') { telemetry.push(request.postDataJSON()); return json(route,{ok:true}); }
      if (url.pathname === '/api/shared') return json(route, { run_id: run, rotation: 0, selection, answer: currentAnswer, controls_enabled: true, revision, source_revision: sourceRevision, source_command: sourceCommand });
      if (url.pathname === '/api/context') {
        if(request.method()==='POST') {
          assert.equal(request.postDataJSON().run_id,run);
          creatorProfile=request.postDataJSON().profile;
        }
        return json(route,{run_id:run,profile:creatorProfile,version:1,known_s:8,story_through_s:8,
          semantic_ready:true,event_count:1,events:[{summary:'A synthetic introduction.',category:'introduction',known_s:8}],
          transcripts:[{evidence_id:'audio-2',t_start:2,text:'My character is Ash.',speaker:'unknown'}]});
      }
      if (url.pathname === '/api/memory') return json(route, { run_id: run, through_s: uploaded ? 0 : 8,
        frames: uploaded ? [] : Array.from({ length: 8 }, (_, i) => ({ id: String(i), t: i, url: `/frames/qa${i}` })),
        records: uploaded ? [] : [0, 2, 4, 6].flatMap(t => [
          { evidence_id: `visual-${t}`, t_start: t, t_end: t + 1, modality: 'video', kind: 'scene', activity: 'QA pattern', text: 'Synthetic test pattern' },
          { evidence_id: `audio-${t}`, t_start: t, t_end: t + 1, modality: 'transcript', text: 'Synthetic transcript for browser testing.' },
        ]), server: { has_video: true, video_url: '/video' } });
      if (url.pathname === '/api/ask') {
        const question = request.postDataJSON().question;
        const times = question.includes('many matches') ? [4, 2, 6, 0] : question.includes('unanswerable') ? [] : [4];
        const summary = question.includes('long summary') ? 'Spoken transcript [0:04]: "<img src=x onerror=alert(1)> ' + 'A clear spoken detail from the recording. '.repeat(10)
          : question.includes('unanswerable') ? 'The recording does not establish why this happened.' : 'Synthetic QA answer with inspectable evidence.';
        currentAnswer = { summary, status: times.length ? 'answered' : 'insufficient_evidence', run_id: run, through_s: 8,
          context:{through_s:8,story_through_s:8,profile_version:1,selected_records:1,semantic_search:true,
            frames:[{evidence_id:'visual-4',time_s:4}],records:[{evidence_id:'audio-2',start_s:2,end_s:3,source_type:'spoken',role:'background',excerpt:'My character is Ash. <img src=x onerror=alert(1)>'}]},
          citations: times.map(t => ({ t, t_end: t+1, evidence_id: `visual-${t}`, source_type: 'observed', label: 'Synthetic visual evidence', excerpt: 'QA data only.', frame_url: `/frames/qa${t}` })), tool_trace: ['Synthetic browser contract test.'] };
        revision++;
        return json(route, currentAnswer);
      }
      if (url.pathname === '/api/moments' && request.method() === 'POST') {
        const payload = request.postDataJSON(); assert(currentAnswer.citations.some(c => c.evidence_id === payload.evidence_id));
        const t = Number(payload.evidence_id.split('-')[1]);
        const id = `moment-${savedMoments.size}`;
        const value = { id, run_id:run, start_s:t, end_s:t+1, evidence_s:t, through_s:8, title:'QA moment', excerpt:'QA data only.', source_type:'observed', video_url:'/video', url:`/?view=moment&id=${id}` };
        savedMoments.set(id,value); return json(route,value);
      }
      if (url.pathname.startsWith('/api/moments/')) return json(route,savedMoments.get(url.pathname.split('/').pop()));
      if (url.pathname === '/api/clip') {
        const payload = request.postDataJSON();
        assert.equal(payload.run_id, run);
        assert.equal(payload.start_s, 4);
        assert.equal(payload.end_s, 5);
        return route.fulfill({ contentType: 'video/mp4', body: fs.readFileSync(mediaPath) });
      }
      if (url.pathname === '/api/source') {
        const payload = request.postDataJSON();
        heartbeats.push({ ...payload, page: request.frame().url() });
        sourceTime = payload.time_s;
        return json(route, status());
      }
      if (url.pathname === '/api/control') {
        const payload = request.postDataJSON();
        if (payload.action === 'play' && mode !== 'replay') { mode = 'replay'; run = 'qa-clean-replay'; uploaded = true; sourceTime = 0; currentAnswer = null; }
        if (payload.action === 'select') selection = payload.index;
        if (payload.action === 'clear_answer') currentAnswer = null;
        if (payload.action === 'retry_processing') { retryCount++; statusOverrides = {}; }
        if (payload.action === 'resume_from_playhead') { statusOverrides = {coverage_gaps:[{start_s:2,end_s:4,lane:'vision',reason:'manual_resume'}]}; }
        if (['play', 'pause', 'restart'].includes(payload.action)) { sourceCommand = payload.action; sourceRevision++; }
        revision++;
        return json(route, { ok: true });
      }
      if (url.pathname === '/api/upload') {
        const bytes = request.postDataBuffer();
        if (bytes) assert(bytes.length > 0, 'Upload contains the file bytes when browser exposes them.');
        uploadedNames.push(url.searchParams.get('name'));
        run = 'qa-run-after-upload'; uploaded = true; sourceTime = 0; currentAnswer = null; revision++;
        return json(route, status());
      }
      return json(route, {});
    });

    const studio = await context.newPage();
    studio.on('pageerror', error => jsErrors.push(error.message));
    await studio.goto(base);
    await studio.getByText('8 sampled frames', { exact: true }).waitFor();
    await studio.locator('.getting-started').waitFor();
    assert((await studio.locator('.getting-started summary').textContent()).includes('How to use Recall'));
    await studio.getByRole('button', { name: 'Fill in an example' }).click();
    assert.equal(await studio.locator('#question').inputValue(), 'What happened in the last minute?');
    await studio.locator('#timeline').fill('2.123');
    assert.equal(Number(await studio.locator('#timeline').inputValue()), 2.123);
    sourceTime = 1;
    await studio.waitForFunction(() => Number(document.querySelector('#timeline').value) <= 1);
    sourceTime = 8;
    await studio.waitForTimeout(1100);
    assert.equal(await studio.locator('#cubeEmpty').isVisible(), false);
    assert.equal(await studio.locator('#cubeCanvas canvas').count(), 1);
    await studio.getByRole('button', { name: 'Catch me up' }).click();
    await studio.getByText('Synthetic QA answer with inspectable evidence.').waitFor();
    await studio.getByText('What the bot used',{exact:true}).click();
    assert((await studio.locator('.answer-context').textContent()).includes('audio-2'));
    assert.equal(await studio.locator('.answer-context img').count(),0,'Context excerpts are escaped.');
    await studio.getByText('What the bot used',{exact:true}).click();
    await studio.locator('#streamContext > summary').click();
    await studio.locator('#contextFeedback').getByText('Loaded context version',{exact:false}).waitFor();
    await studio.locator('#contextForm [name="streamer"]').fill('River');
    await studio.locator('#contextForm [name="character"]').fill('Ash');
    await studio.locator('#addContextPerson').click();
    await studio.locator('[data-person="name"]').fill('Zeek');
    await studio.locator('[data-person="aliases"]').fill('Z, Zeke');
    await studio.locator('#contextUtterance').selectOption('audio-2');
    await studio.locator('#contextSpeaker').fill('River');
    await studio.getByRole('button',{name:'Save stream context',exact:true}).click();
    await studio.locator('#contextFeedback').getByText('Saved.',{exact:false}).waitFor();
    assert.equal(creatorProfile.streamer,'River');
    assert.deepEqual(creatorProfile.cast[0].aliases,['Z','Zeke']);
    assert.equal(creatorProfile.speaker_bindings[0].evidence_id,'audio-2');
    await studio.screenshot({path:path.join(root,'.qa','stream-context.png'),fullPage:true});
    await studio.locator('#streamContext > summary').click();
    await studio.locator('#evidenceVideo').waitFor({ state: 'visible' });
    await studio.waitForFunction(() => document.querySelector('#evidenceVideo').readyState >= 1);
    assert.equal(heartbeats.length, 0, 'The operator does not send a source clock.');
    await studio.locator('#modelSettings > summary').click();
    await studio.locator('#localModel option[value="vision:large"]').waitFor({state:'attached'});
    assert(await studio.locator('#localModel option[value="text:only"]').isDisabled());
    await studio.locator('#localModel').selectOption('vision:large');
    await studio.getByRole('button',{name:'Use model',exact:true}).click();
    await studio.locator('#modelFeedback').getByText('Using vision:large.',{exact:false}).waitFor();
    assert.equal(selectedModel,'vision:large');
    await studio.locator('#modelSettings > summary').click();
    await studio.locator('#testMode > summary').click();
    await studio.locator('#testFeedback').getByText('Current setup:',{exact:false}).waitFor();
    assert(await studio.locator('#testForm option[value="cuda"]').evaluate(el=>el.disabled));
    assert(await studio.locator('#testForm option[value="tiny"]').evaluate(el=>el.disabled));
    await studio.locator('#testLight').click();
    assert.equal(await studio.locator('#testForm [name="model"]').inputValue(),'qwen3.5:4b');
    assert.equal(await studio.locator('#testForm [name="speech_model"]').inputValue(),'base');
    await studio.locator('#testForm [name="frame_interval_s"]').selectOption('10');
    await studio.locator('#testForm [name="allow_predictions"]').uncheck();
    await studio.getByRole('button',{name:'Start new test from beginning'}).click();
    await studio.locator('#testFeedback').getByText('Test ready with empty memory.',{exact:false}).waitFor();
    assert.equal(testSettings.frame_interval_s,10);
    assert.equal(testSettings.frame_width,768);
    assert.equal(testSettings.cpu_threads,4);
    assert.equal(testSettings.catch_up,'live');
    assert.equal(testSettings.vision_detail,'brief');
    assert.equal(testSettings.allow_predictions,false);
    assert.equal(testSettings.answer_style,'natural');
    const testDownload = studio.waitForEvent('download');
    await studio.locator('#testDownload').click();
    assert.equal((await testDownload).suggestedFilename(),'recall-test-results.json');
    await studio.screenshot({path:path.join(root,'.qa','test-mode.png'),fullPage:true});
    await studio.locator('#testMode > summary').click();
    await studio.locator('#diagnosticsPanel > summary').click();
    await studio.locator('#diagnosticsExplanation').getByText('The source player is paused.',{exact:false}).waitFor();
    assert((await studio.locator('#diagnosticsMetrics').innerText()).includes('11.0 GB'));
    const reportDownload = studio.waitForEvent('download');
    await studio.locator('#downloadDiagnostics').click();
    assert((await reportDownload).suggestedFilename().endsWith('.json'));
    await studio.locator('#diagnosticsPanel > summary').click();

    statusOverrides = {playback_s:80,duration_s:120,state:'playing'};
    await studio.getByText('Catching up · 72s behind · processing continues',{exact:true}).waitFor();
    assert.equal(await studio.locator('#processingRecovery').isVisible(),false);
    statusOverrides = {state:'paused',gap:'Vision processing failed',error:'Frame analysis stopped',retry_available:true};
    await studio.getByText('Processing stopped · searchable through 00:08',{exact:true}).waitFor();
    await studio.locator('#diagnosticsPanel > summary').click();
    await studio.locator('#diagnosticsExplanation').getByText('Processing is paused.',{exact:false}).waitFor();
    assert((await studio.locator('#diagnosticsMetrics').innerText()).includes('audio · 1.5 s + vision · 2.0 s'));
    const preservedRun = run;
    await studio.getByRole('button',{name:'Retry processing',exact:true}).click();
    await studio.locator('#processingRecovery').waitFor({state:'hidden'});
    assert.equal(retryCount,1);
    assert.equal(run,preservedRun,'Retry keeps the existing replay memory.');
    assert.equal(heartbeats.length,0,'Retry from the studio never moves source playback.');
    await studio.locator('#diagnosticsPanel > summary').click();
    statusOverrides={state:'paused',gap:'stopped',resume_available:true};
    await studio.locator('#liveRecovery').waitFor({state:'visible'});
    await studio.getByRole('button',{name:'Resume from playhead',exact:true}).click();
    await studio.locator('#coverageNotice').waitFor({state:'visible'});
    await studio.locator('#coverageNotice summary').click();
    await studio.getByText('00:02–00:04 · video · manual resume',{exact:true}).waitFor();
    assert.equal(run,preservedRun,'Resuming keeps prior memory and declares missing analysis.');
    statusOverrides={};
    await studio.locator('#coverageNotice').waitFor({state:'hidden'});

    mode = 'synthetic';
    const waitingDemo = await context.newPage();
    waitingDemo.on('pageerror', error => jsErrors.push(error.message));
    await waitingDemo.goto(`${base}/?view=demo`);
    await waitingDemo.getByText('READY TO START REPLAY', { exact: true }).waitFor();
    await waitingDemo.getByText('0 sampled frames', { exact: true }).waitFor();
    assert.equal((await waitingDemo.locator('#sourceClock').innerText()).replace(/\s+/g, ' '), '00:00 / 00:08');
    assert.equal(await waitingDemo.locator('#sourceVideo').evaluate(video => video.controls), false, 'Native controls cannot bypass replay reset.');
    assert.equal(await waitingDemo.locator('#evidencePanel').isVisible(), false, 'Precomputed evidence remains hidden.');
    await waitingDemo.locator('#demoInput').fill('/recall What happened?');
    await waitingDemo.locator('#demoChatForm button').click();
    await waitingDemo.getByText('Start the local replay first. Memory begins empty and builds as it plays.').waitFor();
    await waitingDemo.locator('#startBroadcast').click();
    await waitingDemo.waitForFunction(() => document.querySelector('#modeBadge').textContent === 'REPLAY');
    assert.equal(run, 'qa-clean-replay', 'Starting a recorded demo creates a fresh replay.');
    assert.equal(await waitingDemo.getByText('0 sampled frames', { exact: true }).isVisible(), true);
    await waitingDemo.close();
    heartbeats.length = 0;
    mode = 'replay'; run = 'qa-run'; uploaded = false;
    sourceTime = 0;
    const freshDemo = await context.newPage();
    await freshDemo.goto(`${base}/?view=demo`);
    await freshDemo.getByText('Simulated replay · not connected to Twitch',{exact:true}).waitFor();
    await freshDemo.locator('#demoChat [data-action="play"]').click();
    await freshDemo.waitForFunction(() => document.querySelector('#sourceVideo').currentTime > .3);
    assert(heartbeats.length > 0,'Play in a fresh demo starts the local player and source heartbeats.');
    await freshDemo.locator('#sourceVideo').evaluate(video=>video.pause());
    await freshDemo.waitForTimeout(100);
    await freshDemo.close();
    heartbeats.length = 0;
    sourceTime = 8;
    const demo = await context.newPage();
    demo.on('pageerror', error => jsErrors.push(error.message));
    await demo.goto(`${base}/?view=demo`);
    await demo.getByText('Simulated replay · not connected to Twitch', { exact: true }).waitFor();
    const videoBox = await demo.locator('#sourcePanel').boundingBox();
    const chatBox = await demo.locator('#demoChat').boundingBox();
    assert(videoBox.width > chatBox.width * 1.8 && videoBox.x < chatBox.x, 'Watch demo prioritizes a large video beside chat.');
    assert.equal(await demo.getByRole('link', { name: 'Back to studio' }).getAttribute('target'), 'recall-studio', 'Opening studio preserves the replay tab.');
    await demo.screenshot({ path: path.join(root, '.qa', 'watch-demo.png'), fullPage: true });
    await demo.locator('#demoInput').fill('/recall What happened?');
    await demo.locator('#demoChatForm button').click();
    await demo.locator('#demoMessages .chat-answer').getByText('Synthetic QA answer with inspectable evidence.', { exact: true }).waitFor();
    const momentTabPromise = context.waitForEvent('page');
    await demo.locator('.chat-matches a').click();
    const momentTab = await momentTabPromise;
    momentTab.on('pageerror', error => jsErrors.push(error.message));
    await momentTab.locator('#momentTitle').getByText('QA moment', { exact: true }).waitFor();
    try { await momentTab.waitForFunction(() => document.querySelector('#momentVideo').currentTime >= 4); }
    catch (error) { console.error(await momentTab.locator('#momentVideo').evaluate(v => ({time:v.currentTime,duration:v.duration,ready:v.readyState,error:v.error?.message,status:document.querySelector('#momentStatus').textContent}))); throw error; }
    await momentTab.locator('#momentVideo').evaluate(v => { v.currentTime = 7; });
    await momentTab.waitForFunction(() => document.querySelector('#momentVideo').currentTime <= 5.05);
    const momentDownload = momentTab.waitForEvent('download');
    await momentTab.locator('#downloadClip').click();
    assert((await momentDownload).suggestedFilename().endsWith('.mp4'));
    await momentTab.evaluate(() => {
      Object.defineProperty(navigator, 'canShare', { configurable:true, value:() => true });
      Object.defineProperty(navigator, 'share', { configurable:true, value:async () => { throw new DOMException('Click expired', 'NotAllowedError'); } });
    });
    const fallbackDownload = momentTab.waitForEvent('download');
    await momentTab.locator('#shareClip').click();
    assert((await fallbackDownload).suggestedFilename().endsWith('.mp4'), 'Native share failure still delivers the clip.');
    await momentTab.getByText('The browser could not open file sharing. The clip was downloaded so you can share it.').waitFor();
    await momentTab.screenshot({ path:path.join(root, '.qa', 'moment-page.png'), fullPage:true });
    assert.equal(heartbeats.length, 0, 'Watching a linked moment never drives or seeks the main source.');
    await momentTab.close();
    await demo.locator('#demoInput').fill('Hello everyone');
    await demo.locator('#demoChatForm button').click();
    await demo.locator('#demoMessages').getByText('Hello everyone', { exact: false }).waitFor();
    await demo.locator('#demoInput').fill('/ask Give a long summary');
    await demo.locator('#demoChatForm button').click();
    await demo.locator('#demoMessages .chat-answer').last().getByText('Spoken transcript [0:04]:', { exact:false }).waitFor();
    const shortAnswer = await demo.locator('#demoMessages .chat-answer').last().textContent();
    assert(shortAnswer.length <= 220 && shortAnswer.endsWith('…'), 'Chat includes a concise answer with its source attribution.');
    assert.equal(await demo.locator('#demoMessages .chat-answer img').count(), 0, 'Source text is escaped before display.');
    await demo.locator('#demoInput').fill('/RECALL Show many matches');
    await demo.locator('#demoChatForm button').click();
    await demo.locator('#demoMessages').getByText('Possible matching moments:', { exact:false }).waitFor();
    assert.equal(await demo.locator('.chat-matches').last().locator('a').count(), 3, 'The bot returns at most three moment links.');
    assert.equal(await demo.locator('#evidencePanel').isVisible(), true, 'A simulated viewer gets real selectable evidence.');
    assert.equal(heartbeats.length, 0, 'Opening the demo does not take ownership of the replay until playback starts.');
    const download = demo.waitForEvent('download');
    await demo.locator('#saveClip').click();
    assert((await download).suggestedFilename().endsWith('.mp4'), 'Selected evidence downloads as an MP4.');
    await demo.locator('#demoInput').fill('/recall An unanswerable question');
    await demo.locator('#demoChatForm button').click();
    await demo.locator('#demoMessages').getByText('The recording does not establish why this happened.',{exact:false}).waitFor();
    await demo.setViewportSize({ width: 390, height: 844 });
    assert.equal(await demo.evaluate(() => document.documentElement.scrollWidth), 390, 'Watch demo fits mobile.');
    await demo.close();
    sourceTime = 0;

    const onAir = await context.newPage();
    onAir.on('pageerror', error => jsErrors.push(error.message));
    await onAir.goto(`${base}/broadcast`);
    await onAir.locator('#startBroadcast').click();
    await onAir.waitForFunction(() => document.querySelector('#sourceVideo').currentTime > 1.2);
    const before = await onAir.locator('#sourceVideo').evaluate(video => video.currentTime);
    await studio.locator('#evidenceVideo').evaluate(video => { video.currentTime = 6; });
    await studio.waitForFunction(() => document.querySelector('#evidenceVideo').currentTime <= 5.01);
    const after = await onAir.locator('#sourceVideo').evaluate(video => video.currentTime);
    assert(after >= before && after < before + 1, 'Seeking evidence never seeks the source.');
    assert(heartbeats.length > 0, 'Broadcast supplies source heartbeats.');
    assert(heartbeats.every(h => h.page.endsWith('/broadcast') || h.page.includes('view=demo')), 'Only a replay view sends a source clock.');
    assert(heartbeats.some(h => h.frame && h.frame.data.startsWith('/9j/')), 'Source captures a real JPEG frame.');
    assert(heartbeats.every(h => h.source_id && h.run_id === 'qa-run'));
    await studio.getByRole('button', { name: 'Pause', exact: true }).click();
    await onAir.waitForFunction(() => document.querySelector('#sourceVideo').paused);
    await studio.getByRole('button', { name: 'Clear', exact: true }).click();
    assert.equal(run, 'qa-run', 'Clearing an answer does not reset stream memory.');

    await studio.setViewportSize({ width: 390, height: 844 });
    await studio.locator('#streamContext > summary').click();
    await studio.locator('#contextFeedback').getByText('Loaded context version',{exact:false}).waitFor();
    assert.equal(await studio.evaluate(()=>document.documentElement.scrollWidth),390,'Context editor fits mobile.');
    await studio.locator('#streamContext > summary').click();
    assert.equal(await studio.evaluate(() => document.documentElement.scrollWidth), 390, 'Mobile layout does not overflow.');
    await studio.locator('#timeline').focus();
    await studio.keyboard.press('ArrowRight');
    assert.equal(await studio.locator('#evidencePanel').isVisible(), true, 'Keyboard timeline navigation exposes evidence.');
    await studio.locator('#fileInput').setInputFiles(mediaPath);
    await studio.getByText('Recording loaded. Open stream demo and press Start to begin.').waitFor();
    assert.equal(run, 'qa-run-after-upload');
    assert.equal(await studio.locator('#evidencePanel').isVisible(), false, 'A new recording clears prior evidence.');
    assert.equal(await studio.getByText('0 sampled frames', { exact: true }).isVisible(), true);
    await studio.evaluate(() => {
      const input = document.querySelector('#fileInput');
      const file = new File(['test'], 'large-test.mp4', { type: 'video/mp4' });
      Object.defineProperty(file, 'size', { value: 4 * 1024 * 1024 * 1024 });
      Object.defineProperty(input, 'files', { configurable: true, value: [file] });
      input.dispatchEvent(new Event('change'));
    });
    await studio.waitForFunction(() => !document.querySelector('#uploadButton').disabled);
    assert(uploadedNames.includes('large-test.mp4'), 'A declared 4 GiB recording passes the client size check.');
    await studio.evaluate(() => {
      const input = document.querySelector('#fileInput');
      const file = new File(['test'], 'too-large.mp4', { type: 'video/mp4' });
      Object.defineProperty(file, 'size', { value: 8 * 1024 * 1024 * 1024 + 1 });
      Object.defineProperty(input, 'files', { configurable: true, value: [file] });
      input.dispatchEvent(new Event('change'));
    });
    await studio.getByText('Choose a recording no larger than 8 GiB.').waitFor();
    assert(!uploadedNames.includes('too-large.mp4'));
    assert.deepEqual(jsErrors, [], 'No uncaught browser errors.');
    assert(telemetry.some(item=>item.event==='browser_play') && telemetry.some(item=>item.event==='browser_pause'),'Playback lifecycle is logged.');
    assert(telemetry.some(item=>item.event==='browser_heartbeat_complete'),'Source timing is logged.');
    assert(telemetry.every(item=>!['question','text','frame','data','csrf_token','url','message'].some(key=>key in item)),'Diagnostics exclude content and credentials.');
    console.log('PASS: textured cube, local-token requests, grounded cards, independent source/evidence, JPEG capture, remote pause, keyboard timeline, mobile layout, and upload run reset.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
