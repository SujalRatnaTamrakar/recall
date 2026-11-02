"""Evidence-linked story memory. Derived notes select sources; they are not proof."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re

from .schema import Record, tokenize

EMPTY_PROFILE = {'channel': '', 'streamer': '', 'character': '', 'activity': '', 'title': '', 'description': '',
                 'cast': [], 'speaker_bindings': [], 'semantic_search': True, 'inspect_frames': True}
ROLES = {'streamer', 'character', 'guest', 'mentioned', 'unknown'}


def validate_profile(value: dict, records: list[Record]) -> dict:
    if not isinstance(value, dict) or set(value) - set(EMPTY_PROFILE):
        raise ValueError('Unknown stream context field.')
    out = copy.deepcopy(EMPTY_PROFILE)
    for key in ('channel', 'streamer', 'character', 'activity', 'title', 'description'):
        text = value.get(key, '')
        limit = 1200 if key == 'description' else 240 if key == 'title' else 120
        if not isinstance(text, str) or len(text) > limit:
            raise ValueError(f'Stream {key} must be at most {limit} characters.')
        out[key] = text.strip()
    for key in ('semantic_search', 'inspect_frames'):
        if type(value.get(key, True)) is not bool:
            raise ValueError('Invalid memory option.')
        out[key] = value.get(key, True)
    cast = value.get('cast', [])
    if not isinstance(cast, list) or len(cast) > 24:
        raise ValueError('Add up to 24 people or characters.')
    names = set()
    for person in cast:
        if not isinstance(person, dict) or set(person) - {'name', 'role', 'aliases'}:
            raise ValueError('Invalid cast member.')
        name, role, aliases = person.get('name'), person.get('role'), person.get('aliases', [])
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or role not in ROLES:
            raise ValueError('Each cast member needs a name and role.')
        if name.strip().casefold() in names:
            raise ValueError('Use one entry per name.')
        names.add(name.strip().casefold())
        if not isinstance(aliases, list) or len(aliases) > 8 or any(not isinstance(a,str) or not 1 <= len(a.strip()) <= 80 for a in aliases):
            raise ValueError('Add up to eight short aliases per person.')
        out['cast'].append({'name': name.strip(), 'role': role, 'aliases': list(dict.fromkeys(a.strip() for a in aliases))})
    bindings = value.get('speaker_bindings', [])
    if not isinstance(bindings, list) or len(bindings) > 200:
        raise ValueError('Too many speaker labels.')
    spoken = {r.evidence_id for r in records if r.kind == 'transcript'}
    for binding in bindings:
        if (not isinstance(binding, dict) or set(binding) != {'evidence_id','name'} or
                binding.get('evidence_id') not in spoken or not isinstance(binding.get('name'),str) or
                not 1 <= len(binding['name'].strip()) <= 80):
            raise ValueError('Speaker labels must refer to an available transcript excerpt.')
        out['speaker_bindings'].append({'evidence_id':binding['evidence_id'],'name':binding['name'].strip()})
    return out


def profile_at(versions: list[dict], cutoff: float) -> dict:
    eligible = [v for v in versions if isinstance(v,dict) and isinstance(v.get('known_s'),(int,float))
                and math.isfinite(v['known_s']) and 0 <= v['known_s'] <= cutoff]
    return copy.deepcopy(max(eligible, key=lambda v: (v['known_s'],v.get('version',0)))) if eligible else {
        'version':0, 'known_s':0, 'data':copy.deepcopy(EMPTY_PROFILE)}


def evidence_row(record: Record, *, role='historical', profile=None) -> dict:
    row = {'evidence_id': record.evidence_id, 't_start':record.t_start, 't_end':record.t_end,
           'source_type':record.provenance, 'excerpt':(getattr(record,'caption','') or record.text)[:900],
           'context_role':role}
    if record.kind == 'transcript':
        row['speaker'] = getattr(record,'speaker','unknown')
        for binding in (profile or {}).get('speaker_bindings',[]):
            if binding['evidence_id'] == record.evidence_id:
                row['speaker'] = binding['name']
                row['speaker_basis'] = 'creator_label_for_this_excerpt_only'
    elif record.kind=='scene' and (profile or {}).get('character') and re.search(
            r'\b(?:main controlled|player.controlled) (?:character|avatar)\b',row['excerpt'],re.I):
        row['subject'] = profile['character']
        row['subject_basis'] = 'creator_character_and_explicit_controlled_avatar_observation'
    return row


def unsupported_speaker_attribution(text: str, evidence: list[dict], profile: dict) -> bool:
    """A creator roster is not permission to attribute every voice to its names."""
    if not any(e.get('source_type')=='spoken' for e in evidence) and re.search(
            r'\b(?:said|says|stated|told|mentioned|speaker|reported|talking)\b',text,re.I):
        return True
    people = [{'name':profile.get(key,''),'aliases':[]} for key in ('streamer','character')]
    people += profile.get('cast',[])
    for person in people:
        names = [person['name'],*person.get('aliases',[])]
        names = [name for name in names if name]
        for name in names:
            direct=r'\b'+re.escape(name)+r"(?:'s)?\s+(?:(?:is|was|has|had)\s+)?(?:said|says|saying|stated|stating|states|explained|reported|mentioned|told|planned|plans|wants|talking|discussing)\b"
            simultaneous=r'\b'+re.escape(name)+r'\b[^.!?]{0,140}\b(?:while|and)\s+(?:saying|stating|talking|explaining)\b'
            if not (re.search(direct,text,re.I) or re.search(simultaneous,text,re.I)):
                continue
            supported = any(e.get('source_type')=='spoken' and (
                str(e.get('speaker','')).casefold() in {n.casefold() for n in names} or
                re.search(r'\b'+re.escape(name)+r'\s+(?:said|says|stated|told|explained|plans)\b',e.get('excerpt',''),re.I) or
                re.search(r"\b(?:my name is|i am|i'm)\s+"+re.escape(name)+r'\b',e.get('excerpt',''),re.I)) for e in evidence)
            if not supported: return True
    return False


def extractive_anchors(memory, start: float, end: float) -> list[dict]:
    """Preserve explicit introductions/plans/changes even if a summary omits them."""
    events=[]
    for record in memory.records:
        if record.kind!='transcript' or not (start < record.completion_s <= end or start==record.completion_s==0): continue
        text=record.text
        if re.search(r'\bmy (?:name|character)\b|\bcall me\b|\bi play\b',text,re.I): category='introduction'
        elif re.search(r'\b(?:cancelled|canceled|instead|no longer|change of plan)\b',text,re.I): category='change'
        elif re.search(r"\b(?:plan|planning|plans|going to|we will|i will|we'll|i'll|next we|next i)\b",text,re.I): category='plan'
        else: continue
        events += validate_episode({'events':[{'summary':text[:500],'category':category,
            'evidence_ids':[record.evidence_id],'supersedes':[]}]},memory,start,end)
    for event in events: event['verbatim']=True
    return events


def validate_episode(result: dict, memory, start: float, end: float) -> list[dict]:
    """Resolve all dates and IDs in code. A model never chooses known-at time."""
    if not isinstance(result,dict) or not isinstance(result.get('events'),list) or len(result['events']) > 6:
        raise ValueError('Invalid story update.')
    by_id = {r.evidence_id:r for r in memory.records if start < r.completion_s <= end or start == r.completion_s == 0}
    previous = {event['id'] for event in memory.story_events}
    clean = []
    for event in result['events']:
        ids = event.get('evidence_ids') if isinstance(event,dict) else None
        if (not isinstance(ids,list) or not ids or len(ids)>6 or any(i not in by_id for i in ids)
                or event.get('category') not in {'introduction','statement','plan','action','change'}
                or not isinstance(event.get('summary'),str) or not 1 <= len(event['summary']) <= 500):
            raise ValueError('Story update cites unavailable evidence.')
        supersedes = event.get('supersedes',[])
        if not isinstance(supersedes,list) or len(supersedes)>4 or any(i not in previous for i in supersedes):
            raise ValueError('Invalid prior story reference.')
        if supersedes and event['category'] != 'change':
            # A valid note can survive a bad state-change classification, but
            # the proposed replacement must not affect prior facts.
            supersedes = []
        category=event['category']
        if category=='introduction' and not any(by_id[i].kind=='transcript' and re.search(
            r"\bmy (?:name|character)\b|\b(?:call me|i play|this is|i am|i'm)\b",by_id[i].text,re.I) for i in ids):
            category='statement' if any(by_id[i].kind=='transcript' for i in ids) else 'action'
        digest = hashlib.sha256(json.dumps([memory.run_id,sorted(ids),event['summary']],ensure_ascii=False).encode()).hexdigest()[:20]
        clean.append({'id':'story_'+digest, 'run_id':memory.run_id, 'known_s':end,
                      'start_s':min(by_id[i].t_start for i in ids), 'end_s':max(by_id[i].t_end for i in ids),
                      'summary':event['summary'].strip(), 'category':category,
                      'evidence_ids':list(dict.fromkeys(ids)), 'supersedes':supersedes})
    return clean


def eligible_events(events: list[dict], records: list[Record], run_id: str, cutoff: float) -> list[dict]:
    ids = {r.evidence_id for r in records}
    return copy.deepcopy([e for e in events if e.get('run_id') == run_id and
        isinstance(e.get('known_s'),(int,float)) and 0 <= e['known_s'] <= cutoff and
        e.get('evidence_ids') and all(i in ids for i in e['evidence_ids'])])


def story_sources(memory, question: str, intent: str, limit=18) -> tuple[list[Record],list[dict]]:
    """Reserve opening identity and changing-plan context, never just the latest captions."""
    by_id = {r.evidence_id:r for r in memory.records}
    question_terms = set(tokenize(question)) - {'said','say','says','tell','talk','talking','happened','happening','stream','video','currently'}
    terms = set(question_terms)
    profile = profile_at(memory.profile_versions,memory.end_time())['data']
    terms.update(tokenize(' '.join(profile.get(k,'') for k in ('streamer','character'))))
    events = list(memory.story_events)
    superseded = {i for e in events for i in e.get('supersedes',[])}
    def score(e):
        return len(terms.intersection(tokenize(e['summary'])))
    if intent == 'recap':
        chosen = spread(events,12)
    elif intent in {'current','prediction','plan'}:
        introductions = [e for e in events if e['category']=='introduction']
        recent_changes = [e for e in events if e['category']=='change'][-3:]
        plans = [e for e in events if e['category']=='plan' and e['id'] not in superseded][-3:]
        relevant = sorted(events,key=lambda e:(score(e),e['known_s']),reverse=True)
        chosen = recent_changes + sorted(introductions,key=score,reverse=True)[:4] + plans + [e for e in relevant if score(e)][:5]
    else:
        chosen = sorted([e for e in events if question_terms.intersection(tokenize(e['summary']))],key=score,reverse=True)[:6]
    chosen = list({e['id']:e for e in chosen}.values())
    selected = [by_id[i] for e in chosen for i in e['evidence_ids'] if i in by_id]
    # Until background extraction finishes, explicit introductions and plan
    # changes remain discoverable directly. This never assigns a guessed name.
    anchors = [r for r in memory.records if r.kind=='transcript' and re.search(
        r"\bmy (?:name|character)\b|\b(?:call me|i play|i am playing|i'm playing)\b|\b(?:cancelled|canceled|instead|change of plan|no longer)\b",r.text,re.I)]
    if intent not in {'current','prediction','plan','recap'}:
        anchors=[r for r in anchors if question_terms.intersection(tokenize(r.text))]
    selected += anchors[:3] + anchors[-4:]
    return list({r.evidence_id:r for r in selected}.values())[:limit], chosen


def spread(items: list, count: int) -> list:
    if len(items) <= count: return list(items)
    if count <= 1: return items[-count:] if count else []
    return [items[round(i*(len(items)-1)/(count-1))] for i in range(count)]


def compact_profile(profile: dict, query: str) -> dict:
    out = copy.deepcopy(profile)
    # These have their own bounded field in the answer context.
    out.pop('title', None)
    out.pop('description', None)
    terms = set(tokenize(query))
    out['cast'] = sorted(out.get('cast',[]),key=lambda p:len(terms.intersection(
        tokenize(' '.join([p['name'],*p['aliases']])))),reverse=True)[:8]
    for person in out['cast']: person['aliases'] = person['aliases'][:2]
    # Assigned speakers already appear on each evidence row.
    out['speaker_bindings'] = []
    return out


def review_episode(memory, model, start: float, end: float) -> list[dict]:
    """Shared replay/import path, bounded text and source-checked updates."""
    profile = profile_at(memory.profile_versions,end)['data']
    records = [r for r in memory.records if start < r.completion_s <= end or start == r.completion_s == 0]
    if memory.semantic and profile['semantic_search']: memory.semantic.index(records)
    speech = [r for r in records if r.kind=='transcript']
    previous_scenes=[r for r in memory.records if r.kind=='scene' and r.completion_s <= start]
    last_text=previous_scenes[-1].text if previous_scenes and start>0 else None
    scenes=[]
    for record in records:
        if record.kind=='scene' and record.text != last_text:
            scenes.append(record); last_text=record.text
    chosen = sorted(spread(speech,24)+spread(scenes,8),key=lambda r:r.t_start)
    rows, used = [],0
    for record in chosen:
        row = evidence_row(record,profile=profile)
        row['excerpt'] = row['excerpt'][:600]
        size = len(json.dumps(row,ensure_ascii=False))
        if used+size>10000: continue
        rows.append(row); used+=size
    previous = [{k:e[k] for k in ('id','summary','category','evidence_ids')} for e in memory.story_events[-6:]]
    result = model.summarize_episode(rows,previous,compact_profile(profile,'')) if rows else {'events':[]}
    events = validate_episode(result,memory,start,end)
    by_id = {r.evidence_id:r for r in memory.records}
    previous_by_id = {e['id']:e for e in memory.story_events}
    # The update's wording was checked by the writer. Check each proposed
    # replacement against BOTH old and new original sources before using it.
    for event in events:
        confirmed=[]
        for old_id in event['supersedes']:
            old=previous_by_id[old_id]
            ids=list(dict.fromkeys(old['evidence_ids']+event['evidence_ids']))
            claim={'text':f"The earlier statement ({old['summary']}) was explicitly changed: {event['summary']}",
                   'kind':'fact','evidence_ids':ids}
            if callable(getattr(model,'verify_claims',None)):
                accepted=model.verify_claims('Did this new statement explicitly change the earlier one?', [claim],
                    [evidence_row(by_id[i],profile=profile) for i in ids],stream_context={'creator_profile':compact_profile(profile,'')})
                if accepted == [0]: confirmed.append(old_id)
        event['supersedes']=confirmed
    for anchor in extractive_anchors(memory,start,end):
        # Exact important speech is stronger than a paraphrase of the same
        # source, and preserves plan versus introduction classifications.
        same=[e for e in events if set(e['evidence_ids'])==set(anchor['evidence_ids'])]
        anchor['supersedes']=list(dict.fromkeys(i for e in same for i in e['supersedes'])) if anchor['category']=='change' else []
        events=[e for e in events if e not in same]+[anchor]
    return events


def recording_context(memory, cutoff: float | None = None) -> dict:
    """Bounded metadata gives a scene context; it does not prove events or identity."""
    at = memory.end_time() if cutoff is None else min(cutoff, memory.end_time())
    profile = profile_at(memory.profile_versions, at)['data']
    return {'title': (profile.get('title') or memory.video.title)[:300],
            'description': profile.get('description', '')[:1200],
            'channel': profile.get('channel', '')[:120],
            'activity': profile.get('activity', '')[:120],
            'basis': 'source title and creator-provided background; not visual or spoken evidence'}


def valid_contextual_claim(claim: dict, evidence: list[dict], context: dict) -> bool:
    """Present-scene interpretation needs metadata, visual support and a hedge.

    The model verifier still checks meaning. This gate prevents contextual
    claims from bypassing source, recency or prediction controls.
    """
    if context.get('modality') == 'spoken':
        return False
    metadata = context.get('recording_context', {})
    if not any(metadata.get(k) for k in ('title', 'description', 'channel', 'activity')):
        return False
    text = claim['text']
    if not re.search(r'\b(?:appears?|seems?|looks? like|likely)\b', text, re.I):
        return False
    if re.search(r"\b(?:will|going to|gonna|next|later|tomorrow)\b", text, re.I):
        return False
    cited = [e for e in evidence if e['evidence_id'] in claim['evidence_ids'] and e['source_type'] == 'observed']
    if context.get('intent') in {'current', 'prediction'}:
        cited = [e for e in cited if e['context_role'] == 'recent']
    return bool(cited)


def transcript_neighbors(memory, ordered: list[Record], eligible: list[Record] | None = None) -> list[Record]:
    """Keep sentence continuations near top speech hits, without merging evidence."""
    speech = sorted((r for r in (memory.records if eligible is None else eligible) if r.kind == 'transcript'),key=lambda r:(r.t_start,r.t_end))
    indices = {r.evidence_id:i for i,r in enumerate(speech)}
    seeds = {r.evidence_id for r in ordered if r.kind == 'transcript'}
    expanded, used, expanded_seeds = [], set(), 0
    for record in ordered:
        group = [record]
        if record.evidence_id in seeds and expanded_seeds < 8:
            expanded_seeds += 1
            index = indices.get(record.evidence_id)
            if index is not None:
                for pos in (index-1,index+1):
                    if not 0 <= pos < len(speech): continue
                    neighbor = speech[pos]
                    earlier,later = sorted((record,neighbor),key=lambda r:r.t_start)
                    gap = later.t_start-earlier.t_end
                    hole = any(g['lane']=='audio' and g['start_s']<later.t_start and g['end_s']>earlier.t_end
                               for g in memory.coverage_gaps)
                    if 0 <= gap <= 2 and not hole:
                        group.append(neighbor)
        for item in group:
            if item.evidence_id not in used:
                expanded.append(item);used.add(item.evidence_id)
    return expanded


def assemble_context(memory, question: str, intent: str, candidates: list[Record], recent_start: float,
                     *, char_budget=10000, modality='any', neighbor_pool=None) -> tuple[list[Record], list[dict], dict, dict]:
    profile = profile_at(memory.profile_versions,memory.end_time())
    anchors, events = story_sources(memory,question,intent)
    if modality == 'spoken':
        anchors = [r for r in anchors if r.kind == 'transcript']
    if intent in {'current','prediction','plan'}:
        recent = [r for r in candidates if r.t_end >= recent_start]
        ordered = recent[-9:] + anchors + candidates
    elif intent == 'recap':
        ordered = anchors + candidates
    else:
        ordered = candidates[:10] + anchors + candidates[10:]
    ordered = list({r.evidence_id:r for r in ordered}.values())
    if modality != 'visual':
        ordered = transcript_neighbors(memory,ordered,neighbor_pool)
    # Compact context is separately budgeted. Facts still require original IDs.
    context = {'intent':intent, 'modality':modality, 'as_of_s':memory.end_time(), 'creator_profile':compact_profile(profile['data'],question), 'profile_version':profile['version'],
        'recording_context':recording_context(memory),
        'story_through_s':memory.story_through_s, 'story_notes':[
            {k:e[k] for k in ('id','summary','category','known_s','evidence_ids','supersedes')} for e in events[:12]],
        'instructions':'Story notes are derived retrieval hints. Check original excerpts. Old plans are not current actions.'}
    # Binding data is included only for the selected excerpts, keeping long
    # editing histories out of each prompt and preserving the source labels.
    while len(json.dumps(context,ensure_ascii=False)) > 3500 and context['story_notes']:
        context['story_notes'].pop()
    rows, kept, used = [], [], 0
    for record in ordered:
        role = 'recent' if record.t_end >= recent_start and intent in {'current','prediction'} else 'background'
        row = evidence_row(record,role=role,profile=profile['data'])
        row['excerpt'] = row['excerpt'][:600]
        size = len(json.dumps(row,ensure_ascii=False))
        if used + size > char_budget: continue
        shown=copy.copy(record)
        if row.get('speaker_basis'):
            shown.speaker=row['speaker']
        rows.append(row); kept.append(shown); used += size
        if len(rows) >= 32: break
    # Relevance chooses what fits; chronology makes split utterances readable.
    rows.sort(key=lambda row:(row['t_start'],row['t_end']))
    available_ids = {r.evidence_id for r in kept}
    context['story_notes'] = [e for e in context['story_notes'] if all(i in available_ids for i in e['evidence_ids'])]
    debug = {'scope_start_s':0 if intent in {'current','prediction','recap'} else None,
             'through_s':memory.end_time(), 'profile_version':profile['version'], 'story_through_s':memory.story_through_s,
             'selected_records':len(rows), 'context_characters':used+len(json.dumps(context,ensure_ascii=False)),
             'creator_profile':copy.deepcopy(context['creator_profile']),
             'recording_context':copy.deepcopy(context['recording_context']),
             'semantic_search':bool(memory.semantic and memory.semantic.ready and profile['data']['semantic_search']),
             'records':[{'evidence_id':r['evidence_id'],'start_s':r['t_start'],'end_s':r['t_end'],
                         'source_type':r['source_type'],'role':r['context_role'],'excerpt':r['excerpt']} for r in rows]}
    return kept, rows, context, debug
