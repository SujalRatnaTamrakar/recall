import './moment.css';

interface Moment { id: string; run_id: string; start_s: number; end_s: number; evidence_s: number; title: string; excerpt: string; source_type: string; video_url: string }
const $ = <T extends HTMLElement = HTMLElement>(s: string) => document.querySelector<T>(s)!;
const clock = (n: number) => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
document.title = 'Recall — Watch a moment';
document.body.className = 'moment-page';
$('#app').innerHTML = `<header class="moment-header"><a href="/?view=demo" target="recall-replay">← Back to stream demo</a><span>Recall · local moment</span></header><main class="moment-main"><p class="eyebrow">FROM THE STREAM</p><h1 id="momentTitle">Loading your moment…</h1><p id="momentRange"></p><video id="momentVideo" controls playsinline preload="metadata" hidden></video><p id="momentExcerpt"></p><section class="moment-editor" hidden><h2>Create a clip</h2><p>Trim inside this moment. Times below are seconds from the start of the recording.</p><div class="moment-trim"><label>Start <input id="clipStart" type="number" step="0.1" /></label><label>End <input id="clipEnd" type="number" step="0.1" /></label><button id="previewClip" class="button">Preview selection</button></div><div class="moment-actions"><button id="downloadClip" class="button load-button">Download MP4</button><button id="shareClip" class="button">Share clip</button><button id="copyMoment" class="button">Copy local link</button></div><p class="moment-local-note">This link works on this computer while this replay is open. Download the MP4 to share elsewhere. Links expire when the replay restarts.</p></section><p id="momentStatus" role="status" aria-live="polite">Loading…</p></main>`;
let moment: Moment | null = null;
let token = '';
let limitStart = 0, limitEnd = 0;
const video = $<HTMLVideoElement>('#momentVideo');
const tell = (s: string) => { $('#momentStatus').textContent = s; };
const expire = (s: string) => { video.pause(); video.removeAttribute('src'); video.load(); video.hidden = true; $('.moment-editor').hidden = true; moment = null; tell(s); };
async function json(path: string) { const response = await fetch(path, { cache: 'no-store' }); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Moment unavailable.'); return data; }
function selection() { const start = Number($<HTMLInputElement>('#clipStart').value), end = Number($<HTMLInputElement>('#clipEnd').value); if (!moment || !Number.isFinite(start) || !Number.isFinite(end) || start < moment.start_s - 0.001 || end > moment.end_s + 0.001 || end <= start || end-start > 60) throw new Error('Choose a start and end inside the displayed moment, up to 60 seconds.'); return { start_s: start, end_s: end }; }
function constrain() { if (!moment) return; if (video.currentTime < limitStart - .05 || video.currentTime > limitEnd + .05) { video.pause(); video.currentTime = Math.max(limitStart, Math.min(limitEnd, video.currentTime)); } if (video.currentTime >= limitEnd) video.pause(); }
video.addEventListener('seeking', constrain); video.addEventListener('timeupdate', constrain);
$('#previewClip').addEventListener('click', () => { try { const range = selection(); limitStart = range.start_s; limitEnd = range.end_s; video.currentTime = limitStart; void video.play().catch(() => tell('Press Play on the video to preview.')); } catch (e) { tell((e as Error).message); } });
async function exportClip(share: boolean) {
  if (!moment) return;
  const current = moment;
  const buttons = ['#downloadClip', '#shareClip'].map(s => $<HTMLButtonElement>(s));
  try {
    const range = selection(); buttons.forEach(b => b.disabled = true); tell('Preparing your clip…');
    const response = await fetch('/api/clip', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Tube-Token': token }, body: JSON.stringify({run_id:current.run_id,moment_id:current.id,...range}), signal:AbortSignal.timeout(180000) });
    if (!response.ok) throw new Error((await response.json()).error || 'Could not create this clip.');
    const file = new File([await response.blob()], `recall-${Math.floor(range.start_s)}s.mp4`, {type:'video/mp4'});
    if (share && navigator.canShare?.({files:[file]})) {
      try { await navigator.share({files:[file],title:'Recall clip'}); tell('Clip handed to your share dialog.'); return; }
      catch (e) { if ((e as Error).name === 'AbortError') { tell('Sharing canceled. You can still download this clip.'); return; } }
    }
    const url=URL.createObjectURL(file);const link=document.createElement('a');link.href=url;link.download=file.name;link.click();setTimeout(()=>URL.revokeObjectURL(url),60000);tell(share ? 'The browser could not open file sharing. The clip was downloaded so you can share it.' : 'Clip download started.');
  } catch (e) { tell((e as Error).message); } finally { buttons.forEach(b => b.disabled = false); }
}
$('#downloadClip').addEventListener('click',()=>void exportClip(false)); $('#shareClip').addEventListener('click',()=>void exportClip(true));
$('#copyMoment').addEventListener('click',()=>void navigator.clipboard.writeText(location.href).then(()=>tell('Local link copied. It works only on this computer and expires on replay restart.')).catch(()=>tell('Copy the address from your browser to share this local moment.')));
async function load() {
  try {
    const id=new URLSearchParams(location.search).get('id'); if(!id) throw new Error('This link is missing its moment. Ask in chat to get a new one.');
    const data=await json(`/api/moments/${encodeURIComponent(id)}`) as Moment; const status=await json('/api/status');
    if(status.run_id!==data.run_id) throw new Error('This moment expired. Ask again in the current replay.');
    moment=data;token=status.csrf_token;limitStart=data.start_s;limitEnd=data.end_s;
    $('#momentTitle').textContent=data.title;$('#momentRange').textContent=`${clock(data.start_s)} – ${clock(data.end_s)} · up to one minute around the match`;
    $('#momentExcerpt').textContent=`${data.source_type === 'spoken' ? 'Automatic transcript' : 'Source evidence'}: ${data.excerpt}`;
    for(const selector of ['#clipStart','#clipEnd']) {const input=$<HTMLInputElement>(selector);input.min=String(data.start_s);input.max=String(data.end_s);}
    $<HTMLInputElement>('#clipStart').value=String(data.start_s);$<HTMLInputElement>('#clipEnd').value=String(data.end_s);
    $('.moment-editor').hidden=false;video.hidden=false;
    video.addEventListener('loadedmetadata',()=>{video.currentTime=data.start_s;void video.play().then(()=>tell('Playing your moment.')).catch(()=>tell('Press Play to watch this moment.'));},{once:true});
    video.src=`/video?run=${encodeURIComponent(data.run_id)}`;
    window.setInterval(()=>void json('/api/status').then(s=>{if(moment && (s.run_id!==moment.run_id || s.processed_through_s < moment.end_s))expire('This moment expired after a replay change. Ask again for a new link.');}).catch(()=>expire('The local server is unavailable. Reopen this link when it is running.')),1500);
  } catch(e) { expire((e as Error).message); }
}
void load();
