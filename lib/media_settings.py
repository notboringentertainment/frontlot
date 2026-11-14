"""Two price forms update one signed production configuration and ceiling."""
import hashlib,json,math,time
from datetime import datetime,timezone
from lib import gates,receipts,media_spending as media
from lib.project_config import read_project_config,load_current_verified_project_config
from lib.pathsafe import resolve_input,PathSafetyError
from lib.state_io import atomic_write_bytes,atomic_move
from lib.story_ingest import root_checked

def _expiry(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<=time.time():
        raise ValueError('Choose a future expiry for the account-confirmed price figures.')
    try:datetime.fromtimestamp(value,timezone.utc)
    except (ValueError,OSError,OverflowError):raise ValueError('Choose a valid future calendar date for the price expiry.') from None

def _permissions(settings,provider,classes):
    providers=settings['provider_egress']
    if not isinstance(providers,list):raise ValueError('Review this trailer’s production settings before preparing prices.')
    entry=next((v for v in providers if v['provider']==provider),None)
    if entry is None:providers.append({'provider':provider,'content_classes':classes})
    else:
        for value in classes:
            if value not in entry['content_classes']:entry['content_classes'].append(value)

def _prepare(root,update,scope,summary):
    from schemas.artifacts import validate_project_config
    from tools.cost_tracker import nonterminal_reservations,reservation_lock
    root=root_checked(root)
    with media.call_lock(root),reservation_lock(root),gates.receipt_lock(root.name,'approval'):
        if nonterminal_reservations(root):raise ValueError('Finish or resolve paid work before changing price settings.')
        signed=receipts.verified_approvals(root,'config')
        if signed:load_current_verified_project_config(root)
        base=root/'.gate-requests'
        superseded=[]
        try:
            if base.is_symlink():raise PathSafetyError()
            for path in base.glob('*.json'):
                request=json.loads(resolve_input(path,root).read_text())
                if request.get('kind')=='config' and request.get('project_id')==root.name and not any(row['record']==request.get('approval_record') for row in signed):
                    if request.get('scope')!=scope:raise ValueError('Finish signing the pending price settings, or discard their draft, before preparing the other form.')
                    if request.get('approval_receipt_id') or 'declined_note' in request:raise ValueError('Finish the recorded settings decision before preparing another draft.')
                    superseded.append(path)
        except (PathSafetyError,OSError,json.JSONDecodeError):raise ValueError('The saved settings requests could not be read safely. Reopen their review before changing prices.') from None
        settings,_=read_project_config(root);settings['version']='1.4';update(settings)
        validate_project_config(settings)
        path=resolve_input('project.yaml',root);old=path.read_bytes()
        archive=root/'.audio-price-history'
        if archive.is_symlink():raise ValueError('Saved settings history cannot use links.')
        archive.mkdir(exist_ok=True)
        prior=archive/(hashlib.sha256(old).hexdigest()+'.yaml')
        if prior.is_symlink():raise ValueError('Saved settings history cannot use links.')
        if not prior.exists():atomic_write_bytes(prior,old)
        import yaml
        raw=yaml.safe_dump(settings,sort_keys=True).encode()
        atomic_write_bytes(path,raw)
        request=media.gate_request(root,'config','project_config',{'config_sha256':hashlib.sha256(raw).hexdigest()},summary,scope=scope)
        for old_request in superseded:
            abandoned=base/'abandoned'
            if abandoned.is_symlink():raise ValueError('Saved request history cannot use links.')
            abandoned.mkdir(exist_ok=True)
            if (abandoned/old_request.name).exists():raise ValueError('The earlier settings request already has a history entry. Reopen its review.')
            atomic_move(old_request,abandoned/old_request.name)
        return request

def prepare_audio(root,data):
    keys={'dollars_per_credit','speech_credits_per_character','music_credits_per_minute','voice_multiplier_ceiling','expires_at'}
    if isinstance(data,dict) and 'effect_credits_per_second' in data:keys.add('effect_credits_per_second')
    if not isinstance(data,dict) or set(data)!=keys or any(type(data[k]) not in (int,float) or not math.isfinite(data[k]) or data[k]<=0 for k in keys):
        raise ValueError('Fill in positive account price ceilings and a future expiry. No default prices are supplied.')
    if data['voice_multiplier_ceiling']<1:raise ValueError('The voice multiplier ceiling must be at least one and the price expiry must be in the future.')
    _expiry(data['expires_at'])
    def update(settings):
        settings['audio_price_basis']={k:{'value':data[k],'set_at':time.time(),'expires_at':data['expires_at']} for k in keys-{'expires_at'}}
        _permissions(settings,'elevenlabs',['speech_text','music_prompt']+(['effect_prompt'] if 'effect_credits_per_second' in keys else []))
    return _prepare(root,update,'audio:project_config','Review both saved sets of account prices and permissions, within the one production ceiling. Every purchase still needs its own signature. These settings buy nothing.')

def prepare_footage(root,data):
    from lib.footage_requests import MODEL
    keys={'cash_usd_per_second','credits_usd_per_second','expires_at','allow_prompts','allow_reference_images'}
    if not isinstance(data,dict) or set(data)!=keys:raise ValueError('Fill both Fal price ceilings, their expiry and both sending permissions. No price is pre-filled.')
    for key in ('cash_usd_per_second','credits_usd_per_second'):media._money(data[key])
    if data['cash_usd_per_second']+data['credits_usd_per_second']<=0:raise ValueError('The two price figures must establish a positive maximum charge.')
    _expiry(data['expires_at'])
    if data['allow_prompts'] is not True or data['allow_reference_images'] is not True:
        raise ValueError('Confirm permission to send separately approved prompts and reference pictures to Fal before preparing these settings.')
    def update(settings):
        settings['footage_price_basis']={'provider':'fal','model':MODEL,'cash_usd_per_second':data['cash_usd_per_second'],
            'credits_usd_per_second':data['credits_usd_per_second'],'set_at':time.time(),'expires_at':data['expires_at']}
        settings['default_video_endpoint']=MODEL
        _permissions(settings,'fal',['prompts','reference_images'])
    return _prepare(root,update,'footage:project_config','Review both saved sets of account prices and permissions, including Fal prompts and reference pictures, within the one production ceiling. These settings send nothing and buy nothing.')
