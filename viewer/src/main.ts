import './demo.css';
import { TimeCube } from './cube';
import { installDiagnostics } from './diagnostics';
import { installModelSettings } from './models';
import { installTestMode } from './testmode';
import { installStreamContext, renderAnswerContext } from './context';
import { perspectiveNav } from './perspectives';
import type { Answer, Evidence, Frame, Memory, MemoryRecord, Shared, Status } from './types';

const icons = {
  cube: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9M8 5.3l8 4.5"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  play: '<path d="m8 5 11 7-11 7V5Z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  reset: '<path d="M4 10a8 8 0 1 1 1 7M4 4v6h6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  external: '<path d="M14 4h6v6m-1-5-9 9M10 4H4v16h16v-6"/>',
  left: '<path d="m14 6-6 6 6 6"/>',
  right: '<path d="m10 6 6 6-6 6"/>',
  sound: '<path d="M3 10h4l5-5v14l-5-5H3v-4Zm13-2a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
};
function icon(name: keyof typeof icons, cls = '') { return `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`; }
function escape(value: unknown) { return String(value ?? '').replace(/[&<>"']/g, s => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[s]!); }
function conciseAnswer(value: string | undefined) {
  const text = (value || '').replace(/\s+/g, ' ').trim();
  if (text.length <= 220) return text;
  const clipped = text.slice(0, 219);
  const boundary = clipped.lastIndexOf(' ');
  return `${(boundary > 0 ? clipped.slice(0, boundary) : clipped).trimEnd()}…`;
}
function time(t: number | undefined) {
  const seconds = Math.max(0, Math.floor(Number(t) || 0));
  const h = Math.floor(seconds / 3600);
  return `${h ? `${h}:` : ''}${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}
function safeUrl(path: string | undefined) {
  if (!path) return '';
  try { const url = new URL(path, location.origin); return url.origin === location.origin ? url.pathname + url.search : ''; } catch { return ''; }
}

const demo = new URLSearchParams(location.search).get('view') === 'demo';
const broadcast = demo || /^\/(broadcast|overlay)\/?$/.test(location.pathname);
document.body.classList.toggle('broadcast', broadcast);
document.body.classList.toggle('demo', demo);
document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
  <header class="topbar">
    <a class="brand" href="/" target="recall-studio" aria-label="Recall studio">${icon('cube')}<span class="brand-name">Recall</span><span class="brand-divider"></span><span class="brand-studio">${demo ? 'WATCH DEMO' : broadcast ? 'BROADCAST' : 'STUDIO'}</span></a>
    ${demo ? '<span class="demo-disclosure"><i></i> Simulated replay · not connected to Twitch</span>' : ''}
    <div class="top-actions"><span class="privacy">${icon('shield')}<span>Local by design</span></span><a class="button subtle" href="${demo ? '/' : '/?view=demo'}" target="${demo ? 'recall-studio' : 'recall-replay'}">${demo ? 'Back to studio' : 'Open stream demo'}</a><a class="button subtle operator-only" href="/broadcast" target="_blank" rel="noopener">${icon('external')}Open local replay</a><button class="button load-button operator-only" id="uploadButton">${icon('upload')}Load recording</button><input id="fileInput" type="file" accept="video/*,.mp4,.mkv,.mov,.webm" hidden /></div>
  </header>
  ${perspectiveNav(demo ? 'demo' : broadcast ? 'replay' : 'studio')}
  <main>
    <section class="heading-row">
      <div><div class="eyebrow"><span class="tiny-square"></span> ${broadcast ? 'THE SHARED VIEW' : 'YOUR STREAM, REMEMBERED'}</div><h1>Every moment.<span> Within reach.</span></h1><p class="intro">Ask about the story. See the evidence. Keep the stream moving.</p></div>
      <div class="session-card"><span class="session-label">CURRENT RECORDING <span id="modeBadge" class="mode-badge">CONNECTING</span></span><strong id="recordingTitle">Waiting for your studio</strong><div class="session-meta"><span id="sourceState" class="state"><i></i>Connecting</span><span class="mono" id="sourceClock">00:00 <span>/ 00:00</span></span></div></div>
    </section>
    <div id="connectionBanner" class="banner" role="status" hidden></div>
    <div id="twitchConnectionNotice" class="banner" role="status" hidden>Twitch chat needs a new broadcaster sign-in. Renew the Twitch token and restart Recall. Local video processing and Studio questions still work.</div>
    <div id="processingRecovery" class="banner" hidden><span>Keep the memory already built and retry the failed processing step.</span> <button class="button" data-action="retry_processing">Retry processing</button></div>
    <div id="liveRecovery" class="banner" hidden><span>Continue from the playhead and keep earlier memory. The skipped interval will be marked as unanalyzed.</span> <button class="button" data-action="resume_from_playhead">Resume from playhead</button></div>
    <details id="coverageNotice" class="banner" hidden><summary>Some intervals were not analyzed. Answers may miss those moments.</summary><p id="coverageRanges"></p></details>
    <details class="getting-started operator-only" open>
      <summary><span><span class="guide-kicker">START HERE</span> How to use Recall</span><span class="guide-summary-right"><span id="guideReady">Checking your recording…</span><span class="guide-chevron">⌄</span></span></summary>
      <div class="guide-steps">
        <div><span class="guide-number">01</span><strong>Ask a question</strong><p>Type a question about the recording in the box on the right, then press the arrow.</p><button type="button" class="guide-link" id="guideExample">Fill in an example →</button></div>
        <div><span class="guide-number">02</span><strong>Explore the evidence</strong><p>Select a result to open the source moment. Drag the cube to rotate it, or use the timeline.</p></div>
        <div><span class="guide-number">03</span><strong>Try the viewer experience</strong><p>Choose <b>Load recording</b>, then <b>Open stream demo</b> and Start. Type <b>/recall</b> in chat and open a matching moment.</p></div>
      </div><p class="guide-note"><b>Studio</b> is your control desk. <b>Stream demo</b> is the Twitch-style watch page with simulated chat. <b>Local replay</b> is the simpler source player for OBS; you can skip it for the demo. These pages do not put you live on Twitch.</p>
    </details>
    <section class="workspace">
      <div class="main-column">
        <section class="source-panel panel" id="sourcePanel">
          <div class="panel-header"><span class="section-label"><span class="dot mint"></span>LOCAL REPLAY SOURCE</span><span class="tag">REPLAY SOURCE</span></div>
          <div class="source-screen"><video id="sourceVideo" controls playsinline preload="metadata"></video><div id="sourceEmpty" class="video-empty">${icon('upload')}<strong>No recording loaded</strong><span>Load a recording from the studio.</span></div><button id="startBroadcast" class="start-broadcast">${icon('play')}Start local replay</button></div>
          <div class="source-footer"><span>Stream playback drives the memory.</span><span class="mono" id="sourceCapture">Waiting for source</span></div>
        </section>
        <section class="cube-panel panel">
          <div class="panel-header"><div><span class="section-label">${icon('cube')} THE TIME CUBE</span><span class="panel-subtitle">Scrub the actual video frame. Side faces visualise sampled slices.</span></div><span class="cube-view-tag">3D VIEW <span>↗</span></span></div>
          <div class="cube-stage"><div class="stage-top"><span class="stage-coordinate">X · IMAGE &nbsp; Y · IMAGE &nbsp; Z · TIME</span><span class="sample-count" id="sampleCount">0 sampled frames</span></div><div id="cubeCanvas"></div><div class="cube-empty" id="cubeEmpty"><span class="empty-orbit">${icon('cube')}</span><strong>Your stream takes shape here.</strong><span>Load a recording and start the broadcast.<br>Captured moments will fill this space.</span></div><div class="stage-bottom"><div class="cube-key"><span><i class="key-frame"></i>Frames</span><span><i class="key-topic"></i>Topics</span><span><i class="key-match"></i>Evidence</span></div><span class="drag-hint">DRAG TO ORBIT · SCROLL TO ZOOM</span></div></div>
          <div class="cube-toolbar"><div class="cube-time"><span class="dot mint"></span><span id="inspectionLabel">Following the source</span><strong class="mono" id="inspectionClock">00:00</strong></div><div class="view-controls"><button id="zoomOut" class="icon-button" aria-label="Zoom out" title="Zoom out">${icon('minus')}</button><button id="zoomIn" class="icon-button" aria-label="Zoom in" title="Zoom in">${icon('plus')}</button><span class="control-divider"></span><button id="resetView" class="text-button">${icon('reset')}Reset view</button></div></div>
        </section>
        <section class="timeline-panel panel">
          <div class="timeline-heading"><span class="section-label">STREAM TIMELINE</span><span class="muted">Navigate evidence without interrupting the source</span></div>
          <div class="timeline-wrap"><div class="timeline-tracks" id="timelineTracks"><div class="track-line vision-line"></div><div class="track-line transcript-line"></div><div class="timeline-progress" id="timelineProgress"></div><div class="timeline-markers" id="timelineMarkers"></div></div><input id="timeline" type="range" min="0" max="1" value="0" step="0.001" aria-label="Inspect recording evidence time" /><div class="timeline-ticks" id="timelineTicks"><span>00:00</span><span>00:00</span><span>00:00</span><span>00:00</span><span>00:00</span></div></div>
          <div class="timeline-footer"><span><i class="key-frame"></i>Visual memory <span id="visionThrough">00:00</span></span><span><i class="key-topic"></i>Transcript <span id="audioThrough">00:00</span></span><span class="processing-lag" id="lagLabel">Waiting for memory</span></div>
        </section>
        <section class="transcript-panel panel"><div class="panel-header"><span class="section-label">${icon('sound')} TRANSCRIPT</span><span class="tag">SOURCE WORDS</span></div><div id="transcript" class="transcript-content"><p class="quiet-empty">Recognized speech will appear here as the replay is processed.</p></div></section>
        <div class="bottom-note">${icon('shield')}Video, frames and answers stay on this computer.<span>Topic ribbons connect related moments; they do not track physical movement.</span></div>
      </div>
      <aside class="side-column">
        <section class="demo-chat panel" id="demoChat" ${demo ? '' : 'hidden'}>
          <div class="panel-header"><span class="section-label">STREAM CHAT</span><span class="demo-badge">LOCAL DEMO</span></div>
          <p class="demo-help">Try the viewer experience: type <b>/recall</b> and a question. Recall answers from moments processed so far.</p>
          <div id="demoMessages" class="demo-messages" role="log" aria-live="polite"><p><strong>Recall</strong> Welcome to the replay. Try <b>/recall What happened so far?</b></p></div>
          <form id="demoChatForm"><label for="demoInput">Message the demo chat</label><div class="demo-compose"><input id="demoInput" maxlength="1000" placeholder="/recall What happened so far?" autocomplete="off"/><button class="button load-button" type="submit">Send</button></div></form>
          <p class="demo-controls"><button class="button" data-action="pause">${icon('pause')}Pause</button><button class="button" data-action="play">${icon('play')}Play</button><button class="button" data-action="restart">${icon('reset')}Restart replay</button><span>Restart clears memory; then press Start local replay. Close other replay tabs first.</span></p>
        </section>
        <section class="ask-panel panel">
          <div class="panel-header"><span class="section-label"><span class="ask-spark">✳</span> ASK THE STREAM</span><span class="tag">LOCAL AI</span></div>
          <div class="ask-body"><h2>Curiosity has a rewind button.</h2><p>Get back into the story, one question at a time.</p><form id="askForm"><label class="sr-only" for="question">Question about this recording</label><textarea id="question" rows="2" maxlength="1000" placeholder="What did I miss in the last 5 minutes?"></textarea><div class="ask-form-footer"><span id="askHint">Answers grounded in captured moments</span><button class="ask-submit" id="askSubmit" type="submit" aria-label="Ask the stream">${icon('arrow')}</button></div></form><div class="suggestion-label">A GOOD PLACE TO START</div><div class="suggestions" id="suggestions"><button data-question="What has happened so far?">Catch me up ${icon('arrow')}</button><button data-question="What are they doing right now?">What’s happening now? ${icon('arrow')}</button><button data-question="What did they say they would do next?">What’s the plan? ${icon('arrow')}</button></div></div>
        </section>
        <section id="answerPanel" class="answer-panel panel"><div class="panel-header"><span class="section-label">THE ANSWER</span><button id="clearAnswer" class="text-button" hidden>Clear</button></div><div id="answerContent" class="answer-content"><div class="answer-empty">${icon('search')}<strong>A question opens a moment.</strong><span>Your answer and its source evidence<br>will appear here.</span></div></div></section>
        <section class="evidence-panel panel" id="evidencePanel" hidden><div class="panel-header"><span class="section-label">${icon('eye')} EVIDENCE PLAYER</span><span class="tag">SEPARATE FROM LIVE</span></div><div class="evidence-screen"><video id="evidenceVideo" controls playsinline preload="none"></video><img id="evidenceImage" alt="Captured source frame for selected evidence" hidden /><div id="evidenceNoVideo" class="quiet-empty" hidden>Source video is unavailable. Read the evidence excerpt below.</div></div><div class="evidence-caption"><span class="mono" id="evidenceTime">00:00</span><span id="evidenceLabel"></span></div><button id="saveClip" class="button save-clip" type="button" hidden>Save selected evidence clip</button></section>
        <section class="source-controls panel operator-only"><div class="panel-header"><span class="section-label">REPLAY CONTROLS</span><span class="dot mint"></span></div><div class="source-buttons"><button class="button" data-action="play">${icon('play')}Play</button><button class="button" data-action="pause">${icon('pause')}Pause</button><button class="button" data-action="restart">${icon('reset')}Restart</button></div><label class="toggle-label"><span>Enable audience cube controls</span><input id="audienceControls" type="checkbox" /><span class="toggle"></span></label><p class="control-help" id="twitchHelp">Playback runs in the local replay tab. Twitch chat is not connected.</p></section>
      </aside>
    </section>
    <footer class="page-footer"><span>MADE FOR THE MOMENTS IN BETWEEN.</span><span id="providerLabel">ON-DEVICE INFERENCE</span><span>RECALL <i>↗</i></span></footer>
  </main><div id="toast" class="toast" role="status" hidden></div>`;

// The watch simulation presents the viewer's stream first, with the local
// memory inspector below it. All existing controls retain their IDs/behavior.
if (demo) {
  const mainColumn = document.querySelector('.main-column')!;
  const channel = document.createElement('section');
  channel.className = 'demo-channel';
  channel.setAttribute('aria-label', 'Replay channel');
  channel.innerHTML = '<div class="demo-avatar" aria-hidden="true">R</div><div class="demo-channel-info"><span class="demo-channel-name">Your channel <span>REPLAY</span></span><div class="demo-recording"></div></div><nav class="demo-channel-actions" aria-label="Explore this replay"><a class="button" href="#demoMemory">Explore memory</a><a class="button subtle" href="#answerPanel">View answer &amp; clip</a></nav>';
  channel.querySelector('.demo-recording')!.append(document.querySelector('.session-card')!);
  document.querySelector('#sourcePanel')!.after(channel);
  const inspector = document.createElement('section');
  inspector.id = 'demoMemory';
  inspector.className = 'demo-inspector';
  inspector.innerHTML = '<div class="demo-inspector-heading"><div><span class="demo-feature-label">RECALL · STREAM MEMORY</span><h2>Go back to the moment.</h2></div><p>Explore earlier footage without interrupting the replay above.</p></div><div class="demo-inspector-grid"><div class="demo-visual-memory"></div><div class="demo-evidence-memory"></div></div>';
  for (const selector of ['.cube-panel', '.timeline-panel', '.transcript-panel']) inspector.querySelector('.demo-visual-memory')!.append(document.querySelector(selector)!);
  for (const selector of ['#answerPanel', '#evidencePanel']) inspector.querySelector('.demo-evidence-memory')!.append(document.querySelector(selector)!);
  mainColumn.append(inspector);
  mainColumn.append(document.querySelector('.bottom-note')!);
}

if (demo) {
  const help = document.querySelector('.demo-help')!;
  help.insertAdjacentHTML('beforeend', '<br><label class="sample-chat-toggle"><input id="sampleChat" type="checkbox" checked /> Show scripted demo viewers</label>');
  const seedChat = () => {
    const messages = document.querySelector('#demoMessages')!;
    messages.querySelectorAll('.scripted-viewer').forEach(row => row.remove());
    if (!(document.querySelector('#sampleChat') as HTMLInputElement).checked) return;
    for (const [name, line] of [['RiverFan', 'Hey chat 👋'], ['PixelFern', 'Just joined. Can I look up what I missed?'], ['DemoMod', 'Try /recall followed by a question. Matching moments open in a new tab.']]) {
      const row = document.createElement('p'); row.className = 'scripted-viewer';
      row.innerHTML = `<strong>${name}</strong><small>DEMO</small> ${line}`; messages.append(row);
    }
  };
  document.querySelector('#sampleChat')!.addEventListener('change', seedChat);
  seedChat();
  document.addEventListener('tube-run-change', seedChat);
}

const $ = <T extends HTMLElement = HTMLElement>(selector: string) => document.querySelector<T>(selector)!;
let status: Status | null = null;
let shared: Shared | null = null;
let memory: Memory = { records: [], frames: [] };
let answer: Answer | null = null;
let selected = 0;
let inspection: number | null = null;
let asking = false;
let sourceRun = '';
let evidenceRun = '';
let lastSourceRevision = -1;
let sourceBusy = false;
let sourceEngaged = false;
let lastFrameBucket = -1;
let lastFrameTime = -1;
let lastAnswerKey = '';
let toastTimer = 0;
let offlineFailures = 0;
let pendingEvidence: number | null = null;
let evidenceLimit = 0;
const sourceVideo = $<HTMLVideoElement>('#sourceVideo');
const evidenceVideo = $<HTMLVideoElement>('#evidenceVideo');
if (broadcast) evidenceVideo.muted = true;
const captureCanvas = document.createElement('canvas');
const sourceId = sessionStorage.getItem('tube-source-id') || crypto.randomUUID();
sessionStorage.setItem('tube-source-id', sourceId);
const cube = new TimeCube($('#cubeCanvas'), t => inspect(t));
const diagnostics = installDiagnostics({video:sourceVideo,role:demo ? 'demo' : broadcast ? 'broadcast' : 'studio',sourceId,status:()=>status,engaged:()=>sourceEngaged,busy:()=>sourceBusy});
installModelSettings(request);
installTestMode(request);
installStreamContext(request);

function toast(message: string) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { $('#toast').hidden = true; }, 5000);
}

async function request<T>(path: string, data?: unknown, raw?: File): Promise<T> {
  const response = await fetch(path, {
    method: data === undefined && !raw ? 'GET' : 'POST',
    headers: data === undefined && !raw ? {} : { 'X-Tube-Token': status?.csrf_token || '', ...(raw ? { 'Content-Type': 'application/octet-stream' } : { 'Content-Type': 'application/json' }) },
    body: raw || (data === undefined ? undefined : JSON.stringify(data)),
    signal: AbortSignal.timeout(path.startsWith('/api/ask') || path === '/api/test/start' || raw ? 180000 : 15000),
    cache: 'no-store',
  });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(typeof result.error === 'string' ? result.error : result.detail || `Request failed (${response.status})`), {http_status:response.status});
  return result as T;
}

function uploadRecording(file: File, progress: (message: string) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/upload?name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader('X-Tube-Token', status?.csrf_token || '');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) progress(event.loaded >= event.total ? 'Checking recording…' : `Loading locally… ${Math.round(event.loaded / event.total * 100)}%`);
    };
    xhr.onerror = () => reject(new Error('Could not upload this recording. Check that the local studio is running.'));
    xhr.onabort = () => reject(new Error('The recording upload was cancelled.'));
    xhr.onload = () => {
      let result: {error?: string; detail?: string} = {};
      try { result = JSON.parse(xhr.responseText); } catch { /* Fall back to the HTTP status. */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(result.error || result.detail || `Upload failed (${xhr.status}).`));
    };
    xhr.send(file);
  });
}

async function control(action: string, extra: Record<string, unknown> = {}) {
  if (['play','pause','restart','retry_processing'].includes(action)) diagnostics.event('browser_control',{action});
  try { await request('/api/control', { action, ...extra }); await pollShared(); }
  catch (error) { toast(error instanceof Error ? error.message : 'Could not update the broadcast.'); }
}

function changeRun(run: string) {
  if (status?.run_id === run) return;
  memory = { records: [], frames: [] };
  answer = null;
  selected = 0;
  inspection = null;
  lastAnswerKey = '';
  lastSourceRevision = -1;
  lastFrameBucket = -1;
  lastFrameTime = -1;
  sourceRun = '';
  evidenceRun = '';
  $('#evidencePanel').hidden = true;
  evidenceVideo.pause();
  evidenceVideo.removeAttribute('src');
  evidenceVideo.load();
  if (demo) { $('#demoMessages').innerHTML = '<p><strong>Recall</strong> Replay ready. Try <b>/recall What happened so far?</b></p>'; document.dispatchEvent(new Event('tube-run-change')); }
  cube.update([], [], 1);
  cube.setVideoSource(null, '', 0);
  cube.highlight([], 0);
  renderAnswer();
}

function renderStatus() {
  if (!status) return;
  diagnostics.update(status);
  const demoWaiting = demo && status.mode !== 'replay';
  const availableTime = demoWaiting ? 0 : Math.max(0, Math.min(status.playback_s, status.processed_through_s, status.duration_s));
  if (inspection !== null) inspection = Math.min(inspection, availableTime);
  const fixture = /fixture|scripted/i.test(status.mode || '');
  const playing = /playing|running|live/i.test(status.state || '');
  $('#modeBadge').textContent = demoWaiting ? 'READY TO START REPLAY' : fixture ? 'SCRIPTED FIXTURE' : /replay/i.test(status.mode) ? 'REPLAY' : status.mode?.toUpperCase() || 'LOCAL';
  $('#modeBadge').classList.toggle('fixture', fixture);
  $('#recordingTitle').textContent = status.title || 'Untitled recording';
  const ready = status.processed_through_s > 0;
  $('#guideReady').textContent = ready ? `Ready to ask through ${time(status.processed_through_s)}${status.gap ? ' · later processing stopped' : ''}` : status.has_video ? 'Start the local replay to process your recording' : 'Load a recording to begin';
  const twitchConnected = /connected/i.test(status.twitch?.state || '') && !/not_connected|disconnected/i.test(status.twitch?.state || '');
  const twitchNeedsSignIn = status.twitch?.state === 'authorization_required';
  $('#twitchConnectionNotice').hidden = !twitchNeedsSignIn;
  $('#twitchHelp').textContent = twitchConnected ? 'Twitch chat connected. Playback runs in the local replay tab.' : 'Playback runs in the local replay tab. Twitch chat is not connected yet.';
  if (twitchNeedsSignIn) $('#twitchHelp').textContent = 'Twitch authorization is no longer valid. Renew the broadcaster token and restart Recall to restore chat replies.';
  $('#sourceClock').innerHTML = `${time(demoWaiting ? 0 : status.playback_s)} <span>/ ${time(status.duration_s)}</span>`;
  const stateLabel = demoWaiting ? 'Ready to start replay' : status.state?.replaceAll('_', ' ') || 'Ready';
  $('#sourceState').innerHTML = `<i class="${playing ? 'active' : ''}"></i>${escape(stateLabel)}`;
  $('#providerLabel').textContent = fixture ? 'SCRIPTED DATA · NOT AI-DERIVED' : status.provider ? `${status.provider.toUpperCase()}${/local/i.test(status.provider) ? '' : ' · LOCAL'}` : 'ON-DEVICE INFERENCE';
  $('#visionThrough').textContent = time(demoWaiting ? 0 : status.vision_through_s);
  $('#audioThrough').textContent = time(demoWaiting ? 0 : status.audio_through_s);
  const lag = demoWaiting ? 0 : Math.max(0, status.playback_s - status.processed_through_s);
  const gap = Boolean(status.gap);
  $('#lagLabel').textContent = gap ? `Processing stopped · searchable through ${time(status.processed_through_s)}` : lag > 15 ? `Catching up · ${Math.round(lag)}s behind · processing continues` : lag > 1 ? `${Math.round(lag)}s behind source` : memory.records.length ? 'Memory up to date' : 'Waiting for memory';
  $('#lagLabel').title = gap ? String(status.gap) : 'Processing delay does not discard footage. Answers use only completed speech and frame analysis.';
  $('#processingRecovery').hidden = !status.retry_available;
  $('#liveRecovery').hidden = !status.resume_available || (!status.gap && lag < 30);
  const holes = status.coverage_gaps || [];
  $('#coverageNotice').hidden = !holes.length;
  $('#coverageRanges').textContent = holes.slice(-10).map(g=>`${time(g.start_s)}–${time(g.end_s)} · ${g.lane === 'audio' ? 'speech' : 'video'} · ${g.reason.replaceAll('_',' ')}`).join('; ');
  if (holes.length && !gap) $('#lagLabel').textContent += ' · partial coverage';
  $('#lagLabel').title = status.pipeline?.catch_up === 'live' ? 'Live mode skips stale frames and records missing analysis. Speech has its own worker.' : $('#lagLabel').title;
  $('#lagLabel').classList.toggle('warning', gap || lag > 15);
  $('#timelineProgress').style.width = `${Math.min(100, (demoWaiting ? 0 : status.processed_through_s) / Math.max(1, status.duration_s) * 100)}%`;
  const timeline = $<HTMLInputElement>('#timeline');
  timeline.max = String(Math.max(1, status.duration_s));
  if (document.activeElement !== timeline || Number(timeline.value) > availableTime) timeline.value = String(inspection ?? availableTime);
  timeline.setAttribute('aria-valuetext', `${time(inspection ?? availableTime)} of ${time(status.duration_s)}`);
  $('#timelineTicks').innerHTML = [0, .25, .5, .75, 1].map(p => `<span>${time(status!.duration_s * p)}</span>`).join('');
  $('#inspectionClock').textContent = time(inspection ?? availableTime);
  $('#inspectionLabel').textContent = inspection === null ? 'Following the source' : 'Inspecting evidence';
  cube.setVideoSource(status.has_video && !demoWaiting ? `${safeUrl(memory.server?.video_url) || '/video'}?run=${encodeURIComponent(status.run_id)}` : null, status.run_id, availableTime);
  cube.setTime(inspection ?? availableTime);
  $('#sourceEmpty').hidden = status.has_video;
  if (demo) sourceVideo.controls = false;
  $('#startBroadcast').innerHTML = `${icon('play')}${status.mode === 'replay' && status.playback_s > 0 ? 'Resume replay' : 'Start local replay'}`;
  $('#startBroadcast').hidden = !broadcast || !status.has_video || (!sourceVideo.paused && sourceVideo.readyState >= 2);
  document.querySelectorAll<HTMLButtonElement>('.source-buttons button').forEach(button => { button.disabled = !status?.has_video; });
  const banner = $('#connectionBanner');
  if (status.error) { banner.textContent = status.error; banner.hidden = false; }
  else if (status.state === 'source_disconnected') { banner.textContent = 'Replay page disconnected. Open stream demo and press Resume to continue from the saved position. Your memory is intact.'; banner.hidden = false; }
  else if (offlineFailures === 0) banner.hidden = true;
  if (broadcast && status.has_video && sourceRun !== status.run_id) {
    sourceRun = status.run_id;
    sourceVideo.src = `${safeUrl(memory.server?.video_url) || '/video'}?run=${encodeURIComponent(status.run_id)}`;
    sourceVideo.load();
  }
  renderTranscript();
}

function renderMemory() {
  const frames = memory.frames || [];
  const records = memory.records || [];
  $('#sampleCount').textContent = `${frames.length} sampled frame${frames.length === 1 ? '' : 's'}`;
  $('#cubeEmpty').hidden = frames.length > 0 || Boolean(status?.has_video && (!demo || status.mode === 'replay') && status.processed_through_s > 0 && status.playback_s > 0);
  if (!frames.length) $('#cubeEmpty').innerHTML = `<span class="empty-orbit">${icon('cube')}</span><strong>${/fixture/i.test(status?.mode || '') ? 'Scripted memory, clearly marked.' : status?.has_video ? 'Your stream takes shape here.' : 'A new dimension for your stream.'}</strong><span>${/fixture/i.test(status?.mode || '') ? 'This fixture has no source frames.<br>Load your own recording to fill the cube.' : status?.has_video ? 'Start playback in the local replay tab.<br>Captured moments will fill this space.' : 'Load a recording and open local replay.<br>Captured moments will fill this space.'}</span>`;
  cube.update(frames, records, Math.max(0, Math.min(status?.playback_s ?? Infinity,
    status?.processed_through_s ?? Infinity, memory.through_s ?? memory.video?.duration_s ?? 0,
    status?.duration_s ?? Infinity)));
  let tags = $('#cubeCanvas').parentElement!.querySelector<HTMLDivElement>('.topic-tags');
  if (!tags) { tags = document.createElement('div'); tags.className = 'topic-tags'; $('#cubeCanvas').parentElement!.append(tags); }
  tags.innerHTML = cube.topics.map((topic, i) => `<span style="--topic-color:${['#b8f76c', '#9e8cff', '#66cce2', '#f8b86a', '#ec88b5'][i]}"><i></i>${escape(topic)}</span>`).join('');
  cube.highlight(answer?.citations || [], selected);
  renderTimeline();
  renderTranscript();
}

function renderTimeline() {
  const duration = Math.max(1, status?.duration_s || 1);
  const evidence = answer?.citations || [];
  const marks = evidence.length ? evidence.map((e, i) => ({ t: e.t, end: e.t_end || e.t + 2, title: e.label, type: 'match', index: i })) : memory.records.filter(r => r.modality === 'video' || r.kind === 'scene').slice(-100).map(r => ({ t: r.t_start, end: r.t_end, title: r.caption || r.text || 'Visual observation', type: 'observation', index: -1 }));
  $('#timelineMarkers').innerHTML = marks.map(m => `<button class="timeline-mark ${m.type} ${m.index === selected ? 'selected' : ''}" style="left:${m.t / duration * 100}%;width:${Math.max(.4, (m.end - m.t) / duration * 100)}%" title="${escape(`${time(m.t)} · ${m.title}`)}" data-time="${m.t}" data-index="${m.index}" aria-label="${escape(`Inspect ${m.title} at ${time(m.t)}`)}"></button>`).join('');
}

function renderTranscript() {
  const point = inspection ?? status?.playback_s ?? 0;
  let records = memory.records.filter(r => r.modality === 'transcript' || r.kind === 'transcript');
  records = records.filter(r => r.t_start <= point + 1).slice(-3);
  const signature = records.map(r => `${r.t_start}${r.text}`).join('|');
  if ($('#transcript').dataset.signature === signature) return;
  $('#transcript').dataset.signature = signature;
  $('#transcript').innerHTML = records.length ? records.map(r => `<button class="transcript-row" data-time="${r.t_start}"><span class="mono">${time(r.t_start)}</span><span>${r.speaker && r.speaker !== 'unknown' ? `<b>${escape(r.speaker)}</b> ` : ''}${escape(r.text || '')}</span>${icon('arrow')}</button>`).join('') : `<p class="quiet-empty">${(status?.audio_through_s || 0) > 0 ? 'Audio processed, but no speech was recognized in this interval. Recognition can miss quiet or noisy speech.' : 'Speech transcription starts as the replay plays.'}</p>`;
}

function renderAnswer() {
  $('#clearAnswer').hidden = !answer;
  if (!answer) {
    $('#answerContent').innerHTML = `<div class="answer-empty">${icon('search')}<strong>A question opens a moment.</strong><span>Your answer and its source evidence<br>will appear here.</span></div>`;
    cube.highlight([], 0);
    return;
  }
  const citations = answer.citations || [];
  const answerStatus = answer.status || (citations.length ? 'grounded' : 'no evidence');
  $('#answerContent').innerHTML = `<div class="answer-meta"><span class="answer-status ${citations.length ? 'supported' : ''}"><i></i>${escape(answerStatus.replaceAll('_', ' '))}</span><span class="mono">THROUGH ${time(answer.through_s ?? status?.processed_through_s)}</span></div><p class="answer-summary">${escape(answer.summary)}</p>${answer.source_label ? `<div class="source-label">${escape(answer.source_label)}</div>` : ''}<div class="evidence-heading"><span>${citations.length ? 'EXPLORE THE EVIDENCE' : 'NO SUPPORTING MOMENTS FOUND'}</span><span>${String(citations.length).padStart(2, '0')}</span></div><div class="evidence-list">${citations.map((c, i) => `<button class="evidence-card ${i === selected ? 'selected' : ''}" data-citation="${i}"><span class="evidence-number">${String(i + 1).padStart(2, '0')}</span><span class="evidence-card-body"><span class="evidence-card-top"><span class="evidence-type">${escape((c.source_type || 'source').replaceAll('_', ' '))}</span><span class="mono">${time(c.t)}${c.t_end && c.t_end > c.t + 1 ? ` – ${time(c.t_end)}` : ''}</span></span><strong>${escape(c.label)}</strong>${c.excerpt ? `<span class="evidence-excerpt">${escape(c.excerpt)}</span>` : ''}</span>${icon('arrow')}</button>`).join('')}</div>${citations.length ? '<div class="evidence-navigation"><button class="text-button" data-action="cube_prev">← Previous</button><span>Shared in the replay</span><button class="text-button" data-action="cube_next">Next →</button></div>' : ''}${answer.tool_trace?.length ? `<details class="trace"><summary>How this answer was found <span>+</span></summary><ol>${answer.tool_trace.map(item => `<li>${escape(typeof item === 'string' ? item : JSON.stringify(item))}</li>`).join('')}</ol></details>` : ''}`;
  cube.highlight(citations, selected);
  renderAnswerContext($('#answerContent'),answer,t=>inspect(t));
  if (answer.coverage_gaps?.length) {
    const note = document.createElement('p'); note.className='coverage-answer';
    note.textContent='Some intervals were not analyzed; this answer describes only the available moments.';
    $('#answerContent').prepend(note);
  }
  renderTimeline();
}

function inspect(t: number, citation?: Evidence) {
  if (demo && status?.mode !== 'replay') return;
  const through = status ? Math.max(0, Math.min(status.playback_s, status.processed_through_s, status.duration_s)) : 0;
  const cutoff = citation ? Math.min(through, answer?.through_s ?? through) : through;
  inspection = Math.max(0, Math.min(t, cutoff));
  evidenceLimit = citation ? Math.min(cutoff, citation.t_end && citation.t_end > citation.t ? citation.t_end : citation.t + 5) : cutoff;
  renderStatus();
  $('#evidencePanel').hidden = false;
  $('#saveClip').hidden = !citation || !status?.has_video;
  const img = $<HTMLImageElement>('#evidenceImage');
  const frame = citation?.frame_url || memory.frames.reduce<Frame | null>((best, frame) => !best || Math.abs(frame.t - t) < Math.abs(best.t - t) ? frame : best, null)?.url;
  $('#evidenceTime').textContent = time(inspection);
  $('#evidenceLabel').textContent = citation?.label || 'Source moment';
  if (status?.has_video) {
    evidenceVideo.hidden = false;
    img.hidden = true;
    $('#evidenceNoVideo').hidden = true;
    if (evidenceRun !== status.run_id) {
      evidenceRun = status.run_id;
      pendingEvidence = inspection;
      evidenceVideo.src = `${safeUrl(memory.server?.video_url) || '/video'}?run=${encodeURIComponent(status.run_id)}`;
      evidenceVideo.load();
    } else if (evidenceVideo.readyState >= 1) evidenceVideo.currentTime = inspection;
    else pendingEvidence = inspection;
  } else {
    evidenceVideo.hidden = true;
    img.hidden = !safeUrl(frame);
    if (safeUrl(frame)) img.src = safeUrl(frame);
    $('#evidenceNoVideo').hidden = Boolean(safeUrl(frame));
  }
}

async function selectCitation(index: number, share = true) {
  const citation = answer?.citations[index];
  if (!citation) return;
  selected = index;
  renderAnswer();
  inspect(citation.t, citation);
  if (share) await control('select', { index });
}

async function ask(question: string) {
  if (asking || !question.trim()) return null;
  asking = true;
  $<HTMLTextAreaElement>('#question').value = question;
  $<HTMLButtonElement>('#askSubmit').disabled = true;
  $('#askSubmit').innerHTML = '<span class="spinner"></span>';
  $('#askHint').textContent = 'Searching the captured memory…';
  $('#answerPanel').classList.add('is-searching');
  try {
    const result = await request<Answer>('/api/ask', { question: question.trim(), now: status?.playback_s || 0, conversation_id:sourceId });
    await pollStatus();
    if (result.run_id && result.run_id !== status?.run_id) throw new Error('The recording changed while searching. Ask again for the current recording.');
    answer = { ...result, citations: result.citations || [] };
    selected = 0;
    renderAnswer();
    if (answer.citations.length) await selectCitation(0, false);
    await pollShared();
    return result;
  } catch (error) {
    toast(error instanceof Error ? error.message : 'The local model could not answer.');
    $('#askHint').textContent = 'Search could not complete. Your recording is safe.';
    return null;
  } finally {
    asking = false;
    $('#answerPanel').classList.remove('is-searching');
    $<HTMLButtonElement>('#askSubmit').disabled = false;
    $('#askSubmit').innerHTML = icon('arrow');
    $('#askHint').textContent = 'Answers grounded in captured moments';
  }
}

async function pollStatus() {
  try {
    const next = await request<Status>('/api/status');
    changeRun(next.run_id);
    status = next;
    offlineFailures = 0;
    renderStatus();
  } catch {
    offlineFailures++;
    $('#sourceState').innerHTML = '<i></i>Studio offline';
    $('#connectionBanner').textContent = 'The local studio is not responding. Start the Recall server; this page will reconnect automatically.';
    $('#connectionBanner').hidden = false;
  }
}

async function pollMemory() {
  try {
    const next = await request<Memory>('/api/memory');
    if (next.run_id && status && next.run_id !== status.run_id) return;
    const waiting = demo && status?.mode !== 'replay';
    memory = { ...next, records: waiting ? [] : next.records || [], frames: waiting ? [] : (next.frames || []).map((f, i) => ({ ...f, id: f.id || String(i), url: safeUrl(f.url) })).filter(f => f.url) };
    renderMemory();
  } catch { /* Status polling owns the visible connection state. */ }
}

async function pollShared() {
  try {
    const next = await request<Shared>('/api/shared');
    if (status && next.run_id !== status.run_id) return;
    if (demo && status?.mode !== 'replay') { answer = null; renderAnswer(); return; }
    shared = next;
    cube.setRotation(Number(next.rotation) || 0);
    $<HTMLInputElement>('#audienceControls').checked = next.controls_enabled;
    const key = JSON.stringify([next.answer, next.selection]);
    if (key !== lastAnswerKey) {
      lastAnswerKey = key;
      answer = next.answer ? { ...next.answer, citations: next.answer.citations || [] } : null;
      selected = next.selection || 0;
      renderAnswer();
      if (answer?.citations[selected]) inspect(answer.citations[selected].t, answer.citations[selected]);
    }
    if (broadcast && next.source_revision !== undefined && next.source_revision > lastSourceRevision && status?.has_video) {
      lastSourceRevision = next.source_revision;
      if (!sourceEngaged) return;
      if (next.source_command) diagnostics.event('browser_source_command',{action:next.source_command,source_revision:next.source_revision});
      if (next.source_command === 'play') void startSource();
      if (next.source_command === 'pause') sourceVideo.pause();
      if (next.source_command === 'restart') { sourceVideo.currentTime = 0; lastFrameBucket = -1; void startSource(); }
    }
  } catch { /* Status polling owns the visible connection state. */ }
}

async function startSource() {
  diagnostics.event('browser_control',{action:'play'});
  try {
    if (status?.mode !== 'replay') {
      await request('/api/control', { action: 'play' });
      await pollStatus();
    }
    await sourceVideo.play(); $('#startBroadcast').hidden = true;
  }
  catch (error) { diagnostics.event('browser_start_error',{error_type:error instanceof DOMException ? error.name : 'Error'}); $('#startBroadcast').hidden = false; toast('Press Start local replay in this tab to allow playback.'); }
}

async function sourceHeartbeat() {
  if (!broadcast || !sourceEngaged || sourceBusy || !status?.has_video || sourceRun !== status.run_id || sourceVideo.readyState < 2) return;
  sourceBusy = true;
  const heartbeatStarted = performance.now();
  diagnostics.event('browser_heartbeat_start',{},5000);
  try {
    const position = sourceVideo.currentTime;
    const payload: Record<string, unknown> = { run_id: status.run_id, source_id: sourceId, time_s: position, playing: !sourceVideo.paused && !sourceVideo.ended, ended: sourceVideo.ended };
    const bucket = Math.floor(position / (status?.pipeline?.frame_interval_s || 5));
    let captured = false;
    if ((bucket !== lastFrameBucket || (sourceVideo.ended && sourceVideo.currentTime > lastFrameTime + .1)) && !sourceVideo.seeking && sourceVideo.videoWidth) {
      captureCanvas.width = Math.min(status?.pipeline?.frame_width || 960, sourceVideo.videoWidth);
      captureCanvas.height = Math.round(captureCanvas.width * sourceVideo.videoHeight / sourceVideo.videoWidth);
      captureCanvas.getContext('2d')!.drawImage(sourceVideo, 0, 0, captureCanvas.width, captureCanvas.height);
      payload.frame = { t: position, data: captureCanvas.toDataURL('image/jpeg', .76).split(',')[1] };
      captured = true;
    }
    await request('/api/source', payload);
    diagnostics.event('browser_heartbeat_complete',{duration_s:(performance.now()-heartbeatStarted)/1000,captured},5000);
    if (captured) { lastFrameBucket = bucket; lastFrameTime = position; $('#sourceCapture').textContent = `FRAME CAPTURED ${time(position)}`; }
  } catch (error) {
    diagnostics.event('browser_heartbeat_error',{duration_s:(performance.now()-heartbeatStarted)/1000,error_type:error instanceof DOMException ? error.name : 'Error',http_status:Number((error as {http_status?:number})?.http_status)||0},1000);
    $('#sourceCapture').textContent = error instanceof Error ? error.message : 'Source connection lost';
    if (error instanceof Error && /another broadcast/i.test(error.message)) sourceVideo.pause();
  } finally { sourceBusy = false; }
}

$('#askForm').addEventListener('submit', e => { e.preventDefault(); void ask($<HTMLTextAreaElement>('#question').value); });
$('#demoChatForm').addEventListener('submit', async e => {
  e.preventDefault();
  const input = $<HTMLInputElement>('#demoInput');
  const message = input.value.trim();
  if (!message) return;
  const messages = $('#demoMessages');
  const line = document.createElement('p');
  line.innerHTML = `<strong>You</strong> ${escape(message)}`;
  messages.append(line);
  input.value = '';
  const match = message.match(/^\/(?:recall|ask)\s+(.+)$/i);
  while (messages.children.length > 100) messages.firstElementChild?.remove();
  if (!match) { messages.scrollTop = messages.scrollHeight; return; }
  if (status?.mode !== 'replay') {
    const note = document.createElement('p');
    note.innerHTML = '<strong>Recall</strong> Start the local replay first. Memory begins empty and builds as it plays.';
    messages.append(note);
    return;
  }
  const submit = $<HTMLButtonElement>('#demoChatForm button');
  submit.disabled = true;
  const reply = document.createElement('p');
  reply.innerHTML = '<strong>Recall</strong> Let me catch up…';
  messages.append(reply);
  messages.scrollTop = messages.scrollHeight;
  try {
    const result = await ask(match[1]);
    const matches = (result?.citations || []).slice(0, 3);
    if (!matches.length) {
      reply.innerHTML = `<strong>Recall</strong> ${escape(result?.summary || 'I can’t tell from the part processed so far. Try asking about a specific moment.')}`;
    } else {
      const links: string[] = [];
      for (const citation of matches) {
        try {
          const moment = await request<{ url: string; start_s: number }>('/api/moments', { run_id: result!.run_id, evidence_id: citation.evidence_id });
          const url = safeUrl(moment.url);
          if (url) links.push(`<li><a href="${escape(url)}" target="_blank" rel="noopener">${time(citation.t)} · ${escape((citation.excerpt || citation.label).slice(0, 95))} ↗</a></li>`);
        } catch { /* A replay change makes old evidence links unavailable. */ }
      }
      const summary = conciseAnswer(result?.summary);
      reply.innerHTML = links.length
        ? `<strong>Recall</strong> <span class="chat-answer">${escape(summary || 'View the supporting evidence below.')}</span>${links.length > 1 ? 'Possible matching moments:' : ''}<ol class="chat-matches">${links.join('')}</ol>`
        : '<strong>Recall</strong> This replay changed. Ask again for a fresh link.';
      if (result?.coverage_gaps?.length) {
        const note=document.createElement('small'); note.textContent='Some intervals were not analyzed.'; reply.append(note);
      }
    }
  } finally {
    submit.disabled = false;
    messages.scrollTop = messages.scrollHeight;
  }
});
$('#guideExample').addEventListener('click', () => { const question = $<HTMLTextAreaElement>('#question'); question.value = 'What happened in the last minute?'; question.focus(); question.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' }); });
$('#question').addEventListener('keydown', e => {
  if ((e as KeyboardEvent).key === 'Enter' && !(e as KeyboardEvent).shiftKey) { e.preventDefault(); void ask($<HTMLTextAreaElement>('#question').value); }
});
$('#suggestions').addEventListener('click', e => { const button = (e.target as Element).closest<HTMLButtonElement>('button[data-question]'); if (button) void ask(button.dataset.question!); });
document.addEventListener('click', e => {
  const element = e.target as Element;
  const action = element.closest<HTMLButtonElement>('[data-action]');
  if (action) { if (broadcast && action.dataset.action === 'play') void startSource(); else void control(action.dataset.action!); }
  const citation = element.closest<HTMLButtonElement>('[data-citation]');
  if (citation) void selectCitation(Number(citation.dataset.citation));
  const moment = element.closest<HTMLButtonElement>('[data-time]');
  if (moment) {
    const index = Number(moment.dataset.index ?? -1);
    if (index >= 0) void selectCitation(index); else inspect(Number(moment.dataset.time));
  }
});
$('#clearAnswer').addEventListener('click', () => { answer = null; inspection = null; $('#evidencePanel').hidden = true; evidenceVideo.pause(); renderAnswer(); void control('clear_answer'); });
$('#zoomIn').addEventListener('click', () => cube.zoom(1));
$('#zoomOut').addEventListener('click', () => cube.zoom(-1));
$('#resetView').addEventListener('click', () => { cube.reset(); inspection = null; renderStatus(); });
$('#timeline').addEventListener('input', e => { inspection = Math.min(Number((e.target as HTMLInputElement).value), status?.processed_through_s || 0, status?.playback_s || 0); renderStatus(); });
$('#timeline').addEventListener('change', e => inspect(Number((e.target as HTMLInputElement).value)));
$('#audienceControls').addEventListener('change', e => { void control('controls', { enabled: (e.target as HTMLInputElement).checked }); });
$('#startBroadcast').addEventListener('click', () => { void startSource(); });
$('#saveClip').addEventListener('click', async () => {
  const citation = answer?.citations[selected];
  if (!status || !citation) return;
  const cutoff = Math.min(status.playback_s, status.processed_through_s, status.duration_s, answer?.through_s ?? Infinity);
  const start = Math.max(0, citation.t);
  const end = Math.min(cutoff, citation.t_end && citation.t_end > start ? citation.t_end : start + 5, start + 60);
  if (end <= start) { toast('This evidence is not yet available as a clip.'); return; }
  const button = $<HTMLButtonElement>('#saveClip');
  button.disabled = true;
  button.textContent = 'Preparing clip…';
  try {
    const response = await fetch('/api/clip', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Tube-Token': status.csrf_token }, body: JSON.stringify({ run_id: status.run_id, start_s: start, end_s: end }), signal: AbortSignal.timeout(180000) });
    if (!response.ok) { const result = await response.json(); throw new Error(result.error || 'Could not prepare this clip.'); }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = `recall-evidence-${Math.floor(start)}s.mp4`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast('Evidence clip saved to your downloads.');
  } catch (error) { toast(error instanceof Error ? error.message : 'Could not prepare this clip.'); }
  finally { button.disabled = false; button.textContent = 'Save selected evidence clip'; }
});
sourceVideo.addEventListener('loadedmetadata', () => { if (status?.mode === 'replay' && status.playback_s > 0 && Number.isFinite(sourceVideo.duration)) sourceVideo.currentTime = Math.min(status.playback_s, sourceVideo.duration); });
sourceVideo.addEventListener('play', () => { sourceEngaged = true; $('#startBroadcast').hidden = true; void sourceHeartbeat(); });
sourceVideo.addEventListener('pause', () => { $('#startBroadcast').hidden = false; void sourceHeartbeat(); });
sourceVideo.addEventListener('seeked', () => { void sourceHeartbeat(); });
sourceVideo.addEventListener('ended', () => { void sourceHeartbeat(); });
evidenceVideo.addEventListener('loadedmetadata', () => { if (pendingEvidence !== null) { evidenceVideo.currentTime = pendingEvidence; pendingEvidence = null; } });
function constrainEvidence() {
  const limit = Math.min(evidenceLimit, status?.processed_through_s || 0, status?.playback_s || 0);
  if (evidenceVideo.currentTime > limit + .01) { evidenceVideo.pause(); evidenceVideo.currentTime = limit; }
  if (evidenceVideo.currentTime >= limit) evidenceVideo.pause();
}
evidenceVideo.addEventListener('seeking', constrainEvidence);
evidenceVideo.addEventListener('timeupdate', () => { constrainEvidence(); if (!evidenceVideo.paused) { inspection = evidenceVideo.currentTime; cube.setTime(inspection); $('#inspectionClock').textContent = time(inspection); } });
$('#uploadButton').addEventListener('click', () => $<HTMLInputElement>('#fileInput').click());
$('#fileInput').addEventListener('change', async e => {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  if (file.size > 8 * 1024 * 1024 * 1024) { toast('Choose a recording no larger than 8 GiB.'); (e.target as HTMLInputElement).value = ''; return; }
  const button = $<HTMLButtonElement>('#uploadButton');
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span>Loading locally…';
  try { await uploadRecording(file, message => { button.textContent = message; }); await pollStatus(); await pollMemory(); await pollShared(); toast('Recording loaded. Open stream demo and press Start to begin.'); }
  catch (error) { toast(error instanceof Error ? error.message : 'Could not load this recording.'); }
  finally { button.disabled = false; button.innerHTML = `${icon('upload')}Load recording`; (e.target as HTMLInputElement).value = ''; }
});

let statusPolling = false;
let memoryPolling = false;
async function tick() {
  if (statusPolling) return;
  statusPolling = true;
  try { await pollStatus(); await pollShared(); } finally { statusPolling = false; }
}
async function memoryTick() {
  if (memoryPolling) return;
  memoryPolling = true;
  try { await pollMemory(); } finally { memoryPolling = false; }
}
void tick().then(memoryTick);
window.setInterval(() => { void tick(); }, 1000);
window.setInterval(() => { void memoryTick(); }, 2000);
if (broadcast) window.setInterval(() => { void sourceHeartbeat(); }, 500);
