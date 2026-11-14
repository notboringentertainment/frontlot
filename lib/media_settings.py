"""Two price forms update one signed production configuration and ceiling."""
import hashlib,json,math,time
from contextlib import contextmanager
from datetime import datetime,timezone
from lib import gates,receipts,media_spending as media
from lib.project_config import read_project_config,load_current_verified_project_config
from lib.pathsafe import resolve_input,PathSafetyError
from lib.state_io import atomic_write_bytes,atomic_move
from lib.story_ingest import root_checked

SCOPES={'audio:project_config','footage:project_config'}
PAUSED='A price-settings draft is waiting. All purchases are paused until you sign or discard that draft. Current signed figures have not been changed.'

def pending(root):
    # This is a safe read of pending requests, not a story approval check.
    # Each caller retains its own signed production and spending verification.
    from tools.audio._legacy_guard import _checked_path
    root=_checked_path(root);base=root/'.gate-requests'
    try:
        if base.is_symlink():raise PathSafetyError()
        rows=[]
        for path in base.glob('*.json'):
            req=json.loads(resolve_input(path,root).read_text())
            if req.get('kind')=='config' and req.get('project_id')==root.name and req.get('scope') in SCOPES:rows.append((path,req))
        return rows
    except (OSError,ValueError,PathSafetyError):raise ValueError('The saved price requests could not be read safely. Reopen their review before changing prices or buying a take.') from None

def require_no_draft(root):
    if pending(root):raise ValueError(PAUSED)

@contextmanager
def decision_lock(root,req):
    # Same lock order as preparation and sending: call, reservation, approval.
    from tools.cost_tracker import reservation_lock
    if req.get('kind')=='config' and req.get('scope') in SCOPES:
        with media.call_lock(root),reservation_lock(root):yield
    else:yield

def _retire(root,path):
    directory=root/'.gate-requests'/'abandoned'
    if directory.is_symlink():raise ValueError('Saved request history cannot use links.')
    directory.mkdir(exist_ok=True)
    if (directory/path.name).exists():raise ValueError('This request already has a history entry. Reopen its review.')
    atomic_move(path,directory/path.name)

def draft_material(root,req):
    """Read the immutable candidate; an unsigned draft is never current settings."""
    import yaml
    from jsonschema.exceptions import ValidationError
    from lib.project_config import normalize_egress
    from schemas.artifacts import validate_project_config
    root=root_checked(root)
    try:
        sha=req['approval_record']['config_sha256']
        if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError()
        raw=resolve_input('.audio-price-history/'+sha+'.yaml',root).read_bytes()
        if len(raw)>1024*1024 or hashlib.sha256(raw).hexdigest()!=sha:raise ValueError()
        data=yaml.safe_load(raw);validate_project_config(data);normalize_egress(data)
        current,digest=read_project_config(root)
        if digest not in {req['settings_base_sha256'],sha}:
            raise ValueError('The signed settings changed elsewhere. Discard this price draft and prepare it again from the current figures.')
        if digest!=sha:
            # Only this form's owned figures and sending permissions may change.
            own={'version','provider_egress'}|({'audio_price_basis'} if req['scope']=='audio:project_config' else {'footage_price_basis','default_video_endpoint'})
            if {k:v for k,v in current.items() if k not in own}!={k:v for k,v in data.items() if k not in own}:raise ValueError()
            provider='elevenlabs' if req['scope']=='audio:project_config' else 'fal'
            if [v for v in current['provider_egress'] if v['provider']!=provider]!=[v for v in data['provider_egress'] if v['provider']!=provider]:raise ValueError()
        return data,sha,raw
    except (OSError,ValueError,KeyError,TypeError,PathSafetyError,yaml.YAMLError,ValidationError):raise ValueError('The price draft changed or cannot be verified. Discard it and prepare the current figures again. Nothing was approved or bought.') from None

def apply_signed(root,req):
    root=root_checked(root)
    with decision_lock(root,req),gates.receipt_lock(root.name,'approval'):
        data,sha,raw=draft_material(root,req)
        rows=receipts.verified_approvals(root,'config')
        if not rows or rows[-1]['record']!={'config_sha256':sha}:
            raise ValueError('These price settings have no current verified signature. Sign their request first; saving approves nothing.')
        if read_project_config(root)[1]!=sha:atomic_write_bytes(resolve_input('project.yaml',root),raw)

def discard(root,scope=None):
    root=root_checked(root)
    retired=0
    with decision_lock(root,{'kind':'config','scope':'audio:project_config'}),gates.receipt_lock(root.name,'approval'):
        signed=receipts.verified_approvals(root,'config')
        for path,req in pending(root):
            if scope is not None and req['scope']!=scope:continue
            if req.get('approval_receipt_id') or any(row['record']==req.get('approval_record') for row in signed):
                raise ValueError('These settings were signed. Open that signing request to finish applying the recorded decision; it cannot be discarded.')
            _retire(root,path)
            retired+=1
    return {'message':('The unsigned price draft was discarded. Current signed settings and the shared ceiling are unchanged.' if retired else
        'No unsigned price draft is waiting. Current signed settings and the shared ceiling are unchanged.')}

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
    else:entry['content_classes']=list(classes)

def _prepare(root,update,scope,summary):
    from schemas.artifacts import validate_project_config
    from tools.cost_tracker import nonterminal_reservations,reservation_lock
    root=root_checked(root)
    with media.call_lock(root),reservation_lock(root),gates.receipt_lock(root.name,'approval'):
        if nonterminal_reservations(root):raise ValueError('Finish or resolve paid work before changing price settings.')
        signed=receipts.verified_approvals(root,'config')
        superseded=[]
        for path,request in pending(root):
            if request.get('approval_receipt_id') or any(row['record']==request.get('approval_record') for row in signed):raise ValueError('Finish applying the signed settings decision before preparing another draft.')
            if request.get('scope')!=scope:raise ValueError('Finish signing the pending price settings, or discard their draft, before preparing the other form.')
            superseded.append(path)
        if signed:load_current_verified_project_config(root)
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
        digest=hashlib.sha256(raw).hexdigest();candidate=archive/(digest+'.yaml')
        if candidate.is_symlink():raise ValueError('Saved settings history cannot use links.')
        if candidate.exists():
            if candidate.read_bytes()!=raw:raise ValueError('The saved price draft changed. Nothing was replaced.')
        else:atomic_write_bytes(candidate,raw)
        request=media.gate_request(root,'config','project_config',{'config_sha256':digest},summary+' '+PAUSED,scope=scope,
            metadata={'settings_base_sha256':hashlib.sha256(old).hexdigest()})
        for old_request in superseded:
            _retire(root,old_request)
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
