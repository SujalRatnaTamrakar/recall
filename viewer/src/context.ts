import './context.css';
import type { Answer } from './types';

type Person = {name:string;role:string;aliases:string[]};
type Profile = {channel:string;streamer:string;character:string;activity:string;title:string;description:string;cast:Person[];
  speaker_bindings:{evidence_id:string;name:string}[];semantic_search:boolean;inspect_frames:boolean};
type ContextData = {run_id:string;profile:Profile;version:number;known_s:number;story_through_s:number;recording_title?:string;
  semantic_ready:boolean;semantic_error?:string;event_count:number;
  story_gaps?:{start_s:number;end_s:number}[];
  events:{summary:string;category:string;known_s:number}[];
  transcripts:{evidence_id:string;t_start:number;text:string;speaker:string}[]};
const clock = (s:number) => `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`;

export function installStreamContext(request:<T>(path:string,data?:unknown)=>Promise<T>) {
  const panel = document.createElement('details');
  panel.id='streamContext'; panel.className='diagnostics-panel stream-context operator-only';
  panel.innerHTML=`<summary><span>Stream context <small>Tell the bot who’s who · explore its story memory</small></span></summary>
    <div class="diagnostics-body"><p>Set this up before pressing Start. The bot combines this background with speech and footage from the whole processed stream. Names you enter are creator-provided labels.</p>
    <form id="contextForm"><fieldset disabled><div class="context-fields">
    <label>Channel name<input name="channel" maxlength="120" placeholder="Your channel"></label>
    <label>Streamer’s name<input name="streamer" maxlength="120" placeholder="Who is broadcasting?"></label>
    <label>Controlled character<input name="character" maxlength="120" placeholder="Optional · character the streamer plays"></label>
    <label>Game or activity<input name="activity" maxlength="120" placeholder="Roleplay, cooking, a birdwatching trip…"></label>
    <label class="context-wide">Recording / stream title<input name="title" maxlength="240" placeholder="Leave blank to use the recording’s filename"></label>
    <label class="context-wide">Description / background<textarea name="description" maxlength="1200" rows="3" placeholder="What is this broadcast about? Add reliable background that helps interpret the footage."></textarea></label></div>
    <p id="contextRecordingTitle"></p><p>The title and description help interpret the scene. They do not prove who is on screen or what happens next.</p>
    <h3>People &amp; characters</h3><p>Add known names and nicknames. A name alone does not identify an unknown voice or everyone visible on screen.</p>
    <div id="contextCast"></div><button type="button" class="button" id="addContextPerson">Add a person or character</button>
    <h3>Label a spoken excerpt</h3><p>Choose a line you recognize and name its speaker. This label applies to that excerpt only; automatic voice identification is not enabled.</p>
    <div class="context-fields"><label>Transcript excerpt<select id="contextUtterance"><option value="">Keep existing labels</option></select></label>
    <label>Speaker’s name<input id="contextSpeaker" maxlength="80" placeholder="Name the person speaking"></label></div>
    <div class="context-switches"><label><input name="semantic_search" type="checkbox" checked>Search by meaning as well as exact words</label>
    <label><input name="inspect_frames" type="checkbox" checked>Recheck relevant original frames when answering</label></div>
    <p id="contextSearchStatus"></p><div class="diagnostics-actions"><button class="button primary" type="submit">Save stream context</button><button class="button" type="button" id="refreshContext">Refresh memory</button></div>
    </fieldset></form><p id="contextFeedback" role="status">Open to load this recording’s context.</p>
    <h3>Story memory</h3><p id="storyProgress"></p><div id="storyEvents"></div>
    <p>Story notes help find source moments. Answers are checked against the original excerpts and selected frames. New labels apply from the point you save them; restarting carries your setup into a fresh run.</p></div>`;
  document.querySelector('#testMode')!.before(panel);
  const form=panel.querySelector<HTMLFormElement>('form')!;
  const fieldset=form.querySelector('fieldset')!;
  const cast=panel.querySelector<HTMLElement>('#contextCast')!;
  const feedback=panel.querySelector<HTMLElement>('#contextFeedback')!;
  const field=(name:string)=>form.elements.namedItem(name) as HTMLInputElement;
  let current:ContextData|null=null;
  let busy=false;
  function addPerson(person:Person={name:'',role:'character',aliases:[]}) {
    if (cast.children.length>=24) return;
    const row=document.createElement('div'); row.className='context-person';
    row.innerHTML='<label>Name<input data-person="name" maxlength="80" required></label><label>Role<select data-person="role"><option value="character">Character</option><option value="streamer">Streamer</option><option value="guest">Guest / speaker</option><option value="mentioned">Mentioned only</option><option value="unknown">Not yet established</option></select></label><label>Nicknames, separated by commas<input data-person="aliases" maxlength="640"></label><button class="button" type="button" aria-label="Remove cast member">Remove</button>';
    (row.querySelector('[data-person="name"]') as HTMLInputElement).value=person.name;
    (row.querySelector('[data-person="role"]') as HTMLSelectElement).value=person.role;
    (row.querySelector('[data-person="aliases"]') as HTMLInputElement).value=person.aliases.join(', ');
    row.querySelector('button')!.addEventListener('click',()=>row.remove());
    cast.append(row);
  }
  function show(data:ContextData) {
    current=data;
    for(const name of ['channel','streamer','character','activity','title','description'] as const) field(name).value=data.profile[name]??'';
    panel.querySelector('#contextRecordingTitle')!.textContent=data.recording_title?`Recording title detected: ${data.recording_title}`:'';
    for(const name of ['semantic_search','inspect_frames'] as const) field(name).checked=data.profile[name];
    cast.replaceChildren(); data.profile.cast.forEach(addPerson);
    const select=panel.querySelector<HTMLSelectElement>('#contextUtterance')!;
    select.replaceChildren(new Option('Keep existing labels',''));
    for(const line of [...data.transcripts].reverse()) select.add(new Option(`${clock(line.t_start)} · ${line.speaker} · ${line.text}`,line.evidence_id));
    panel.querySelector('#contextSearchStatus')!.textContent=data.semantic_ready
      ? 'Meaning search is ready and runs locally on the CPU. Frame rechecking may add time to an answer.'
      : 'Meaning search is unavailable; exact-word search still works. Run Setup.ps1 to prepare the local search model.';
    panel.querySelector('#storyProgress')!.textContent=`${data.event_count} source-linked notes · memory updates through ${clock(data.story_through_s)}. Updates run when frame analysis and questions leave room.`;
    if(data.story_gaps?.length) panel.querySelector('#storyProgress')!.textContent+=` ${data.story_gaps.length} summary updates failed; original speech and frames remain searchable.`;
    const events=panel.querySelector('#storyEvents')!; events.replaceChildren();
    for(const event of data.events.slice().reverse()) {
      const row=document.createElement('p'); row.className='context-event';
      const tag=document.createElement('strong'); tag.textContent=`${clock(event.known_s)} · ${event.category}`;
      const text=document.createElement('span'); text.textContent=event.summary;
      row.append(tag,text); events.append(row);
    }
    if(!data.events.length) events.textContent='Notes will appear as completed portions of the stream are reviewed. Questions can already search the raw transcript and frame descriptions.';
  }
  async function load() {
    if(busy) return; busy=true; fieldset.disabled=true;
    try { show(await request<ContextData>('/api/context')); feedback.textContent=`Loaded context version ${current!.version}.`; }
    catch(error) {feedback.textContent=error instanceof Error?error.message:'Could not load stream context.';}
    finally {busy=false;fieldset.disabled=!current;}
  }
  form.addEventListener('submit',async e=>{
    e.preventDefault(); if(!current||busy) return;
    const profile:Profile={...current.profile,cast:[]};
    for(const name of ['channel','streamer','character','activity','title','description'] as const) profile[name]=field(name).value.trim();
    for(const name of ['semantic_search','inspect_frames'] as const) profile[name]=field(name).checked;
    for(const row of cast.children) {
      const value=(key:string)=>(row.querySelector(`[data-person="${key}"]`) as HTMLInputElement).value.trim();
      profile.cast.push({name:value('name'),role:value('role'),aliases:value('aliases').split(',').map(s=>s.trim()).filter(Boolean)});
    }
    const id=panel.querySelector<HTMLSelectElement>('#contextUtterance')!.value;
    const name=panel.querySelector<HTMLInputElement>('#contextSpeaker')!.value.trim();
    if(id&&!name) {feedback.textContent='Enter the speaker’s name for the selected excerpt.';return;}
    if(id) profile.speaker_bindings=[...profile.speaker_bindings.filter(b=>b.evidence_id!==id),{evidence_id:id,name}];
    busy=true;fieldset.disabled=true;feedback.textContent='Saving…';
    try {
      show(await request<ContextData>('/api/context',{run_id:current.run_id,profile}));
      feedback.textContent=`Saved. New questions use these labels from ${clock(current!.known_s)} onward. Your recording and existing evidence are preserved.`;
      panel.querySelector<HTMLInputElement>('#contextSpeaker')!.value='';
    } catch(error) {feedback.textContent=error instanceof Error?error.message:'Could not save context.';}
    finally {busy=false;fieldset.disabled=false;}
  });
  panel.querySelector('#addContextPerson')!.addEventListener('click',()=>addPerson());
  panel.querySelector('#refreshContext')!.addEventListener('click',()=>void load());
  panel.addEventListener('toggle',()=>{if(panel.open) void load();});
  document.addEventListener('tube-run-change',()=>{current=null;fieldset.disabled=true;if(panel.open) void load();});
}

export function renderAnswerContext(container:HTMLElement, answer:Answer, inspect:(seconds:number)=>void) {
  const data=answer.context;
  if(!data?.records?.length) return;
  const details=document.createElement('details'); details.className='trace answer-context';
  const summary=document.createElement('summary'); summary.textContent='What the bot used';
  const note=document.createElement('p');
  note.textContent=`${data.selected_records} source excerpts · ${data.frames?.length||0} original frames · context version ${data.profile_version} · ${data.semantic_search?'meaning + word search':'word search'}. Available through ${clock(data.through_s)}; story notes through ${clock(data.story_through_s)}.`;
  details.append(summary,note);
  if(data.recording_context?.title || data.recording_context?.description) {
    const background=document.createElement('p');
    background.textContent=`Recording background: ${data.recording_context.title||'Untitled'}. ${data.recording_context.description||''} This is context, not proof of events.`;
    details.append(background);
  }
  if(data.creator_profile) {
    const profile=document.createElement('p');
    profile.textContent=`Creator labels used: streamer ${data.creator_profile.streamer||'unassigned'}; controlled character ${data.creator_profile.character||'unassigned'}; activity ${data.creator_profile.activity||'unspecified'}.`;
    details.append(profile);
  }
  for(const record of data.records) {
    const row=document.createElement('div'); row.className='context-source';
    const button=document.createElement('button'); button.className='text-button';
    button.textContent=`${clock(record.start_s)} · ${record.source_type} · ${record.role}`;
    button.addEventListener('click',()=>inspect(record.start_s));
    const text=document.createElement('p'); text.textContent=record.excerpt;
    const id=document.createElement('small'); id.textContent=record.evidence_id;
    row.append(button,text,id);details.append(row);
  }
  container.append(details);
}
