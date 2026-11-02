import './review.css';
import { perspectiveNav } from './perspectives';
import { renderAnswerContext } from './context';
import type { Answer, Evidence, Memory, Status } from './types';

type Role = 'moderator' | 'campaign' | 'editor';
type Source = { evidence_id: string; t_start: number; t_end: number; excerpt: string; source_type: string };
type Item = { id: string; title: string; notes: string; requirement: string; status: string; sources: Source[]; through_s: number };
type Board = { run_id: string; revision: number; role: Role; title: string; brief: {name: string; terms: string; checklist: string[]}; items: Item[]; through_s: number };
type Moment = {id: string; run_id: string; start_s: number; end_s: number; evidence_s: number; url: string};
const role = new URLSearchParams(location.search).get('view') as Role;
const config = {
  moderator: {name: 'Moderator', eyebrow: 'CONTEXT BEFORE A DECISION', title: 'See the moment in context.',
    subtitle: 'Find what was said, inspect what came before, and leave a review note.',
    brief: 'Review focus', nameLabel: 'Topic or concern', termLabel: 'Words or names to look for', checklistLabel: 'Questions to check · one per line',
    board: 'Review queue', save: 'Add to review queue', empty: 'Save a source moment to start a review. Your notes stay in this workspace.',
    presets: ['What was said in the last minute?', 'What changed in the last five minutes?', 'What did they say they would do next?'],
    states: {needs_review: 'Needs review', reviewed: 'Reviewed', follow_up: 'Follow up'},
    boundary: 'Review broadcast footage and transcripts. Decisions stay with you; this workspace does not take moderation actions or collect viewer chat.'},
  campaign: {name: 'Campaign', eyebrow: 'FROM A BRIEF TO SOURCE PROOF', title: 'Find the moments behind a campaign.',
    subtitle: 'Check brand mentions and planned talking points against the actual stream.',
    brief: 'Campaign brief', nameLabel: 'Sponsor or campaign', termLabel: 'Brand and product names', checklistLabel: 'Deliverables · one per line',
    board: 'Campaign proof board', save: 'Save campaign proof', empty: 'Add a brief, search for a deliverable, then save and review the supporting moment.',
    presets: ['Find brand mentions', 'Find a sponsorship disclosure', 'What did they say about the product?'],
    states: {needs_review: 'Needs review', confirmed: 'Confirmed by reviewer', follow_up: 'Follow up'},
    boundary: 'Counts refer to your saved reviews. Sampled footage cannot measure every appearance, exposure time, impressions, conversions, or compliance.'},
  editor: {name: 'Editor', eyebrow: 'KEEP THE SETUP. FIND THE PAYOFF.', title: 'Build a shortlist worth watching.',
    subtitle: 'Find a story beat, watch its surrounding footage, and take a clip into your edit.',
    brief: 'Edit brief', nameLabel: 'Working title', termLabel: 'Story, subject, or recurring names', checklistLabel: 'Moments to find · one per line',
    board: 'Edit shortlist', save: 'Add to shortlist', empty: 'Save the moments you want to revisit. Open a source clip to trim and download it.',
    presets: ['Summarize the whole stream so far.', 'What changed after the first attempt?', 'What did they plan earlier, and what happened next?'],
    states: {shortlisted: 'Shortlisted', selected: 'Selected for edit', rejected: 'Set aside'},
    boundary: 'Clips come from your local recording. Open a moment to trim up to one minute, download the MP4, or use your browser’s share dialog.'},
}[role];
const states = Object.fromEntries(Object.entries(config.states)) as Record<string, string>;
const $ = <T extends HTMLElement = HTMLElement>(s: string) => document.querySelector<T>(s)!;
const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]!));
const clock = (n: number) => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2,'0')}`;
const sourceLabel = (value: string) => value === 'spoken' ? 'Automatic transcript' : value === 'observed' ? 'Generated visual description' : 'Source record';
let status: Status | null = null, board: Board | null = null, memory: Memory | null = null;
let answer: Answer | null = null, question = '', requirement = '', moment: Moment | null = null;
let working = false, editing = false, mutating = false, generation = 0, previewVersion = 0;
let answerRequirement = '', briefDirty = false;
const drafts = new Map<string, {status: string; notes: string}>();
const conversation = `${role}-${crypto.randomUUID()}`;
document.title = `Recall — ${config.name}`;
document.body.className = `review-page review-${role}`;
$('#app').innerHTML = `<header class="review-header"><a class="review-brand" href="/" target="recall-studio"><span>Recall</span><small>${config.name}</small></a><span class="review-private">Local workspace · answers stay off the broadcast</span><a class="button" href="/" target="recall-studio">Open studio ↗</a></header>
${perspectiveNav(role)}
<main class="review-main"><section class="review-heading"><div><p class="eyebrow">${config.eyebrow}</p><h1>${config.title}</h1><p>${config.subtitle}</p></div><div class="review-session"><strong id="reviewRecording">Connecting…</strong><span id="reviewClock">Checking available memory</span><span id="reviewMode"></span></div></section>
<p id="reviewNotice" class="review-notice" role="status" aria-live="polite">Load a recording in Studio, then play it in Viewer or Local replay. Return here to review the memory as it grows.</p>
<p id="reviewCoverage" class="review-coverage" hidden></p>
<div class="review-layout"><section class="review-search-column">
<details class="review-brief review-panel" ${role === 'campaign' ? 'open' : ''}><summary>${config.brief}<span>Optional · stays outside the stream memory</span></summary><form id="briefForm"><div class="review-brief-fields"><label>${config.nameLabel}<input id="briefName" maxlength="120" placeholder="${role === 'campaign' ? 'e.g. Trail Flask launch' : 'Give this review a name'}" /></label><label>${config.termLabel}<input id="briefTerms" maxlength="400" placeholder="Names and phrases used in the recording" /></label></div><label>${config.checklistLabel}<textarea id="briefChecklist" rows="3" placeholder="${role === 'campaign' ? 'Explain the product\nShow it in use\nMention the sponsorship' : 'A question or moment you want to revisit'}"></textarea></label><button class="button" type="submit">Save brief</button><span id="briefState"></span></form></details>
<section class="review-panel review-search"><div class="review-section-title"><h2>Search the stream</h2><span>Speech + sampled visuals</span></div><form id="reviewAsk"><label for="reviewQuestion">What do you want to find?</label><textarea id="reviewQuestion" maxlength="1500" rows="3" placeholder="Ask about a name, statement, action, or earlier part of the stream…" required></textarea><div class="review-query-footer"><span>Searches all memory available when you ask.</span><button class="button review-primary" id="reviewSubmit" disabled>Ask Recall →</button></div></form><div class="review-presets">${config.presets.map((s,i)=>`<button class="button" data-preset="${i}">${s}</button>`).join('')}</div><div id="reviewChecklist"></div></section>
<section class="review-panel review-results" aria-live="polite"><div class="review-section-title"><h2 id="resultsHeading">Your source moments</h2><span id="resultCount"></span></div><div id="reviewResults"><div class="review-empty"><span>01 &nbsp; ASK &nbsp; → &nbsp; 02 &nbsp; WATCH &nbsp; → &nbsp; 03 &nbsp; SAVE</span><h3>${role === 'moderator' ? 'Get the surrounding story.' : role === 'campaign' ? 'Turn a claim into something you can check.' : 'Start with a story, not a timestamp.'}</h3><p>Ask a question above. Each result links back to the recording, with its transcript or visual description.</p></div></div></section>
<section class="review-panel review-inspector" id="reviewInspector" hidden><div class="review-section-title"><h2>Source preview</h2><button class="button" id="closePreview">Close</button></div><p id="previewRange"></p><video id="reviewVideo" controls playsinline preload="none"></video><div class="review-preview-footer"><span>This player does not move the broadcast.</span><a id="reviewClip" class="button" target="_blank" rel="noopener">Open clip editor ↗</a></div><h3>Nearby transcript</h3><div id="nearbyTranscript"></div></section>
</section><aside class="review-board-column"><section class="review-panel review-board"><div class="review-section-title"><div><p class="eyebrow">SAVED ON THIS COMPUTER</p><h2>${config.board}</h2></div><span class="review-board-count" id="boardCount">0</span></div><div class="review-board-tools"><label>Show <select id="boardFilter"><option value="all">All moments</option>${Object.entries(states).map(([key,value])=>`<option value="${key}">${value}</option>`).join('')}</select></label><button class="button" id="exportBoard" disabled>Export notes</button><button class="button" id="refreshBoard">Refresh</button></div><p id="boardSummary"></p><div id="reviewItems"><p class="review-empty-note">${config.empty}</p></div><p class="review-board-foot">Notes survive a page refresh. A new replay starts an empty board; previous boards remain in the run’s local files.</p></section><p class="review-boundary">${config.boundary}</p><p class="review-boundary">These are local perspectives, not separate accounts or Twitch permissions.</p></aside></div></main>`;
const video = $<HTMLVideoElement>('#reviewVideo');
function tell(message: string) { $('#reviewNotice').textContent = message; }
async function api(path: string, data?: object) {
  const response = await fetch(path, data ? {method:'POST',headers:{'Content-Type':'application/json','X-Tube-Token':status?.csrf_token || ''},body:JSON.stringify(data)} : {cache:'no-store'});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Could not complete that request.');
  return value;
}
function closePreview() { previewVersion++; moment = null; video.pause(); video.removeAttribute('src'); video.load(); $('#reviewInspector').hidden = true; }
function fillBrief() {
  if (!board) return;
  $<HTMLInputElement>('#briefName').value = board.brief.name;
  $<HTMLInputElement>('#briefTerms').value = board.brief.terms;
  $<HTMLTextAreaElement>('#briefChecklist').value = board.brief.checklist.join('\n');
  $('#briefState').textContent = board.revision ? 'Saved locally' : '';
}
async function loadBoard(keepDrafts=false) {
  const expected = status?.run_id;
  const value = await api(`/api/reviews?role=${role}`) as Board;
  if (expected !== status?.run_id || value.run_id !== expected) return;
  board = value;
  if(!keepDrafts){briefDirty=false;drafts.clear();}
  for(const id of drafts.keys())if(!board.items.some(i=>i.id===id))drafts.delete(id);
  editing=briefDirty||drafts.size>0;
  if(!briefDirty)fillBrief();
  renderBoard();
}
async function mutate(data: {action:string;id?:string;[key:string]:unknown}) {
  if (!board || !status || board.run_id !== status.run_id) throw new Error('Wait for the current replay to connect.');
  if(mutating)throw new Error('A save is already in progress.');
  const run = board.run_id;
  const controls=()=>document.querySelectorAll<HTMLInputElement|HTMLButtonElement|HTMLSelectElement|HTMLTextAreaElement>('#briefForm input,#briefForm textarea,#briefForm button,#reviewItems select,#reviewItems textarea,#reviewItems button,#refreshBoard');
  mutating=true;controls().forEach(c=>c.disabled=true);
  try {
    const value = await api('/api/reviews', {run_id:run,role,revision:board.revision,...data}) as Board;
    if (status.run_id !== run || value.run_id !== run) throw new Error('The replay changed. Open the current board.');
    if(data.id) drafts.delete(data.id);
    if(data.action==='brief') briefDirty=false;
    board = value; editing = briefDirty || drafts.size>0; renderBoard();
  } finally {mutating=false;controls().forEach(c=>c.disabled=false);}
}
function renderBoard() {
  if (!board) return;
  $('#boardCount').textContent = String(board.items.length);
  const reviewed = board.items.filter(i => ['reviewed','confirmed','selected'].includes(i.status)).length;
  $('#boardSummary').textContent = `${board.items.length} saved moments · ${reviewed} ${role === 'editor' ? 'selected' : 'reviewed by you'}`;
  $<HTMLButtonElement>('#exportBoard').disabled = false;
  const filter = $<HTMLSelectElement>('#boardFilter').value;
  const items = board.items.filter(i => filter === 'all' || i.status === filter);
  $('#reviewItems').innerHTML = items.length ? items.slice().reverse().map(item => `<article class="review-saved" data-item="${escape(item.id)}"><div class="review-saved-top"><span>${clock(item.sources[0].t_start)}</span><span>${escape(states[item.status])}</span></div><h3>${escape(item.title)}</h3>${item.requirement ? `<p class="review-requirement">Check: ${escape(item.requirement)}</p>` : ''}<p class="review-saved-excerpt">${escape(item.sources[0].excerpt)}</p><button class="button" data-watch="${escape(item.sources[0].evidence_id)}" data-cutoff="${item.through_s}">Watch context</button><label>Review status<select class="item-status">${Object.entries(states).map(([key,value])=>`<option value="${key}" ${key===item.status?'selected':''}>${value}</option>`).join('')}</select></label><label>Your notes<textarea class="item-notes" rows="2" maxlength="3000" placeholder="What did you observe? What needs a second look?">${escape(item.notes)}</textarea></label><div class="review-item-actions"><button class="button" data-save="${escape(item.id)}">Save review</button><button class="button review-remove" data-remove="${escape(item.id)}">Remove</button></div></article>`).join('') : `<p class="review-empty-note">${filter === 'all' ? config.empty : 'No moments in this review state.'}</p>`;
  $('#reviewChecklist').innerHTML = board.brief.checklist.length ? `<p>FROM YOUR BRIEF · SELECT TO SEARCH</p>${board.brief.checklist.map((line,index)=>{
    const done = board!.items.some(i=>i.requirement===line && ['confirmed','reviewed','selected'].includes(i.status));
    return `<button class="review-check" data-check="${index}"><span>${done?'✓':'○'}</span>${escape(line)}<small>${done?'Reviewed by you':'Find supporting moments'}</small></button>`;
  }).join('')}` : '';
  for(const card of document.querySelectorAll<HTMLElement>('[data-item]')) {
    const draft=drafts.get(card.dataset.item!);
    if(draft){card.querySelector<HTMLSelectElement>('.item-status')!.value=draft.status;card.querySelector<HTMLTextAreaElement>('.item-notes')!.value=draft.notes;}
  }
}
async function inspect(evidenceId: string, through = answer?.through_s ?? status?.processed_through_s ?? 0) {
  if (!status) return;
  const current = status.run_id;
  const version = ++previewVersion;
  try {
    const [value, records] = await Promise.all([api('/api/reviews/moment',{run_id:current,evidence_id:evidenceId,through_s:through}),api(`/api/memory?now=${through}`)]);
    if (version!==previewVersion || status.run_id !== current || value.run_id !== current || records.run_id !== current) return;
    moment = value; memory = records; $('#reviewInspector').hidden = false;
    $('#previewRange').textContent = `${clock(value.start_s)} – ${clock(value.end_s)} · match at ${clock(value.evidence_s)}`;
    $<HTMLAnchorElement>('#reviewClip').href = `/?view=moment&id=${encodeURIComponent(value.id)}`;
    const rows = (memory?.records || []).filter(r=>r.kind==='transcript' && r.t_end>=value.start_s && r.t_start<=value.end_s);
    $('#nearbyTranscript').innerHTML = rows.length ? rows.map(r=>`<button class="review-transcript" data-seek="${r.t_start}"><time>${clock(r.t_start)}</time><span>${escape(r.text)}</span></button>`).join('') : '<p class="review-empty-note">No transcript was captured in this range. Listen to the footage to check.</p>';
    video.onloadedmetadata = () => { if(moment?.id !== value.id) return; video.currentTime=value.start_s; void video.play().catch(()=>tell('Press Play to watch this source moment.')); };
    video.preload='metadata';
    video.src=`/video?run=${encodeURIComponent(current)}`;
    $('#reviewInspector').scrollIntoView({behavior:'auto',block:'nearest'});
  } catch(error) {tell((error as Error).message);}
}
function constrain() {
  if (!moment) return;
  if (video.currentTime < moment.start_s - .05 || video.currentTime > moment.end_s + .05) video.currentTime=Math.max(moment.start_s,Math.min(moment.end_s,video.currentTime));
  if (video.currentTime >= moment.end_s) video.pause();
}
video.addEventListener('timeupdate',constrain); video.addEventListener('seeking',constrain);
function renderResults() {
  if (!answer) return;
  const citations = answer.citations.filter(c=>c.evidence_id).slice(0,12);
  $('#resultsHeading').textContent='Answer from the available stream';
  $('#resultCount').textContent=`${citations.length} supporting moments`;
  $('#reviewResults').innerHTML=`<p class="review-answer">${escape(answer.summary)}</p><p class="review-answer-meta">Available through ${clock(answer.through_s || 0)} · ${answer.source_label === 'EXTRACTIVE' ? 'Source excerpts' : 'Generated answer; inspect the sources'}</p>${citations.map((c,i)=>`<article class="review-match"><div class="review-match-label"><span>${String(i+1).padStart(2,'0')}</span><strong>${clock(c.t)}${c.t_end ? ` – ${clock(c.t_end)}` : ''}</strong><small>${sourceLabel(c.source_type || '')}</small></div><p>${escape(c.excerpt || c.label)}</p><div class="review-match-actions"><button class="button" data-watch="${escape(c.evidence_id)}">Watch context</button><button class="button" data-add="${i}">${config.save} +</button></div></article>`).join('')}${!citations.length?'<p class="review-empty-note">Try a name or phrase from the stream, or wait for more footage to be processed. A missing match does not establish that something never happened.</p>':''}`;
  renderAnswerContext($('#reviewResults'),answer,t=>{const record=answer?.context?.records.find(r=>r.start_s===t);if(record)void inspect(record.evidence_id);});
}
async function ask() {
  if (working || !status) return;
  const value = $<HTMLTextAreaElement>('#reviewQuestion').value.trim(); if (!value) return;
  working = true; const version = generation, current = status.run_id, check = requirement;
  $<HTMLButtonElement>('#reviewSubmit').disabled=true;
  tell('Searching the available stream and checking the source moments…');
  try {
    const result = await api('/api/ask',{question:value,now:status.processed_through_s,conversation_id:conversation,share:false}) as Answer;
    if (generation !== version || result.run_id !== current) return;
    answer=result;question=value;answerRequirement=check;renderResults();tell('Open a source moment, then save it to your board. This answer has not been sent to the broadcast.');
  } catch(error) {tell((error as Error).message);} finally {working=false;$<HTMLButtonElement>('#reviewSubmit').disabled=!status;}
}
$('#reviewAsk').addEventListener('submit',e=>{e.preventDefault();void ask();});
$('#reviewQuestion').addEventListener('input',()=>{requirement='';});
$('#briefForm').addEventListener('input',()=>{editing=true;briefDirty=true;$('#briefState').textContent='Unsaved changes';});
$('#briefForm').addEventListener('submit',e=>{e.preventDefault();void mutate({action:'brief',brief:{name:$<HTMLInputElement>('#briefName').value,terms:$<HTMLInputElement>('#briefTerms').value,checklist:$<HTMLTextAreaElement>('#briefChecklist').value.split('\n').map(s=>s.trim()).filter(Boolean)}}).then(()=>{fillBrief();tell('Brief saved locally. Select a checklist item to look for supporting moments.');}).catch(error=>tell(error.message));});
$('#boardFilter').addEventListener('change',()=>{if(editing){tell('Save your changes before filtering the board.');return;}renderBoard();});
$('#reviewItems').addEventListener('input',e=>{const card=(e.target as HTMLElement).closest<HTMLElement>('[data-item]');if(!card)return;editing=true;drafts.set(card.dataset.item!,{status:card.querySelector<HTMLSelectElement>('.item-status')!.value,notes:card.querySelector<HTMLTextAreaElement>('.item-notes')!.value});});
$('#refreshBoard').addEventListener('click',()=>{void loadBoard(true).then(()=>tell(editing?'Board refreshed. Your unsaved edits are preserved in the forms; review them before saving.':'Board refreshed.')).catch(e=>tell(e.message));});
$('#closePreview').addEventListener('click',closePreview);
$('#exportBoard').addEventListener('click',()=>{
  if(!board)return;
  const payload={...board,exported_at:new Date().toISOString(),notice:'Human review notes. Not audience metrics or automated moderation decisions. Source links require this local recording and replay.'};
  const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download=`recall-${role}-${board.run_id.slice(0,8)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);tell('Saved notes exported. Download source clips separately from the clip editor.');
});
document.addEventListener('click',e=>{
  const button=(e.target as HTMLElement).closest<HTMLElement>('[data-watch],[data-add],[data-save],[data-remove],[data-preset],[data-check],[data-seek]'); if(!button)return;
  const d=button.dataset;
  if(d.watch) void inspect(d.watch,d.cutoff?Number(d.cutoff):undefined);
  if(d.seek && moment) {video.currentTime=Math.max(moment.start_s,Math.min(moment.end_s,Number(d.seek)));void video.play().catch(()=>{});}
  if(d.preset!==undefined) {
    requirement='';let query=config.presets[Number(d.preset)];const terms=board?.brief.terms||board?.brief.name;
    if(role==='campaign') {
      if(Number(d.preset)===0){if(!terms){tell('Add a brand or product name in the campaign brief first.');$('#briefTerms').focus();return;}query=`What was said or shown about ${terms}?`;}
      if(Number(d.preset)===1)query='What did they say about this stream being sponsored or paid for?';
      if(Number(d.preset)===2 && terms)query=`What did they say about ${terms}?`;
    }
    $<HTMLTextAreaElement>('#reviewQuestion').value=query;$('#reviewQuestion').focus();
  }
  if(d.check!==undefined && board) {requirement=board.brief.checklist[Number(d.check)];$<HTMLTextAreaElement>('#reviewQuestion').value=`Find the part of the stream about: ${requirement}${board.brief.terms?`. Look for ${board.brief.terms}.`:''}`;$('#reviewQuestion').focus();}
  if(d.add!==undefined && answer) {
    const citation=answer.citations.filter(c=>c.evidence_id).slice(0,12)[Number(d.add)] as Evidence;
    if(editing){tell('Save your edits before adding a moment.');return;}
    (button as HTMLButtonElement).disabled=true;
    void mutate({action:'create',title:question.slice(0,200),evidence_ids:[citation.evidence_id],through_s:answer.through_s,requirement:answerRequirement})
      .then(()=>tell('Source moment saved. Add your review notes on the right.')).catch(error=>tell(error.message)).finally(()=>{(button as HTMLButtonElement).disabled=false;});
  }
  if(d.save) {const card=button.closest('.review-saved')!;void mutate({action:'update',id:d.save,status:card.querySelector<HTMLSelectElement>('.item-status')!.value,notes:card.querySelector<HTMLTextAreaElement>('.item-notes')!.value}).then(()=>tell('Review saved. Your notes do not become evidence for the bot.')).catch(error=>tell(error.message));}
  if(d.remove) void mutate({action:'remove',id:d.remove}).then(()=>tell('Removed from this board. Source memory is unchanged.')).catch(error=>tell(error.message));
});
async function poll() {
  try {
    const next=await api('/api/status') as Status;
    const changed=!!status && status.run_id!==next.run_id, first=!status;
    status=next;
    if(changed){generation++;answer=null;memory=null;board=null;editing=false;requirement='';closePreview();$('#reviewResults').textContent='A new replay has started. Ask about its available memory.';$('#resultCount').textContent='';tell('New replay: the board starts empty. Earlier notes are kept in the previous run’s local files.');}
    $('#reviewRecording').textContent=next.title||'No recording loaded';
    $('#reviewClock').textContent=`Memory ${clock(next.processed_through_s)} · source ${clock(next.playback_s)}`;
    $('#reviewMode').textContent=next.mode==='fixture'||next.mode==='synthetic'?'Synthetic example data':next.state;
    const gaps=next.coverage_gaps||[];
    $('#reviewCoverage').hidden=!gaps.length;
    $('#reviewCoverage').textContent=`Missing analysis: ${gaps.map(g=>`${clock(g.start_s)}–${clock(g.end_s)} (${g.lane})`).join(', ')}. Results may be incomplete.`;
    $<HTMLButtonElement>('#reviewSubmit').disabled=working;
    if(moment && (moment.run_id!==next.run_id || moment.end_s>next.processed_through_s))closePreview();
    if(first||changed||!board) await loadBoard();
  } catch(error) {closePreview();$<HTMLButtonElement>('#reviewSubmit').disabled=true;tell(`Could not reach Recall. ${(error as Error).message}`);}
  window.setTimeout(()=>void poll(),2500);
}
void poll();
