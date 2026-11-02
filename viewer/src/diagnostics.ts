import './diagnostics.css';
import type { Status } from './types';

type Field = string | number | boolean | null;
type Data = Record<string, unknown>;
interface DiagnosticReport { status?: Partial<Status>; hardware?: Data; events?: Data[]; logging?: Data }
interface Options {
  video: HTMLVideoElement;
  role: 'studio' | 'demo' | 'broadcast';
  sourceId: string;
  status: () => Status | null;
  engaged: () => boolean;
  busy: () => boolean;
}

const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : null;
const seconds = (value: unknown) => { const n = number(value); return n === null ? 'Unavailable' : `${n.toFixed(1)} s`; };
const clock = (value: unknown) => { const n = number(value); return n === null ? 'Unavailable' : `${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`; };
const object = (value: unknown): Data => value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {};
const gb = (value: unknown) => { const n = number(value); return n === null ? 'Unavailable' : `${(n/1024).toFixed(1)} GB`; };

export function installDiagnostics(options: Options) {
  const panel = document.createElement('details');
  panel.id = 'diagnosticsPanel';
  panel.className = 'diagnostics-panel';
  panel.innerHTML = `<summary><span>Diagnostics <small>Playback &amp; processing</small></span><span id="diagnosticsSummary">Waiting for status…</span></summary><div class="diagnostics-body"><p id="diagnosticsExplanation">Check whether playback stopped or processing is behind. Logs stay on this computer.</p><div id="diagnosticsMetrics" class="diagnostics-metrics"></div><div class="diagnostics-actions"><button class="button" id="refreshDiagnostics" type="button">Refresh diagnostics</button><a class="button" id="downloadDiagnostics" href="/api/diagnostics/download" download="recall-debug-report.json">Download debug report</a><span id="diagnosticsUpdated" role="status"></span></div><p class="diagnostics-note">Records timing, playback state, queues and errors. Chat messages, transcripts, images and access tokens are excluded.</p><details class="diagnostics-events"><summary>Recent events</summary><ol id="diagnosticsEvents"><li>Open Diagnostics to load recent events.</li></ol></details></div>`;
  document.querySelector('.workspace')!.before(panel);
  const find = <T extends HTMLElement>(selector: string) => panel.querySelector<T>(selector)!;
  let refreshing = false;
  let lastSent = new Map<string, number>();
  let pending = 0;

  function videoFields(): Record<string, Field> {
    const video = options.video;
    let bufferedEnd: number | null = null;
    try { if (video.buffered.length) bufferedEnd = video.buffered.end(video.buffered.length - 1); } catch { /* A source swap can clear ranges. */ }
    return {
      page_role: options.role, source_id: options.sourceId,
      position_s: Number.isFinite(video.currentTime) ? video.currentTime : 0,
      paused: video.paused, ended: video.ended, seeking: video.seeking,
      ready_state: video.readyState, network_state: video.networkState,
      buffered_end_s: bufferedEnd, playback_rate: video.playbackRate,
      visibility: document.visibilityState === 'hidden' ? 'hidden' : 'visible',
      source_engaged: options.engaged(), source_busy: options.busy(), error_code: video.error?.code ?? 0,
    };
  }

  function event(name: string, fields: Record<string, Field> = {}, throttleMs = 0, keepalive = false) {
    const status = options.status();
    if (!status?.csrf_token || pending >= 4) return;
    const now = performance.now();
    if (now - (lastSent.get(name) ?? -Infinity) < throttleMs) return;
    lastSent.set(name, now);
    // Only explicit structured fields cross this boundary; never pass errors,
    // request bodies, URLs, or arbitrary application objects into the logger.
    const body = JSON.stringify({run_id:status.run_id,event:name,...videoFields(),...fields});
    pending++;
    void fetch('/api/diagnostics/event', {
      method:'POST',headers:{'Content-Type':'application/json','X-Tube-Token':status.csrf_token},
      body,keepalive,signal:AbortSignal.timeout(5000),
    }).catch(() => { /* Diagnostics must never interrupt playback or recursively log itself. */ }).finally(() => pending--);
  }

  function update(status: Partial<Status> | null = options.status()) {
    if (!status) return;
    const lag = Math.max(0,(status.playback_s ?? 0)-(status.processed_through_s ?? 0));
    const state = String(status.state || 'unknown').replaceAll('_',' ');
    find('#diagnosticsSummary').textContent = `${state} · ${lag.toFixed(1)} s processing lag`;
    let explanation = 'Playback and processing are separate. A growing queue with long model timings indicates processing pressure.';
    if (status.gap) explanation = `Processing is paused. Questions can still use memory through ${clock(status.processed_through_s)}. Retry the missing work, or use Resume from playhead to keep earlier memory and mark the skipped interval.`;
    else if (status.state === 'paused') explanation = 'The source player is paused. Processing can finish captured work while playback stays still. Check recent browser events to see what preceded the pause.';
    else if (status.state === 'source_disconnected') explanation = 'The replay page stopped sending its clock. Pending processing is suspended. Reopen the replay to continue; any forward jump will be marked as missing analysis.';
    else if (lag > 30) explanation = status.pipeline?.catch_up==='live' ? 'Live mode drops stale video samples. Speech runs separately. Pause to catch up, or resume from the playhead and mark skipped analysis.' : 'Processing is catching up. Pause to analyze all pending work, or resume from the playhead and mark skipped analysis.';
    find('#diagnosticsExplanation').textContent = explanation;
  }

  function render(report: DiagnosticReport) {
    const status = report.status || options.status() || {};
    update(status);
    const metrics = status.metrics || {};
    const active = status.active_jobs ?? (status.active_job ? [status.active_job] : []);
    const hardware = object(report.hardware);
    const ram = object(hardware.memory || hardware.ram);
    const gpuStatus = object(hardware.gpu);
    const gpu = Array.isArray(gpuStatus.devices) ? object(gpuStatus.devices[0]) : {};
    const cpu = object(hardware.cpu_power);
    const cells: Array<[string,string]> = [
      ['Playback',clock(status.playback_s)], ['Processed through',clock(status.processed_through_s)],
      ['Source connection',status.source_connected === undefined ? 'Unavailable' : status.source_connected ? 'Connected' : 'Disconnected'],
      ['Last source heartbeat',seconds(status.source_age_s)],
      ['Queued processing',status.queue_depth === undefined ? 'Unavailable' : String(status.queue_depth)],
      ['Waiting questions',status.questions_waiting === undefined ? 'Unavailable' : String(status.questions_waiting)],
      ['Active work',active.length ? active.map(job => `${job.kind} · ${seconds(job.age_s)}`).join(' + ') : 'None'],
      ['Last frame analysis',seconds(metrics.last_vision_s)], ['Last speech window',seconds(metrics.last_audio_s)],
      ['Last answer',seconds(metrics.last_answer_s)],
      ['Available RAM',gb(ram.available_mb ?? ram.free_mb ?? hardware.ram_available_mb)],
      ['GPU load',number(gpu.utilization_pct) === null ? 'Unavailable' : `${gpu.utilization_pct}%`],
      ['GPU memory',number(gpu.memory_used_mb) === null ? 'Unavailable' : `${gb(gpu.memory_used_mb)} / ${gb(gpu.memory_total_mb)}`],
      ['GPU clock',number(gpu.clock_mhz)===null ? 'Unavailable' : `${gpu.clock_mhz} / ${gpu.max_clock_mhz ?? '?'} MHz`],
      ['GPU power',number(gpu.power_w)===null ? 'Unavailable' : `${gpu.power_w} W · limit ${gpu.power_limit_w ?? 'unavailable'}`],
      ['GPU clock-limit flags',number(gpu.clock_reasons)===null ? 'Unavailable' : `0x${Number(gpu.clock_reasons).toString(16)}`],
      ['CPU load',number(cpu.utilization_pct)===null ? 'Unavailable' : `${cpu.utilization_pct}%`],
      ['Power source',cpu.ac_connected===true ? 'Plugged in' : cpu.ac_connected===false ? 'Battery' : 'Unavailable'],
      ['Speech / video queue',`${status.audio_queue_depth ?? '?'} / ${status.vision_queue_depth ?? '?'}`],
      ['Hardware sample age',seconds(hardware.sample_age_s)],
    ];
    find('#diagnosticsMetrics').innerHTML = cells.map(([label,value]) => `<div><span>${escape(label)}</span><strong>${escape(value)}</strong></div>`).join('');
    const events = (report.events || []).slice(-15).reverse();
    find('#diagnosticsEvents').innerHTML = events.length ? events.map(row => {
      const values = ['kind','action','state','outcome','reason','position_s','elapsed_s','duration_s','queue_depth','error_type','http_status'].filter(key => row[key] !== undefined).map(key=>`${key}: ${row[key]}`).join(' · ');
      return `<li><time>${escape(row.time || row.timestamp || row.utc || '')}</time><strong>${escape(row.event || 'event')}</strong><span>${escape(values)}</span></li>`;
    }).join('') : '<li>No events have been recorded yet.</li>';
    find('#diagnosticsUpdated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
  }

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    const button = find<HTMLButtonElement>('#refreshDiagnostics');
    button.disabled = true;
    try {
      const response = await fetch('/api/diagnostics',{cache:'no-store',signal:AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('unavailable');
      render(await response.json());
    } catch { find('#diagnosticsUpdated').textContent = 'Diagnostics unavailable. The server may need restarting after an update.'; }
    finally { refreshing = false; button.disabled = false; }
  }
  panel.addEventListener('toggle',() => { if (panel.open) void refresh(); });
  find('#refreshDiagnostics').addEventListener('click',() => void refresh());
  window.setInterval(() => { if (panel.open) void refresh(); },5000);
  window.addEventListener('error',() => event('browser_javascript_error',{error_type:'Error'},1000));
  window.addEventListener('unhandledrejection',() => event('browser_unhandled_rejection',{error_type:'Error'},1000));
  if (options.role !== 'studio') {
    for (const name of ['play','playing','pause','waiting','stalled','seeking','seeked','ended','error','abort','loadedmetadata','ratechange']) {
      options.video.addEventListener(name,() => event(`browser_${name}`,{},500));
    }
    document.addEventListener('visibilitychange',() => event('browser_visibility',{},0,true));
    window.addEventListener('pagehide',() => event('browser_pagehide',{},0,true));
    window.setInterval(() => event('browser_snapshot',{},4500),5000);
  }
  return {event,update,refresh};
}
