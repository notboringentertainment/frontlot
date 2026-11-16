"""Trailer voice panels adapt existing audio operations; they never sign."""
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from lib import audio_workspace as audio, receipts
from lib.project_config import load_current_verified_project_config
from lib.pathsafe import PathSafetyError
from lib.trailer_contract import signed_trailer_pin
from tools.audio import _governed

MODEL='eleven_multilingual_v2'
_catalogues={}
SIGN_PRICES_FIRST='Sign the price settings first, then load account voices or prepare paid audio. No story was sent.'

def account_status():
    configured=bool(os.environ.get('ELEVENLABS_API_KEY','').strip())
    return {'configured':configured,'message':('Front Lot has an ElevenLabs account key. Load account voices to check access; a saved key does not prove prices or voice rights.' if configured else
        'ElevenLabs is not connected to this board. Ask the person who starts Front Lot to connect the account securely and restart the board, then reload this page. Account connection is not available on this screen.')}

def _price_config(root):
    from lib.project_config import ProjectConfigError
    try:config=load_current_verified_project_config(root)
    except ProjectConfigError:raise ValueError(SIGN_PRICES_FIRST) from None
    if not isinstance(config.data.get('audio_price_basis'),dict):raise ValueError(SIGN_PRICES_FIRST)
    return config

def root_checked(root):
    root=_governed._root(root)
    for name in ('project.json','project.yaml','approvals.jsonl'):audio._checked_path(root/name)
    if signed_trailer_pin(root) is None:
        raise ValueError('Sign this trailer’s creation before preparing voice.')
    return root

def catalogue(root):
    import requests
    root=root_checked(root)
    config=_price_config(root)
    from lib.project_config import ProjectConfigError
    try:config.require_egress('elevenlabs','speech_text')
    except ProjectConfigError:raise ValueError(SIGN_PRICES_FIRST) from None
    key=os.environ.get('ELEVENLABS_API_KEY','').strip()
    account=account_status()
    if not account['configured']:raise ValueError(account['message'])
    try:
        response=requests.get('https://api.elevenlabs.io/v1/voices',headers={'xi-api-key':key},timeout=15,allow_redirects=False)
        response.raise_for_status()
        value=response.json()
        voices=[{'id':v['voice_id'],'name':v['name'][:100]} for v in value['voices']
                if isinstance(v,dict) and isinstance(v.get('voice_id'),str)
                and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',v['voice_id']) and isinstance(v.get('name'),str)]
        if not voices or key in json.dumps(voices):raise ValueError()
    except Exception:
        raise ValueError('The account voices could not be loaded. Check the connection and try Load account voices again. No speech was sent.') from None
    _catalogues[str(root)]=(time.time(),config.digest,hashlib.sha256(key.encode()).hexdigest(),voices)
    return voices

def _gate_id(root,entity,kind):
    from backlot.state import _gate_rows
    rows=_gate_rows(root,root.name,limit=None)
    for row in reversed(rows.get('requests',[])):
        if row.get('entity_id')==entity and row.get('kind')==kind and row.get('state')=='pending':return row['request_id']
    return None

def prepare_voice(root,data):
    root=root_checked(root)
    if not isinstance(data,dict) or set(data)-{'line_index','voice','rights_confirmed','notes'}:
        raise ValueError('Use the saved line and voice controls. Replacement words need a new paper cut.')
    account=account_status()
    if not account['configured']:raise ValueError(account['message'])
    cached=_catalogues.get(str(root));key=os.environ.get('ELEVENLABS_API_KEY','').strip()
    config=_price_config(root)
    if not cached or time.time()-cached[0]>600 or cached[1]!=config.digest or cached[2]!=hashlib.sha256(key.encode()).hexdigest():
        raise ValueError('Load account voices again before preparing this request.')
    selected=next((v for v in cached[3] if v['id']==data.get('voice')),None)
    if selected is None:raise ValueError('Choose a voice from the loaded account list.')
    if data.get('rights_confirmed') is not True:raise ValueError('Confirm you have permission to use the chosen voice before preparing its request.')
    record=audio.prepare_request(root,'speech',MODEL,line_index=data.get('line_index'),voice=selected['id'],
                                 performance_notes=data.get('notes',''),voice_label=selected['name'])
    return {'request':record,'gate_request_id':_gate_id(root,record['request_id'],'audio_spend')}

def send(root,identity):
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    root=root_checked(root)
    account=account_status()
    if not account['configured']:return {'success':False,'message':account['message']}
    result=ElevenLabsTTS().execute({'project_dir':str(root),'audio_request_id':identity})
    return {'success':result.success,**({'take':result.data['take']} if result.success else {'message':result.error+' Review the request and signed settings before trying again.'})}


def recover(root,identity,*,kind='speech'):
    root=root_checked(root)
    with audio.call_lock(root):
        record=_governed.read_request(root,identity)
        _governed.validate_request(record,check_expiry=False)
        if record['kind']!=kind:raise ValueError('Recover this take from its matching audio controls.')
        take=audio.recover_recorded_take(root,record)
        if take is None:raise ValueError('No verified completed take is recorded for this request. Review any held charge; recovery cannot submit it again.')
    return {'take':take,'message':'Recorded take recovered without another call or charge. Listen, then sign keeping or declining it.'}

def decide(root,identity,decision):
    root=root_checked(root)
    _voice_record(root,audio.read(root,identity))
    return audio.request_decision(root,identity,decision)

def _voice_record(root,take):
    if take.get('kind')!='speech' or take.get('origin')!='generated':raise ValueError('Choose a voice take made through an approved voice request.')
    return audio.approved_source_request(root,take)

def playback(root,identity):
    root=root_checked(root);take=audio._decision_material(root,identity,'decline')['take']
    path=audio._checked_path(root/take['path']);path.relative_to(root)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        with os.fdopen(os.dup(fd),'rb') as f:data=f.read(100_000_001)
    finally:os.close(fd)
    if len(data)>100_000_000 or hashlib.sha256(data).hexdigest()!=take['sha256']:
        raise ValueError('This take’s file changed. Review a new take instead.')
    mime={'pcm_s16le':'audio/wav','mp3':'audio/mpeg','aac':'audio/mp4','flac':'audio/flac'}.get(take['codec'],'application/octet-stream')
    return data,mime

def snapshot(root):
    root=root_checked(root)
    from lib.story_ingest import source_warning
    from lib.trailer_edit import paper_state
    paper=paper_state(root)
    settings_error=None;basis=None;config_sha=None
    try:
        config=load_current_verified_project_config(root)
        ceiling=config.budget_usd_cap
        basis=config.data.get('audio_price_basis');config_sha=config.digest
        if basis is None:settings_error=SIGN_PRICES_FIRST
    except RuntimeError:
        from lib.project_config import read_project_config
        draft,_=read_project_config(root)
        # An unsigned draft explains a stop. It grants no spending permission.
        ceiling=draft['budget_usd_cap']
        settings_error=SIGN_PRICES_FIRST
    account=account_status()
    from lib.media_settings import pending,PAUSED
    price_drafts=pending(root)
    if price_drafts:settings_error=PAUSED
    from tools.cost_tracker import CostTracker
    tracker=CostTracker(budget_total_usd=ceiling,reserve_pct=0,cost_log_path=root/'cost_log.json')
    state=audio.state(root)
    takes=[t for t in state['takes'] if t['kind'] in ('speech','music','effect')]
    for take in takes:
        if take['origin']=='generated':
            source=audio.approved_source_request(root,take)
            take['spoken_words']=source['prompt'];take['voice_name']=source['binding'].get('voice_label') or source['voice']
        else:take['spoken_words']=take['licence_note']
    requests=[]
    from tools.cost_tracker import load_reservations
    used={r.get('output_hint',{}).get('request_id') for r in load_reservations(root).values()}
    generations=receipts.verified_generation_receipts(root)
    audio._checked_path(root/'.audio-requests')
    for path in (root/'.audio-requests').glob('*.json'):
        record=_governed.read_request(root,path.stem);_governed.validate_request(record,check_expiry=False)
        if record['kind'] not in ('speech','music','effect'):continue
        from lib.canonical_json import record_sha256
        approval=receipts.find_approval(root,'audio_spend',entity_id=record['request_id'],record_sha256=record_sha256(record))
        approved=approval is not None
        error=None
        made=any(t.get('approved_request_id')==record['request_id'] for t in takes)
        recoverable=False;recovery_error=None
        if not made and approval and any(r.get('audio_approval_receipt_id')==approval['receipt_id'] for r in generations):
            try:recoverable=audio.recorded_take_material(root,record) is not None
            except ValueError as exc:recovery_error=str(exc)
            except (OSError,subprocess.SubprocessError):
                recovery_error='The recorded audio file is missing or cannot be read. Restore its original unchanged file before recovery.'
        if not made:
            try:audio.check_price(root,record)
            except Exception:error='This request needs fresh review. Check the paper cut and signed price settings, then prepare another request.'
            if price_drafts:error=PAUSED
            if record['max_cash_usd']+record['max_credits_usd']>tracker.budget_remaining_usd:
                error='Not enough remains under the production ceiling. Resolve held charges or stop; this request cannot be approved.'
            if record['request_id'] in used:error='This signature has already been used. If no take was recovered, prepare a new spending card; it cannot buy another call.'
            if state['unresolved_requests']:error='Resolve the held audio charge in the account review above before submitting any request.'
            if not account['configured'] and error is None:error=account['message']
        if recoverable:error=None
        elif recovery_error:error=recovery_error
        gate_id=_gate_id(root,record['request_id'],'audio_spend')
        from lib.spending_requests import readiness
        from lib.run_common import read_request as gate_read
        ready=readiness(root,gate_read(root,gate_id)[1]) if gate_id and not approved else {}
        refusal=ready.get('signing_refusal')
        requests.append({'record':record,'approved':approved,'recoverable':recoverable,'error':error,'gate_request_id':gate_id,
            'signing_refusal':refusal,'replaceable':bool(refusal and gate_id and not approved and record['request_id'] not in used),
            'waiting_reason':ready.get('waiting_reason'),
            'source_warning':source_warning(root,record['prompt']) if record['kind']=='speech' else None})
    charges={}
    for r in receipts.verified_generation_receipts(root):
        if r.get('audio_approval_receipt_id'):charges[r['audio_approval_receipt_id']]=r
    for r in receipts.verified_approvals(root,'audio_resolution'):
        record=r['record'];original=record['reservation']['output_hint'].get('audio_approval_receipt_id')
        if not original:continue
        charges.setdefault(original,{'cash_usd':record['cash_usd'],'credits_usd':record['credits_usd']})
    charges=list(charges.values())
    from backlot.music_panel import current_brief
    try:music_brief=current_brief(root)
    except ValueError:music_brief=None
    try:effect_brief=current_brief(root,kind='effect')
    except ValueError:effect_brief=None
    return {'lines':(paper.get('current') or {}).get('paper_cut',{}).get('lines',[]),
            'line_source_warnings':[source_warning(root,line['text']) for line in (paper.get('current') or {}).get('paper_cut',{}).get('lines',[])],
            'paper_review_message':('The saved paper cut is marked for review. Open Cutting workspace, review and save the cut, then return here to check this take again.' if (paper.get('current') or {}).get('paper_cut_pending') else None),
            'music_brief':music_brief,
            'effect_brief':effect_brief,'shots':(paper.get('current') or {}).get('paper_cut',{}).get('shots',[]),
            'account':account,'price_basis':basis,'price_config_sha256':config_sha,'price_settings_signed':basis is not None,'purchases_paused':bool(price_drafts),
            'protected_lines':paper.get('protected_lines',[]),'usable':paper.get('usable_downstream',False),
            'message':settings_error or (None if paper.get('usable_downstream') else 'Review the approved story and save a checked paper cut before requesting audio.'),
            'record_warnings':state['record_warnings'],
            'takes':takes,'requests':requests,'unresolved_requests':state['unresolved_requests'],
            'budget':{'ceiling':ceiling,'spent':tracker.budget_spent_usd,'held':tracker.budget_reserved_usd,
                      'remaining':tracker.budget_remaining_usd,'audio_cash':sum(r.get('cash_usd',0) for r in charges),
                      'audio_credits':sum(r.get('credits_usd',0) for r in charges)}}

def replace_request(root,identity):
    from lib.spending_requests import set_aside
    from lib.media_spending import call_lock
    root=root_checked(root)
    with call_lock(root):
        record=_governed.read_request(root,identity)
        from lib.spending_requests import temporary_wait
        waiting=temporary_wait(root)
        if waiting:raise ValueError(waiting)
        set_aside(root,'audio_spend',identity)
        binding=record['binding']
        fresh=audio.prepare_request(root,record['kind'],record['model'],line_index=binding.get('line_index'),
            brief_id=binding.get('brief_id'),voice=record.get('voice'),
            performance_notes=binding.get('performance_notes',''),voice_label=binding.get('voice_label'))
        return {'request':fresh,'gate_request_id':_gate_id(root,fresh['request_id'],'audio_spend'),
                'message':'A fresh spending card is ready. Review and sign it separately; nothing was bought.'}


def prepare_prices(root,data):
    from lib.media_settings import prepare_audio
    return prepare_audio(root,data)

def prepare_resolution(root,data):
    if not isinstance(data,dict) or set(data)!={'reservation_id','charged','cash','credits','note'}:raise ValueError('Fill in the charge outcome and what you checked in the account.')
    return audio.request_resolution(root_checked(root),data['reservation_id'],data['charged'],data['cash'],data['credits'],data['note'])

def restore_prices(root):
    from lib.media_settings import discard
    return discard(root)

def apply_resolution(root,identity):
    root=root_checked(root)
    from lib.media_spending import apply_signed_resolution
    return apply_signed_resolution(root,identity)

def mount(app,require_writer,safe_root,invalidate):
    import asyncio
    from fastapi import HTTPException,Request
    from fastapi.responses import Response
    async def form(request):
        try:body=await request.json()
        except ValueError:raise HTTPException(400,'This form could not be read. Reload voice and fill in the controls again.') from None
        if not isinstance(body,dict):raise HTTPException(400,'Use the voice form controls and try again.')
        return body
    # Annotation resolution for FastAPI's deferred annotations.
    async def run(project_id,fn,*args):
        try:return await asyncio.to_thread(fn,safe_root(project_id),*args)
        except (ValueError,OSError,RuntimeError,PathSafetyError) as exc:
            message=str(exc) if isinstance(exc,ValueError) else 'Voice could not be read safely. Review the story and signed audio settings, then try again.'
            raise HTTPException(400,message) from None
    @app.get('/api/project/{project_id}/voice')
    async def state(project_id:str):return await run(project_id,snapshot)
    @app.post('/api/project/{project_id}/voice/catalogue')
    async def voices(project_id:str,request:Request):
        require_writer(request);return await run(project_id,catalogue)
    @app.post('/api/project/{project_id}/voice/prepare')
    async def prepare(project_id:str,request:Request):
        require_writer(request);body=await form(request);result=await run(project_id,prepare_voice,body);invalidate(project_id);return result
    @app.post('/api/project/{project_id}/voice/{identity}/replace')
    async def replace(project_id:str,identity:str,request:Request):
        require_writer(request);result=await run(project_id,replace_request,identity);invalidate(project_id);return result
    @app.post('/api/project/{project_id}/voice/prices')
    async def prices(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,prepare_prices,await form(request));invalidate(project_id);return result
    @app.post('/api/project/{project_id}/voice/prices/restore')
    async def restore(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,restore_prices);invalidate(project_id);return result
    @app.post('/api/project/{project_id}/voice/resolution')
    async def resolution(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,prepare_resolution,await form(request));invalidate(project_id);return result
    @app.post('/api/project/{project_id}/voice/resolution/{identity}/apply')
    async def apply(project_id:str,identity:str,request:Request):
        require_writer(request);result=await run(project_id,apply_resolution,identity);invalidate(project_id);return result
    @app.post('/api/project/{project_id}/voice/{identity}/send')
    async def submit(project_id:str,identity:str,request:Request):
        require_writer(request);result=await run(project_id,send,identity);invalidate(project_id);return result
    @app.post('/api/project/{project_id}/voice/{identity}/decision')
    async def decision(project_id:str,identity:str,request:Request):
        require_writer(request);body=await form(request);result=await run(project_id,decide,identity,body.get('decision'));invalidate(project_id);return result
    @app.post('/api/project/{project_id}/voice/{identity}/recover')
    async def recover_take(project_id:str,identity:str,request:Request):
        require_writer(request);result=await run(project_id,recover,identity);invalidate(project_id);return result
    @app.get('/api/project/{project_id}/audio-take/{identity}/play')
    async def play(project_id:str,identity:str):
        data,mime=await run(project_id,playback,identity);return Response(data,media_type=mime,headers={'Cache-Control':'no-store'})
