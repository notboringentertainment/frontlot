"""Board adapters. The signed operations alone own sending, spending and decisions."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path

from fastapi import HTTPException, Request
from lib import footage_requests as footage, receipts, supervised_production as shots
from lib.canonical_json import record_sha256
from lib.look_ingest import active_looks
from lib.media_spending import _tracker, request_resolution, resolve_charge, call_lock
from lib.pathsafe import resolve_input, sha256_file
from lib.project_config import load_current_verified_project_config, ProjectConfigError
from lib.run_common import request_paths
from tools.cost_tracker import load_reservations, BudgetExceededError, IndeterminatePaidCallError


def plain_refusal(exc):
    from lib.media_spending import PaidCallBusy
    if isinstance(exc,PaidCallBusy):return 'A sound or footage operation is still running. Wait for it to finish before changing or reviewing this request.'
    if isinstance(exc,IndeterminatePaidCallError):return 'A charge is unresolved. Check the service account, then use the account-review controls to sign its outcome before another request.'
    if isinstance(exc,BudgetExceededError):return 'This clip does not fit the remaining ceiling. Choose a shorter clip or sign a higher production ceiling before creating a new request.'
    if 'allowance is exhausted' in str(exc):return 'This shot has used its allowance. Add a new paper-cut shot with its own request, or have its brief allowance revised before another take.'
    if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError):
        message=str(exc)
        # Machine paths and internal validation fields are never instructions.
        if '/' not in message and '_' not in message:
            return message+' Open the shot again after fixing this; nothing will be retried automatically.'
    return 'This shot could not be verified. Review its saved paper cut, approved looks and reference pictures, then try again.'


def _gate(root,kind,entity):
    directory=root/'.gate-requests'
    if directory.is_symlink():raise ValueError('Signing requests cannot use links. Reopen the production from Front Lot.')
    for path in sorted(directory.glob('*.json')):
        if path.is_symlink():continue
        row=json.loads(path.read_text())
        if row.get('kind')==kind and row.get('entity_id')==entity:return row
    return None


def _paper(root):
    footage.require_trailer(root)
    from lib.trailer_edit import paper_state
    state=paper_state(root)
    return state,(state.get('current') or {}).get('paper_cut',{}).get('shots',[])


def _looks(root):
    return {kind+':'+identity:value for (kind,identity),value in active_looks(root).items()}


def _prepare_brief(root,shot_id,choices):
    # A new board brief is the paper shot itself, never arbitrary provider input.
    footage._paper_snapshot(root)
    _,paper=_paper(root)
    shot=next((s for s in paper if s['id']==shot_id),None)
    if shot is None:raise ValueError('Save this shot in the paper cut before requesting footage.')
    active=_looks(root);old=shots.read_brief(root,shot_id)
    if not active:raise ValueError('This shot has no approved active look. Approve its looks outside these controls first.')
    refs=[];look_refs=[]
    for path in shot['references']:
        key=choices.get(path)
        if key is None and old:
            previous=next((r for r in old['reference_manifest'] if r['path']==path),None)
            key=next((k for k,v in active.items() if previous and v.entity_id==previous.get('visual_bible_entity_id')),None)
        if key is None and len(active)==1:key=next(iter(active))
        if key not in active:raise ValueError('Choose the approved look behind every reference picture, then create the request.')
        look=active[key]
        ref={'entity_kind':look.entity_kind,'entity_id':look.entity_id,'look_hash':look.look_hash}
        if ref not in look_refs:look_refs.append(ref)
        file=resolve_input(path,root)
        refs.append({'path':path,'asset_id':sha256_file(file),'role':'hero' if look.entity_kind=='character' else 'establishing',
                     'visual_bible_entity_id':look.entity_id})
    if not refs:raise ValueError('Add a reference picture to this paper-cut shot before creating its footage request.')
    if old and not old['stopped'] and old.get('paper_shot')==shot and old['reference_manifest']==refs and old['look_refs']==look_refs:
        return old
    config=load_current_verified_project_config(root)
    spec={'shot_id':shot_id,'paper_shot_id':shot_id,'direction':shot['purpose'],
          'source_paths':['checkpoint_canon_ingest.json'],'look_refs':look_refs,'reference_manifest':refs,
          'spend_allowance_usd':old['spend_allowance_usd'] if old else config.budget_usd_cap,
          'max_video_takes':old['max_video_takes'] if old else 10,'allowed_tools':['kling_reference_video']}
    shots.prepare(root,spec,user_note='Prepared from this saved paper-cut shot using its selected approved looks and pictures.')
    return shots.read_brief(root,shot_id)


def create(root,body):
    with call_lock(root):return _create(root,body)


def _create(root,body):
    model=body.get('model');length=body.get('duration_seconds')
    footage.quote(root,model,length)  # refuse invalid choices before writing a brief
    choices=body.get('looks',{})
    if not isinstance(choices,dict):raise ValueError('Choose an approved look for each picture.')
    _prepare_brief(root,body.get('shot_id'),choices)
    record=footage.prepare_request(root,body['shot_id'],model=model,duration_seconds=length,retake_of=body.get('retake_of'))
    gate=_gate(root,'footage_spend',record['request_id']) or footage.request_approval(root,record['request_id'])
    return {'gate':gate}


def prepare_decision(root,body):
    with call_lock(root):
        shot,take,decision=(body.get(k) for k in ('shot_id','take_id','decision'))
        subject=footage.decision_record(root,shot,take,decision)
        pending=_gate(root,'footage_take',take)
        return pending if pending and pending.get('approval_record')==subject else footage.request_decision(root,shot,take,decision)


def inspect(root,jobs):
    state,paper=_paper(root)
    active=_looks(root)
    from backlot.state import _footage_look_words
    looks=[{'key':key,'entity_id':value.entity_id,
            'name':('Character' if value.entity_kind=='character' else 'Location')+f' look {n}',
            'payload':{k:_footage_look_words(v) if isinstance(v,(dict,list)) else v
                       for k,v in value.payload.items()}}
           for n,(key,value) in enumerate(active.items(),1)]
    reservations=load_reservations(root)
    records=[]
    for path in sorted((root/'.footage-requests').glob('*.json'),key=lambda p:(p.stat().st_mtime_ns,p.name)):
        records.append(footage._read(root,path.stem))
    try:remaining=_tracker(root,load_current_verified_project_config(root)).budget_remaining_usd
    except (ValueError,ProjectConfigError,OSError):remaining=None
    try:footage._paper_snapshot(root);source_refusal=None
    except Exception as exc:source_refusal=plain_refusal(exc)
    placed={p.get('take_id') for p in (state.get('current') or {}).get('timeline',{}).get('picture',[]) if p.get('kind')=='footage'}
    result=[]
    for number,shot in enumerate(paper,1):
        history=shots.history(root,shot['id']);takes=[]
        for take in history:
            if take.get('kind')!='take':continue
            status=footage.take_state(root,shot['id'],take['take_id'])
            try:resolve_input(take['path'],root);playable=True
            except (ValueError,OSError,KeyError):playable=False
            takes.append({**take,'status':status,'selected':status=='Accepted' and take['take_id'] in placed,'declined':status=='Declined',
                          'waiting':status=='Waiting for your decision','playable':playable})
        requests=[]
        for record in records:
            if record['shot_id']!=shot['id']:continue
            rid=record['request_id'];gate=_gate(root,'footage_spend',rid)
            approved=receipts.find_approval(root,'footage_spend',entity_id=rid,record_sha256=record_sha256(record)) is not None
            reservation=next((r for r in reservations.values() if (r.get('output_hint') or {}).get('footage_request_id')==rid),None)
            job=jobs.get((str(root),rid),{})
            requests.append({'id':rid,'gate_id':gate['request_id'] if gate else None,'approved':approved,
                             'running':job.get('running',False),'error':job.get('error'),
                             'reservation':reservation,'record':record})
        status='Nothing yet'
        if any(t['waiting'] for t in takes):status='Take waiting for your decision'
        elif any(t['selected'] for t in takes):status='Kept'
        elif any(t['status']=='Needs a fresh look' for t in takes):status='Needs a fresh look'
        elif takes:status='Take waiting for your decision' if any(t['waiting'] for t in takes) else 'Ask for a retake'
        if any(not r['approved'] and r['gate_id'] for r in requests):status='Waiting on your signature'
        if any(r['running'] for r in requests):status='Generating'
        if any(r['reservation'] and r['reservation']['state'] in ('submitting','submitted','pending_billing') and not r['running'] for r in requests):
            status='Charge needs your review'
        brief=shots.read_brief(root,shot['id'])
        result.append({'id':shot['id'],'label':f'Shot {number}','purpose':shot['purpose'],
                       'duration_seconds':shot['duration_frames']/24,'references':shot['references'],
                       'brief':brief,'takes':takes,'requests':requests,'status':status})
    return {'shots':result,'looks':looks,'remaining_usd':remaining,
            'models':[{'id':footage.MODEL,'label':'Kling o3 reference video · Fal'}],
            'story_ready':source_refusal is None,'refusal':source_refusal}


def install(app,require_writer,safe_project,invalidate):
    jobs={};tasks=set();app.state.footage_tasks=tasks

    async def dispatch(root,rid):
        record=footage._read(root,rid)
        if not receipts.find_approval(root,'footage_spend',entity_id=rid,record_sha256=record_sha256(record)):
            raise ValueError('Sign this footage request before making its clip.')
        key=(str(root),rid)
        if jobs.get(key,{}).get('running'):return {'running':True}
        # Reopening a completed request is recovery, not another purchase.
        prior=next((r for r in load_reservations(root).values() if (r.get('output_hint') or {}).get('footage_request_id')==rid),None)
        if prior and prior['state']!='completed':
            raise ValueError('This request was already submitted and its charge needs review. Check the service account and sign an outcome below; do not try it again.')
        job=jobs[key]={'running':True,'error':None}
        async def run():
            try:await asyncio.to_thread(footage.execute,root,rid)
            except Exception as exc:job['error']=plain_refusal(exc)
            finally:job['running']=False;invalidate(root.name)
        task=asyncio.create_task(run());tasks.add(task);task.add_done_callback(tasks.discard)
        return {'running':True}

    @app.get('/api/project/{project_id}/footage')
    async def read(project_id:str):
        try:return await asyncio.to_thread(inspect,safe_project(project_id),jobs)
        except Exception as exc:raise HTTPException(400,plain_refusal(exc)) from None

    @app.post('/api/project/{project_id}/footage/{action}')
    async def act(project_id:str,action:str,request:Request):
        require_writer(request);root=safe_project(project_id)
        fields={'quote':{'shot_id','model','duration_seconds'},'create':{'shot_id','model','duration_seconds','looks','retake_of'},
                'dispatch':{'request_id'},'decision':{'shot_id','take_id','decision'},'signed':{'gate_id'},
                'apply':{'shot_id','take_id','decision'},'resolve':{'reservation_id','charged','cash','credits','note'}}
        try:
            footage.require_trailer(root)
            body=await request.json()
            if action not in fields or not isinstance(body,dict) or set(body)-fields[action]:
                raise ValueError('This control request is invalid. Reopen the shot and use its buttons.')
            if action=='quote':
                _,paper=_paper(root)
                if not any(s['id']==body.get('shot_id') for s in paper):
                    raise ValueError('Save this shot in the paper cut before requesting footage.')
                price=footage.quote(root,body.get('model'),body.get('duration_seconds'))
                return {**price,'highest_usd':round(price['max_cash_usd']+price['max_credits_usd'],4),
                        'remaining_usd':_tracker(root,load_current_verified_project_config(root)).budget_remaining_usd}
            if action=='create':result=await asyncio.to_thread(create,root,body)
            elif action=='dispatch':result=await dispatch(root,body.get('request_id'))
            elif action=='decision':
                result={'gate':await asyncio.to_thread(prepare_decision,root,body)}
            elif action=='apply':
                await asyncio.to_thread(footage.apply_decision,root,body.get('shot_id'),body.get('take_id'),body.get('decision'))
                result={'applied':True}
            elif action=='resolve':result={'gate':await asyncio.to_thread(request_resolution,root,body.get('reservation_id'),body.get('charged'),body.get('cash'),body.get('credits'),body.get('note'))}
            else:
                path=request_paths(root,body.get('gate_id'))['done']
                req=json.loads(resolve_input(path.relative_to(root).as_posix(),root).read_text())
                record=req['approval_record'];kind=req['kind']
                if not receipts.find_approval(root,kind,entity_id=req['entity_id'],record_sha256=record_sha256(record)):
                    raise ValueError('This decision needs your verified signature first. Open its signing card.')
                if kind=='footage_spend':result=await dispatch(root,record['request_id'])
                elif kind=='footage_take':
                    await asyncio.to_thread(footage.apply_decision,root,record['shot_id'],record['take']['take_id'],record['decision'])
                    result={'applied':True}
                elif kind=='audio_resolution':
                    result=await asyncio.to_thread(resolve_charge,root,record['reservation_id'],record['charged'],record['cash_usd'],record['credits_usd'],record['note'])
                else:raise ValueError('This signature is not a footage decision. Return to its own panel.')
            invalidate(project_id);return result
        except HTTPException:raise
        except Exception as exc:raise HTTPException(400,plain_refusal(exc)) from None
