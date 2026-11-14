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
from lib.media_spending import gate_request, call_lock, _root, _tracker, _money, take_decisions, check_quote
from tools.cost_tracker import reservation_lock, load_reservations, resume_check

MODEL = 'fal-ai/kling-video/o3/pro/reference-to-video'
_AUTH = contextvars.ContextVar('signed_footage_call', default=None)
FIELDS = {'project_id','request_id','shot_id','brief_revision_id','paper_shot','prompt',
          'references','look_refs','look_material','source_snapshots','provider','model',
          'duration_seconds','aspect_ratio','generate_audio','config_sha256','price_basis_sha256',
          'price_expires_at','max_cash_usd','max_credits_usd','remaining_usd','retake_of','input_sha256'}


def require_trailer(root):
    """Display evidence is never authority for a paid trailer request."""
    from lib.trailer_contract import signed_trailer_pin, require_contract
    try:
        if signed_trailer_pin(root) is None:raise ValueError()
        require_contract(root)
    except (ValueError, OSError, KeyError, TypeError, receipts.ReceiptChainError):
        raise ValueError('A footage request requires a verified trailer with signed creation consent. Nothing was sent.') from None


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
    from lib.look_ingest import active_looks, verify_look_refs, LookIngestError
    try:
        verify_look_refs(root, record['look_refs'])
        active = active_looks(root)
        material = [{'receipt_id':active[(r['entity_kind'],r['entity_id'])].receipt_id,
                     'payload':active[(r['entity_kind'],r['entity_id'])].payload} for r in record['look_refs']]
    except (ValueError,LookIngestError,KeyError):raise ValueError('The current looks changed or are no longer approved. This take needs a fresh look; review the current looks before a new request.') from None
    if material != record['look_material']:raise ValueError('The approved look changed. Prepare a new footage request.')
    return brief


def check_current(root, record, *, check_allowance=True, saved=True):
    root = _root(root)
    require_trailer(root)
    validate_request(record)
    if saved and _read(root, record['request_id']) != record:raise ValueError('The saved footage request changed.')
    if record['provider'] != 'fal' or record['model'] != MODEL or record['aspect_ratio'] != '16:9' or record['generate_audio'] is not False:
        raise ValueError('The service, model or clip settings changed.')
    price = quote(root, record['model'], record['duration_seconds'])
    check_quote(record,price,'footage')
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
        from lib.shot_allowance import ShotGuard
        ShotGuard(record['shot_id'],'video',inputs).check_limits(root,
            round(record['max_cash_usd']+record['max_credits_usd'],4), tool='kling_reference_video')
    return config


def prepare_request(root, shot_id, *, duration_seconds, model, retake_of=None):
    root = _root(root)
    require_trailer(root)
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
        key = lambda r:{k:v for k,v in r.items() if k not in ('request_id','input_sha256')}
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
    require_trailer(root)
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


def mark_upload_contact(root,inputs):
    """Private live-call evidence, never an input flag or a browser assertion."""
    authorization(root,inputs)['external_contact_started']=True


def cancel_before_contact(root,inputs,tracker):
    """Receipt a proven local failure. Any attempted upload forbids this path."""
    from lib import gates
    from lib.state_io import append_jsonl
    from tools.cost_tracker import reservation_lock,load_reservations,reconcile_paid_call
    auth=_AUTH.get()
    if not auth or auth['root']!=root or auth.get('external_contact_started'):return False
    identity=auth.get('reservation_id')
    with reservation_lock(root):
        row=load_reservations(root).get(identity)
        if not row or row['state']!='submitting' or row.get('provider_request_id'):return False
        if row.get('output_hint',{}).get('footage_request_id')!=auth['record']['request_id']:return False
        receipt={'receipt_id':str(uuid.uuid4()),'project_id':root.name,
                 'kind':'footage_not_submitted','request_id':auth['record']['request_id'],
                 'reservation_id':identity,'actual_usd':0,'recorded_at':time.time(),
                 'approval_receipt_id':auth['approval']['receipt_id'],
                 'input_sha256':auth['record']['input_sha256'],
                 'reason':'Local failure before any external contact.'}
        receipt['signature']=gates.sign_receipt(receipt)
        # Evidence must be durable before money is released. A receipt-writing
        # failure leaves the hold and the one-use reservation in place.
        append_jsonl(root/'cost-reservations.jsonl',{'reservation_id':identity,'not_submitted_receipt':receipt})
        reconcile_paid_call(root,identity,0,'failed',tracker)
        auth['not_submitted_receipt']=receipt
    return True


def actual_charge(inputs, data):
    auth = authorization(_AUTH.get()['root'],inputs)
    # Fal's ordinary result is a clip, not an invoice. Book the full signed
    # maximum conservatively, explicitly distinguished from a reported charge.
    reported = 'cash_usd' in data or 'credits_usd' in data
    cash = _money(data.get('cash_usd')) if reported else auth['record']['max_cash_usd']
    credits = _money(data.get('credits_usd')) if reported else auth['record']['max_credits_usd']
    if cash > auth['record']['max_cash_usd'] or credits > auth['record']['max_credits_usd']:
        raise ValueError('The reported footage charge exceeds its signed bound. Billing needs signed review.')
    auth['charge']={'cash_usd':cash,'credits_usd':credits,
        'footage_charge_basis':'service_report' if reported else 'signed_maximum',
        'footage_approval_receipt_id':auth['approval']['receipt_id'],'footage_request_id':auth['record']['request_id']}
    return round(cash+credits,4)


def receipt_charge(root,inputs):
    return dict(authorization(root,inputs)['charge'])


def _take(root, shot_id, take_id):
    take = next((r for r in sp.history(root,shot_id) if r.get('kind')=='take' and r.get('take_id')==take_id),None)
    if take is None:raise ValueError('Choose a saved take for this shot.')
    if take.get('shot_id',shot_id)!=shot_id:raise ValueError('This take belongs to another shot.')
    return take


def _provenance(root, take):
    reservation = load_reservations(root).get(take.get('reservation_id'))
    hint = (reservation or {}).get('output_hint') or {}
    if not isinstance(hint.get('footage_request_id'),str):
        raise ValueError('This take has no signed footage request and matching generated clip. It cannot be accepted as trailer footage.')
    record = _read(root,hint.get('footage_request_id'))
    approval = receipts.find_approval(root,'footage_spend',record_sha256=record_sha256(record),entity_id=record['request_id'])
    from tools.base_tool import normalized_inputs_hash
    found = receipts.find_generation_by_id(root,take.get('generation_receipt_id'),output_sha256=take['sha256'])
    if (not approval or hint.get('footage_approval_receipt_id') != approval['receipt_id']
            or reservation.get('state') != 'completed' or record['shot_id'] != take.get('shot_id')
            or take['brief_revision_id'] != record['brief_revision_id'] or not found
            or take['take_id'] != record['request_id'] or take.get('footage_request_id') != record['request_id']
            or found.get('footage_request_id') != record['request_id'] or found.get('footage_approval_receipt_id') != approval['receipt_id']
            or found.get('tool') != 'kling_reference_video' or found.get('model_endpoint') != record['model']
            or found.get('provider_request_id') != reservation.get('provider_request_id')
            or found.get('normalized_inputs_hash') != normalized_inputs_hash(tool_inputs(root,record))
            or reservation.get('normalized_inputs_hash') != found.get('normalized_inputs_hash')):
        raise ValueError('The take does not match its signed request and generation receipt.')
    expected=f"production/shots/{record['shot_id']}/takes/{take['sha256']}.mp4"
    if (take.get('path')!=expected or take.get('prompt')!=record['prompt'] or take.get('references')!=record['references']
            or take.get('cost_usd')!=found['cost_usd'] or take.get('cost_basis')!=found.get('footage_charge_basis')):
        raise ValueError('The take description, references, file or booked charge changed. It cannot be accepted.')
    from lib.pathsafe import resolve_input, PathSafetyError, sha256_file
    from tools.video._shared import verify_video_file
    try:
        path=resolve_input(take['path'],root)
        if sha256_file(path)!=take['sha256']:raise ValueError('The take file changed.')
        measured=verify_video_file(path)['duration_seconds']
    except (PathSafetyError,OSError) as exc:
        raise ValueError('The take file is missing, linked or outside this production.') from exc
    if take.get('duration_seconds')!=measured:raise ValueError('The saved take length differs from the clip. It cannot be accepted.')
    return record


def decision_record(root, shot_id, take_id, decision):
    if decision not in ('accept','decline'):raise ValueError('Accept or decline this take.')
    take = _take(root,shot_id,take_id)
    subject = {'shot_id':shot_id,'take':take,'decision':decision}
    decisions = take_decisions(root,'footage_take',take_id,take,identity_key='take_id',subject={'shot_id':shot_id})
    if any(old['decision']=='decline' for old in decisions):
        raise ValueError('This take was declined. Decline is final; request a new take.')
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
    try:valid = take_decisions(root,'footage_take',take_id,take,identity_key='take_id',subject={'shot_id':shot_id})
    except (receipts.ReceiptChainError,ValueError):return 'Needs a fresh look'
    if any(r.get('decision')=='decline' for r in valid):return 'Declined'
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


def _window(picture,take):
    fps=picture.get('source_timebase_fps',24)
    start=picture.get('source_in_frame',0)
    end=picture.get('source_out_frame',start+picture['duration_frames'])
    if (any(type(x) is not int for x in (fps,start,end)) or fps<=0 or start<0 or end<=start
            or end/fps>take['duration_seconds']+1e-6):
        raise ValueError('This take is too short for the saved trim window. Adjust the cut; the signed decision and previous version are kept.')
    return {'source_timebase_fps':fps,'source_in_frame':start,'source_out_frame':end}


def validate_cut_sources(root,value):
    """Later preview/export and stage signing use the saved timeline, never labels."""
    for picture in value.get('timeline',{}).get('picture',[]):
        if picture.get('kind')!='footage':continue
        take=require_accepted(root,picture.get('shot_id'),picture.get('take_id'))
        if picture.get('path')!=take['path'] or picture.get('asset_id')!=take['sha256']:
            raise ValueError('The cut does not match its accepted footage take.')
        _window(picture,take)


def set_aside_declined(root,value):
    """A restored edit preserves history without making a declined take usable."""
    if value.get('cutting_view'):
        return  # The editor displays the original placement with a live refusal.
    for picture in value.get('timeline',{}).get('picture',[]):
        if picture.get('kind')!='footage':continue
        try:declined=take_state(root,picture.get('shot_id'),picture.get('take_id'))=='Declined'
        except (ValueError,OSError,TypeError,KeyError):declined=False
        if not declined:continue
        value.setdefault('set_aside_footage',[]).append({'picture':copy.deepcopy(picture),
            'reason':'This take was declined. Its original placement remains in the previous saved version.'})
        for key in ('take_id','shot_id','path','asset_id','source_in_frame','source_out_frame','source_timebase_fps'):
            picture.pop(key,None)
        picture.update(kind='placeholder',label='Unfinished placeholder — take declined')
        value.update(unfinished=True,paper_cut_pending=True,
            timing_message='A declined take was set aside. Its placement is kept in the previous saved version. Downstream work needs a fresh look.')


def preview_availability(root,value):
    result={}
    for picture in (value or {}).get('timeline',{}).get('picture',[]):
        if picture.get('kind')!='footage':continue
        try:
            validate_cut_sources(root,{'timeline':{'picture':[picture]}})
            result[picture['id']]='Accepted footage available'
        except (ValueError,OSError,TypeError,KeyError):result[picture.get('id')]='Needs a fresh look — placeholder'
    return result


def apply_decision(root, shot_id, take_id, decision):
    with call_lock(root):return _apply_decision(root,shot_id,take_id,decision)


def _apply_decision(root, shot_id, take_id, decision):
    take = _take(root,shot_id,take_id)
    record = {'shot_id':shot_id,'take':take,'decision':decision}
    if decision=='accept' and take_state(root,shot_id,take_id)=='Declined':
        raise ValueError('This take was declined. Decline is final; request a new take.')
    if not receipts.find_approval(root,'footage_take',entity_id=take_id,record_sha256=record_sha256(record)):
        raise ValueError('This take decision needs your signature first. Nothing was decided.')
    if decision == 'accept':require_accepted(root,shot_id,take_id)
    elif decision != 'decline':raise ValueError('Accept or decline this take.')
    from lib import trailer_edit as edit
    with edit._edit_lock(root):
        old = edit._read_current(root)
        if not old:raise ValueError('The saved paper cut is missing. The signed decision was kept.')
        if old.get('cutting_view'):
            return old  # Decisions change usability, never the writer's placements.
        value = copy.deepcopy(old)
        for picture in value.get('timeline',{}).get('picture',[]):
            if picture.get('id') != shot_id:continue
            if decision=='accept':
                window=_window(picture,take)
                picture.update(kind='footage',take_id=take_id,shot_id=shot_id,path=take['path'],asset_id=take['sha256'],
                    **window,
                    label='Accepted footage')
            elif picture.get('take_id')==take_id:
                for k in ('take_id','shot_id','path','asset_id','source_in_frame','source_out_frame','source_timebase_fps'):picture.pop(k,None)
                picture.update(kind='placeholder',label='Unfinished placeholder')
        if value == old:return old
        value.update(unfinished=True,paper_cut_pending=True,
            timing_message='The footage choice changed. Existing timing and sound were kept; downstream work needs a fresh look. The earlier choice remains in saved versions.')
        return edit._commit(Path(root),value,old)


def execute(root, identity):
    root = _root(root)
    require_trailer(root)
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
            if not result.success:
                if auth.get('not_submitted_receipt'):
                    raise ValueError('This request was not submitted or charged. Its hold was released and a receipt was saved. Prepare and sign a new request for another attempt.')
                if auth.get('external_contact_started'):
                    raise ValueError('An upload or footage request did not finish. Its outcome is uncertain, so the money is held. Check the service account and sign its outcome before another request.')
                if auth.get('reservation_id'):
                    raise ValueError('This request did not finish and its cancellation could not be recorded. The money stays held. Check the service account and sign its outcome before another request.')
                raise ValueError('The footage request did not finish. Check its recorded reservation and signed settings before preparing another request.')
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
