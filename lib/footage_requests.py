"""Signed trailer requests on the existing supervised-shot and Kling path."""
from __future__ import annotations
import contextvars
import copy
import hashlib
import json
import math
import os
import stat
import time
import uuid
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from lib import receipts, supervised_production as sp
from lib.canonical_json import record_sha256
from lib.project_config import load_current_verified_project_config, ProjectConfigError, read_project_config
from lib.audio_workspace import gate_request, call_lock
from tools.audio._governed import _root, _tracker, _money
from tools.cost_tracker import reservation_lock, load_reservations, resume_check

MODEL = 'fal-ai/kling-video/o3/pro/reference-to-video'
_AUTH = contextvars.ContextVar('signed_footage_call', default=None)
FIELDS = {'project_id','request_id','shot_id','brief_revision_id','paper_shot','prompt',
          'references','look_refs','look_material','source_snapshots','provider','model',
          'duration_seconds','aspect_ratio','generate_audio','config_sha256','price_basis_sha256',
          'price_expires_at','max_cash_usd','max_credits_usd','remaining_usd','retake_of','input_sha256'}


def validate_request(record):
    if not isinstance(record,dict) or set(record)!=FIELDS:raise ValueError('The footage request fields changed.')
    for key in ('max_cash_usd','max_credits_usd','remaining_usd'):_money(record[key])
    if record['max_cash_usd']+record['max_credits_usd']<=0:raise ValueError('A positive footage price bound is required.')
    if record['provider']!='fal' or record['model']!=MODEL:raise ValueError('The footage service or model changed.')
    if type(record['duration_seconds']) is not int or not 3<=record['duration_seconds']<=15:raise ValueError('The footage length changed.')
    material={k:v for k,v in record.items() if k not in ('request_id','input_sha256')}
    if record['input_sha256']!=record_sha256(material):raise ValueError('The footage input fingerprint changed.')


def _read(root, identity):
    root = _root(root)
    if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
        raise ValueError('Choose a saved footage request.')
    directory = root/'.footage-requests'
    parent = os.open(root, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        folder = os.open(directory.name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=parent)
        try:
            fd = os.open(identity+'.json', os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK, dir_fd=folder)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('Footage requests must be plain files.')
                with os.fdopen(os.dup(fd)) as f:record = json.load(f)
            finally:os.close(fd)
        finally:os.close(folder)
    finally:os.close(parent)
    if (not isinstance(record, dict) or set(record) != FIELDS or record['project_id'] != root.name
            or record['request_id'] != identity):
        raise ValueError('The footage request changed or belongs to another production.')
    material = {k:v for k,v in record.items() if k not in ('request_id','input_sha256')}
    if record['input_sha256'] != record_sha256(material):raise ValueError('The footage input fingerprint changed.')
    validate_request(record)
    return record


def quote(root, model, seconds):
    try:config = load_current_verified_project_config(root)
    except ProjectConfigError:
        raise ValueError('Valid signed footage price figures are missing from the current settings. Sign figures for this service and model before approval.') from None
    basis = config.data.get('footage_price_basis')
    if not isinstance(basis, dict):
        raise ValueError('No footage price figures have been signed. Sign figures for this service and model before approving a request.')
    if model != MODEL or basis.get('provider') != 'fal' or basis.get('model') != model:
        raise ValueError('The signed footage figures do not cover this service and model.')
    if type(seconds) is not int or not 3 <= seconds <= 15:
        raise ValueError('Choose a clip length of three to fifteen whole seconds.')
    start, expiry = basis.get('set_at'), basis.get('expires_at')
    if any(type(x) not in (int,float) or not math.isfinite(x) for x in (start,expiry)) or not start <= time.time() < expiry:
        raise ValueError('The signed footage figures have expired or are not yet valid. Sign current figures before approval.')
    charges = {}
    for source,target in [('cash_usd_per_second','max_cash_usd'),('credits_usd_per_second','max_credits_usd')]:
        value = _money(basis.get(source))
        charges[target] = float((Decimal(str(value))*seconds).quantize(Decimal('.0001'), rounding=ROUND_CEILING))
    if sum(charges.values()) <= 0:raise ValueError('The signed footage figures must establish a positive maximum charge.')
    return {**charges,'price_expires_at':expiry,'price_basis_sha256':record_sha256(basis)}


def tool_inputs(root, record):
    return {'project_dir':str(_root(root)), 'shot_id':record['shot_id'],
        'brief_revision_id':record['brief_revision_id'], 'asset_class':'supervised_shot',
        'prompt':record['prompt'], 'duration':str(record['duration_seconds']),
        'aspect_ratio':record['aspect_ratio'], 'generate_audio':record['generate_audio'],
        'look_refs':copy.deepcopy(record['look_refs']), 'reference_manifest':copy.deepcopy(record['references']),
        'reference_image_paths':[str(Path(root)/r['path']) for r in record['references']],
        'output_path':str(Path(root)/'assets/video'/(record['request_id']+'.mp4')),
        'footage_request_id':record['request_id']}


def _paper_snapshot(root):
    # Atomic edit replacement makes a lock-free read safe. Signers already own
    # the receipt lock; taking the edit lock here would invert the editor's
    # edit-then-receipt order.
    from lib import trailer_edit as edit
    value = edit._read_current(Path(root))
    try:checked = bool(value and 'paper_cut' in value and edit._story_check(Path(root),value['paper_cut'])['status']=='checked')
    except (ValueError,OSError,KeyError,TypeError):checked = False
    # Pending downstream timing still needs review in the cutting view. A new
    # footage signature reviews this exact shot; it does not clear that flag.
    if not checked or value.get('inputs')!=edit._inputs(Path(root)):
        raise ValueError('The paper cut needs a current signed story and a fresh story comparison before footage can be approved.')
    return value


def _sources(root, record):
    state = _paper_snapshot(root)
    brief = sp.read_brief(root, record['shot_id'])
    if not brief or brief['stopped'] or brief['revision_id'] != record['brief_revision_id']:
        raise ValueError('The shot brief changed or stopped. Prepare a new footage request.')
    current = next((s for s in state['paper_cut']['shots'] if s['id']==record['shot_id']),None)
    if current != record['paper_shot'] or brief.get('paper_shot') != record['paper_shot']:
        raise ValueError('The paper cut shot changed. This take needs a fresh look.')
    if (brief['direction'] != record['prompt'] or brief['reference_manifest'] != record['references']
            or brief['look_refs'] != record['look_refs'] or brief['source_snapshots'] != record['source_snapshots']):
        raise ValueError('The description, references, looks or sources changed. Prepare a new footage request.')
    for source in record['source_snapshots']:
        if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('A story or look source changed. Prepare a revised shot brief.')
    for ref in record['references']:
        from lib.pathsafe import resolve_input, sha256_file
        if sha256_file(resolve_input(ref['path'], root)) != ref['asset_id']:
            raise ValueError('A reference picture changed. Nothing can be sent.')
    if not record['references'] or any(not any(r.get('visual_bible_entity_id')==look['entity_id'] for r in record['references']) for look in record['look_refs']):
        raise ValueError('Choose a verified reference picture for every selected character or location before approval.')
    from lib.look_ingest import active_looks, verify_look_refs
    try:verify_look_refs(root, record['look_refs'])
    except ValueError:raise ValueError('The current looks are not approved for generation. Approve them outside these controls first.') from None
    active = active_looks(root)
    material = [{'receipt_id':active[(r['entity_kind'],r['entity_id'])].receipt_id,
                 'payload':active[(r['entity_kind'],r['entity_id'])].payload} for r in record['look_refs']]
    if material != record['look_material']:raise ValueError('The approved look changed. Prepare a new footage request.')
    return brief


def check_current(root, record, *, check_allowance=True, saved=True):
    root = _root(root)
    validate_request(record)
    if saved and _read(root, record['request_id']) != record:raise ValueError('The saved footage request changed.')
    if record['provider'] != 'fal' or record['model'] != MODEL or record['aspect_ratio'] != '16:9' or record['generate_audio'] is not False:
        raise ValueError('The service, model or clip settings changed.')
    price = quote(root, record['model'], record['duration_seconds'])
    if any(record[k] != v for k,v in price.items()):raise ValueError('The signed footage price changed. Prepare a new request.')
    config = load_current_verified_project_config(root)
    if config.digest != record['config_sha256']:raise ValueError('The signed production settings changed.')
    config.require_egress('fal','prompts','reference_images')
    _sources(root, record)
    from tools.video import _shared
    inputs = tool_inputs(root, record)
    governance = _shared.verify_look_governance(inputs, root, media='video')
    refs = [Path(root)/r['path'] for r in record['references']]
    _shared.verify_reference_lineage(inputs, root, refs, governed=governance.get('governed',False))
    _shared.bind_reference_manifest(inputs, root, refs)
    if check_allowance:
        from lib.shot_allowance import resolve
        resolve(root, inputs, kind='video').check_limits(root,
            round(record['max_cash_usd']+record['max_credits_usd'],4), tool='kling_reference_video')
    return config


def prepare_request(root, shot_id, *, duration_seconds, model, retake_of=None):
    root = _root(root)
    if not sp.is_trailer(root):raise ValueError('Use the existing shot route for this older production.')
    from lib.look_ingest import active_looks
    from tools.video.kling_reference_video import KlingReferenceVideo
    with reservation_lock(root):
        resume_check(root)
        _paper_snapshot(root)
        brief = sp.read_brief(root, shot_id)
        if not brief or sp.paper_binding(root, brief)['needs_review']:
            raise ValueError('Prepare the current paper cut shot before requesting footage.')
        price = quote(root, model, duration_seconds)
        config = load_current_verified_project_config(root)
        active = active_looks(root)
        try:
            materials = [{'receipt_id':active[(r['entity_kind'],r['entity_id'])].receipt_id,
                          'payload':active[(r['entity_kind'],r['entity_id'])].payload} for r in brief['look_refs']]
        except KeyError:raise ValueError('This shot has no approved active look. Approve its looks outside these controls first.') from None
        record = {'project_id':root.name,'request_id':str(uuid.uuid4()),'shot_id':shot_id,
            'brief_revision_id':brief['revision_id'],'paper_shot':brief['paper_shot'],'prompt':brief['direction'],
            'references':brief['reference_manifest'],'look_refs':brief['look_refs'],'look_material':materials,
            'source_snapshots':brief['source_snapshots'],'provider':'fal','model':model,
            'duration_seconds':duration_seconds,'aspect_ratio':'16:9','generate_audio':False,
            'config_sha256':config.digest,**price,'remaining_usd':_tracker(root,config).budget_remaining_usd,
            'retake_of':retake_of}
        if retake_of is not None and _read(root,retake_of)['shot_id'] != shot_id:
            raise ValueError('A retake must belong to this same shot.')
        directory = root/'.footage-requests'
        if directory.is_symlink():raise ValueError('Footage requests must stay inside the production.')
        directory.mkdir(exist_ok=True)
        key = lambda r:{k:v for k,v in r.items() if k not in ('request_id','input_sha256','remaining_usd')}
        for path in directory.glob('*.json'):
            old = _read(root,path.stem)
            if key(old) == key(record):return old
        record['input_sha256'] = record_sha256({k:v for k,v in record.items() if k!='request_id'})
        check_current(root,record,saved=False)
        from lib.state_io import atomic_write_json
        atomic_write_json(directory/(record['request_id']+'.json'), record)
        # No network is performed by any of these existing checks.
        check_current(root, record)
        error = KlingReferenceVideo()._preflight_error(tool_inputs(root,record))
        if error:raise ValueError(error)
        return record


def request_approval(root, identity):
    record = _read(root, identity)
    approval_material(root, record)
    return gate_request(root,'footage_spend',identity,record,'Review this one footage request. One signature buys one clip; a retake needs a new request.')


def approval_material(root, record):
    with call_lock(root):return _approval_material(root,record)


def _approval_material(root, record):
    config = check_current(root, record)
    resume_check(root)
    remaining = _tracker(root,config).budget_remaining_usd
    if remaining != record['remaining_usd']:
        raise ValueError('The remaining ceiling changed. Prepare an updated footage request before signing.')
    if round(record['max_cash_usd']+record['max_credits_usd'],4) > remaining:
        raise ValueError('The remaining production ceiling does not cover this footage request.')
    return record


def authorization(root, inputs, estimated_usd=None, tool=None):
    auth = _AUTH.get()
    if not auth or auth['root'] != Path(root).resolve() or inputs != tool_inputs(root,auth['record']):
        raise ValueError('This trailer shot needs Ben’s signed footage request before anything is sent.')
    record = auth['record']
    if tool not in (None,'kling_reference_video'):
        raise ValueError('The signed footage request is for a different model or service.')
    approval = receipts.find_approval(root,'footage_spend',entity_id=record['request_id'],record_sha256=record_sha256(record))
    if not approval or approval['receipt_id'] != auth['approval']['receipt_id']:
        raise ValueError('The signed footage approval cannot be verified.')
    if estimated_usd is not None and estimated_usd != round(record['max_cash_usd']+record['max_credits_usd'],4):
        raise ValueError('The footage charge differs from its signed bound.')
    return auth


def bound_for(inputs):
    auth = _AUTH.get()
    if auth is None:return None
    authorization(auth['root'], inputs)
    return round(auth['record']['max_cash_usd']+auth['record']['max_credits_usd'],4)


def reserve_before_upload(root, tracker, inputs):
    from tools.base_tool import normalized_inputs_hash
    from tools.cost_tracker import reserve_paid_call
    auth = authorization(root, inputs)
    record = auth['record']
    with reservation_lock(root):
        approval_material(root,record)
        if any((r.get('output_hint') or {}).get('footage_request_id') == record['request_id'] for r in load_reservations(root).values()):
            raise ValueError('This footage signature has already been used.')
        reservation = reserve_paid_call(tracker,root,tool='kling_reference_video',endpoint=MODEL,
            normalized_inputs_hash=normalized_inputs_hash(inputs),reserved_usd=bound_for(inputs),
            inputs=inputs,kind='video',output_hint={'kind':'video','output_path':inputs['output_path'],
                'footage_request_id':record['request_id'],'footage_approval_receipt_id':auth['approval']['receipt_id']})
        auth['reservation_id'] = reservation
        return reservation


def frozen_reference(root, inputs, path):
    auth = authorization(root,inputs)
    from lib.pathsafe import sha256_file
    relative = Path(path).relative_to(root).as_posix()
    ref = next(r for r in auth['record']['references'] if r['path']==relative)
    directory = Path(root)/'.footage-frozen'/auth['record']['request_id']
    if directory.parent.is_symlink() or directory.is_symlink():raise ValueError('Reference snapshots cannot use links.')
    directory.mkdir(parents=True,exist_ok=True)
    target = directory/(ref['asset_id']+Path(path).suffix)
    sp._copy(path,target)
    if sha256_file(target) != ref['asset_id']:raise ValueError('The reference changed before upload.')
    return target


def before_submit(root, inputs):
    auth = authorization(root,inputs)
    check_current(root,auth['record'],check_allowance=False)


def actual_charge(inputs, data):
    auth = authorization(_AUTH.get()['root'],inputs)
    cash, credits = _money(data.get('cash_usd')), _money(data.get('credits_usd'))
    if cash > auth['record']['max_cash_usd'] or credits > auth['record']['max_credits_usd']:
        raise ValueError('The reported footage charge exceeds its signed bound. Billing needs signed review.')
    return round(cash+credits,4)


def _take(root, shot_id, take_id):
    take = next((r for r in sp.history(root,shot_id) if r.get('kind')=='take' and r.get('take_id')==take_id),None)
    if take is None:raise ValueError('Choose a saved take for this shot.')
    if take.get('shot_id',shot_id)!=shot_id:raise ValueError('This take belongs to another shot.')
    return take


def _provenance(root, take):
    reservation = load_reservations(root).get(take.get('reservation_id'))
    hint = (reservation or {}).get('output_hint') or {}
    record = _read(root,hint.get('footage_request_id'))
    approval = receipts.find_approval(root,'footage_spend',record_sha256=record_sha256(record),entity_id=record['request_id'])
    from tools.base_tool import normalized_inputs_hash
    found = receipts.find_generation(root,take['sha256'])
    if (not approval or hint.get('footage_approval_receipt_id') != approval['receipt_id']
            or reservation.get('state') != 'completed' or record['shot_id'] != take.get('shot_id')
            or take['brief_revision_id'] != record['brief_revision_id'] or not found
            or found.get('tool') != 'kling_reference_video' or found.get('model_endpoint') != record['model']
            or found.get('provider_request_id') != reservation.get('provider_request_id')
            or found.get('normalized_inputs_hash') != normalized_inputs_hash(tool_inputs(root,record))
            or reservation.get('normalized_inputs_hash') != found.get('normalized_inputs_hash')):
        raise ValueError('The take does not match its signed request and generation receipt.')
    return record


def decision_record(root, shot_id, take_id, decision):
    if decision not in ('accept','decline'):raise ValueError('Accept or decline this take.')
    take = _take(root,shot_id,take_id)
    subject = {'shot_id':shot_id,'take':take,'decision':decision}
    decisions = receipts.verified_approvals(root,'footage_take',entity_id=take_id)
    declined = any(r.get('kind')=='decline' and r.get('take_id')==take_id for r in sp.history(root,shot_id))
    for row in decisions:
        old = row['record']
        if old.get('shot_id') != shot_id or old.get('take') != take or old.get('decision') not in ('accept','decline'):
            raise ValueError('The take decision history cannot be verified.')
        declined = declined or old['decision']=='decline'
    if declined:raise ValueError('This take was declined. Decline is final; request a new take.')
    if decision == 'accept':
        if decisions:raise ValueError('This take is already accepted.')
        record = _provenance(root,take)
        _sources(root,record)
        if hashlib.sha256((Path(root)/take['path']).read_bytes()).hexdigest() != take['sha256']:
            raise ValueError('The take file changed.')
        if not take.get('duration_seconds') or take['duration_seconds'] < record['paper_shot']['duration_frames']/24:
            raise ValueError('This clip is shorter than the paper shot. Revise the shot before accepting.')
    return subject


def request_decision(root, shot_id, take_id, decision):
    record = decision_record(root,shot_id,take_id,decision)
    return gate_request(root,'footage_take',take_id,record,'Watch the whole clip, then sign your '+decision+' decision. This completes no stage.')


def take_state(root, shot_id, take_id):
    take = _take(root,shot_id,take_id)
    rows = receipts.verified_approvals(root,'footage_take',entity_id=take_id)
    if any(r.get('kind')=='decline' and r.get('take_id')==take_id for r in sp.history(root,shot_id)):
        return 'Declined'
    valid = [r['record'] for r in rows if isinstance(r.get('record'),dict) and r['record'].get('shot_id')==shot_id and r['record'].get('take')==take]
    if any(r.get('decision')=='decline' for r in valid):return 'Declined'
    if len(valid)!=len(rows):return 'Needs a fresh look'
    if not valid:return 'Waiting for your decision'
    if any(r.get('decision')!='accept' for r in valid) or len(valid)>1:return 'Needs a fresh look'
    try:
        record = _provenance(root,take);_sources(root,record)
        if hashlib.sha256((Path(root)/take['path']).read_bytes()).hexdigest()!=take['sha256']:return 'Needs a fresh look'
    except (ValueError,OSError,TypeError,KeyError):return 'Needs a fresh look'
    return 'Accepted'


def require_accepted(root, shot_id, take_id):
    if take_state(root,shot_id,take_id) != 'Accepted':raise ValueError('Only an accepted, unchanged footage take can enter the cut.')
    return _take(root,shot_id,take_id)


def apply_decision(root, shot_id, take_id, decision):
    with call_lock(root):return _apply_decision(root,shot_id,take_id,decision)


def _apply_decision(root, shot_id, take_id, decision):
    take = _take(root,shot_id,take_id)
    record = {'shot_id':shot_id,'take':take,'decision':decision}
    if not receipts.find_approval(root,'footage_take',entity_id=take_id,record_sha256=record_sha256(record)):
        raise ValueError('This take decision needs Ben’s signed approval.')
    if decision == 'accept':require_accepted(root,shot_id,take_id)
    elif decision != 'decline':raise ValueError('Accept or decline this take.')
    with reservation_lock(root):
        kind = 'selection' if decision=='accept' else 'decline'
        if not any(r.get('kind')==kind and r.get('take_id')==take_id for r in sp.history(root,shot_id)):
            sp._append(root,shot_id,{'kind':kind,'take_id':take_id,'user_note':'Applied Ben’s signed take decision.'})
    from lib import trailer_edit as edit
    with edit._edit_lock(root):
        old = edit._read_current(root)
        if not old:raise ValueError('The saved paper cut is missing. The signed decision was kept.')
        value = copy.deepcopy(old)
        for picture in value.get('timeline',{}).get('picture',[]):
            if picture.get('id') != shot_id:continue
            if decision=='accept':
                picture.update(kind='footage',take_id=take_id,shot_id=shot_id,path=take['path'],asset_id=take['sha256'],
                    source_in_frame=0,source_out_frame=picture['duration_frames'],source_timebase_fps=24,
                    label='Accepted footage')
            elif picture.get('take_id')==take_id:
                for k in ('take_id','shot_id','path','asset_id','source_in_frame','source_out_frame','source_timebase_fps'):picture.pop(k,None)
                picture.update(kind='placeholder',label='Unfinished placeholder')
        if value == old:return old
        return edit._commit(Path(root),value,old)


def execute(root, identity):
    root = _root(root)
    with call_lock(root):
        resume_check(root)
        record = _read(root,identity)
        approval = receipts.find_approval(root,'footage_spend',entity_id=identity,record_sha256=record_sha256(record))
        if not approval:raise ValueError('This footage request needs Ben’s signed approval.')
        for reservation in load_reservations(root).values():
            hint = reservation.get('output_hint') or {}
            if hint.get('footage_request_id') != identity and hint.get('footage_approval_receipt_id') != approval['receipt_id']:continue
            if reservation['state'] != 'completed':raise ValueError('This signature was already used. A retake needs a new request and signature.')
            return generated_take(root,record,reservation)
        approval_material(root,record)
        auth = {'root':root,'record':record,'approval':approval}
        token = _AUTH.set(auth)
        try:
            from tools.video.kling_reference_video import KlingReferenceVideo
            result = KlingReferenceVideo()._execute(tool_inputs(root,record))
            if not result.success:raise ValueError('The footage request did not finish. Its charge may still be reserved; inspect the request before trying another.')
            return generated_take(root,record,load_reservations(root)[result.data['reservation_id']])
        finally:_AUTH.reset(token)


def generated_take(root, record, reservation):
    with reservation_lock(root):
        for old in sp.history(root,record['shot_id']):
            if old.get('kind')=='take' and old.get('reservation_id')==reservation['reservation_id']:
                return {**old,'status':take_state(root,record['shot_id'],old['take_id'])}
        take = sp.attach(root,record['shot_id'],tool_inputs(root,record)['output_path'],
            user_note='Generated from this signed footage request.',reservation_id=reservation['reservation_id'],
            prompt=record['prompt'],references=record['references'])
        return {**take,'status':take_state(root,record['shot_id'],take['take_id'])}


def provider_entry(inputs):
    if not isinstance(inputs,dict) or set(inputs)-{'project_dir','footage_request_id','output_path'}:
        raise ValueError('Use the saved signed footage request without overriding its material or settings.')
    root = _root(inputs['project_dir']);record = _read(root,inputs['footage_request_id'])
    if inputs.get('output_path') and Path(inputs['output_path']).expanduser().resolve()!=Path(tool_inputs(root,record)['output_path']):
        raise ValueError('The footage output location changed.')
    return execute(root,record['request_id'])
