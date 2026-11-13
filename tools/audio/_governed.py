"""One signed audio request, one submission, on the production's existing ledger.

No transport or credential lookup lives here. Audio tools supply their single-call
transport when their controls are built. This boundary never retries or falls back.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lib.canonical_json import record_sha256
from lib.project_config import load_current_verified_project_config
from lib.receipts import find_approval, stage_generation_wal, complete_generation
from lib.state_io import append_jsonl
from tools.cost_tracker import (reservation_lock, reserve_paid_call,
    attach_request_id, reconcile_paid_call, resume_check, load_reservations)

from lib.media_spending import _root, _tracker, _money, call_lock, check_quote

CLASSES = {'speech':'speech_text', 'music':'music_prompt', 'effect':'effect_prompt'}
FIELDS = {'project_id','request_id','provider','kind','model','voice','prompt','duration_seconds',
          'max_cash_usd','max_credits_usd','price_expires_at','input_sha256','config_sha256'}

def _payload(record):
    payload={k:record[k] for k in ('provider','kind','model','voice','prompt','duration_seconds')}
    if record['kind']=='music' and 'binding' in record:payload['force_instrumental']=True
    return payload

def validate_request(record, *, check_expiry=True):
    if not isinstance(record,dict) or not FIELDS <= set(record) or set(record)-FIELDS-{'binding','price_basis_sha256'}:
        raise ValueError('Audio request fields changed or are incomplete.')
    try:
        if str(uuid.UUID(record['request_id']))!=record['request_id']:raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise ValueError('Audio request identity changed.')
    if record['provider']!='elevenlabs' or record['kind'] not in CLASSES:
        raise ValueError('This approval is for ElevenLabs audio only.')
    if not all(isinstance(record[k],str) and record[k].strip() for k in ('model','prompt')):
        raise ValueError('An explicit model and exact words or description are required.')
    if record['kind']=='speech':
        if not isinstance(record['voice'],str) or not record['voice'].strip():
            raise ValueError('Choose a voice before approval.')
    elif record['voice'] is not None:
        raise ValueError('Only speech requests have a voice.')
    length=record['duration_seconds']
    if isinstance(length,bool) or not isinstance(length,(int,float)) or not math.isfinite(length) or not 0 < length <= 120:
        raise ValueError('Audio length must be positive and no more than two minutes.')
    _money(record['max_cash_usd']);_money(record['max_credits_usd'])
    if record['max_cash_usd'] + record['max_credits_usd'] <= 0:
        raise ValueError('A positive upper charge bound is required for audio.')
    expiry=record['price_expires_at']
    if isinstance(expiry,bool) or not isinstance(expiry,(int,float)) or not math.isfinite(expiry):
        raise ValueError('A firm price expiry is required.')
    if check_expiry and expiry <= time.time():raise ValueError('The approved price bound has expired.')
    if record['input_sha256']!=record_sha256(_payload(record)):
        raise ValueError('Audio input fingerprint changed.')
    if not isinstance(record['config_sha256'],str) or len(record['config_sha256'])!=64:
        raise ValueError('Signed project settings are required.')

def prepare(root, *, kind,model,voice,prompt,duration_seconds,max_cash_usd,max_credits_usd,price_expires_at,binding=None,price_basis_sha256=None):
    root=_root(root)
    # Bounds are validated even if the saved settings are damaged.
    _money(max_cash_usd);_money(max_credits_usd)
    config=load_current_verified_project_config(root)
    r=dict(project_id=root.name,request_id=str(uuid.uuid4()),provider='elevenlabs',kind=kind,model=model,voice=voice,prompt=prompt,
           duration_seconds=duration_seconds,max_cash_usd=max_cash_usd,max_credits_usd=max_credits_usd,
           price_expires_at=price_expires_at,config_sha256=config.digest)
    if binding is not None:r['binding']=copy.deepcopy(binding)
    if price_basis_sha256 is not None:r['price_basis_sha256']=price_basis_sha256
    r['input_sha256']=record_sha256(_payload(r))
    # Expired quotes may be saved for inspection but cannot be signed or submitted.
    validate_request(r,check_expiry=False)
    directory=root/'.audio-requests';directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise ValueError('Audio request links are not permitted.')
    with (directory/(r['request_id']+'.json')).open('x') as stream:json.dump(r,stream,sort_keys=True)
    return r

def read_request(root,request_id):
    try:
        if str(uuid.UUID(request_id))!=request_id:raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise ValueError('Invalid audio request identity.')
    root = Path(root)
    descriptors = []
    try:
        if root.is_symlink():
            raise ValueError('Audio request links are not permitted.')
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        directory = os.open('.audio-requests', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        descriptors.append(directory)
        fd = os.open(request_id+'.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        descriptors.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Audio request must be a regular file.')
        with os.fdopen(os.dup(fd), 'r') as stream:
            record = json.load(stream)
        if not isinstance(record, dict):
            raise ValueError('The saved audio request is invalid.')
        if record.get('project_id') != root.name or record.get('request_id') != request_id:
            raise ValueError('The saved audio request belongs to another production.')
        return record
    finally:
        for fd in reversed(descriptors):
            os.close(fd)

def check_current(root,record):
    root=_root(root);validate_request(record)
    from lib.audio_prices import quote
    price=quote(root,record['kind'],record['prompt'],record['duration_seconds'])
    check_quote(record,price,'audio')
    if 'binding' in record:
        from lib.audio_workspace import check_binding
        check_binding(root,record)
    if read_request(root,record['request_id'])!=record:raise ValueError('Saved audio request changed.')
    config=load_current_verified_project_config(root)
    config.require_egress('elevenlabs',CLASSES[record['kind']])
    if config.digest!=record['config_sha256']:raise ValueError('Signed project settings changed after audio approval.')
    return config

def execute(root,record, *, submit,download):
    with call_lock(root):return _execute_locked(root,record,submit=submit,download=download)


def _execute_locked(root,record, *, submit,download):
    """Transport callbacks run only after a durable reservation. Any ambiguous
    failure leaves billing pending; only the existing reconciler may resolve it.
    """
    root=_root(root);record=copy.deepcopy(record)
    with reservation_lock(root):
        config=check_current(root,record)
        approval=find_approval(root,'audio_spend',record_sha256=record_sha256(record),entity_id=record['request_id'])
        if approval is None:raise ValueError('This request needs Ben\'s signed audio approval.')
        for old in load_reservations(root).values():
            if ((old.get('output_hint') or {}).get('request_id')==record['request_id']
                    or (old.get('output_hint') or {}).get('audio_approval_receipt_id')==approval['receipt_id']):
                raise ValueError('This audio approval was already used. It cannot buy another call.')
        resume_check(root)
        tracker=_tracker(root,config)
        bound=round(record['max_cash_usd']+record['max_credits_usd'],4)
        remaining = tracker.budget_remaining_usd
        if bound > remaining:
            from tools.cost_tracker import BudgetExceededError
            raise BudgetExceededError(f'This audio request needs ${bound:.4f}; only ${remaining:.4f} remains.')
        reservation=reserve_paid_call(tracker,root,tool='elevenlabs_audio',endpoint=record['model'],
            normalized_inputs_hash=record['input_sha256'],reserved_usd=bound,
            output_hint={'kind':'audio','audio_approval_receipt_id':approval['receipt_id'],'request_id':record['request_id']})
    started=datetime.now(timezone.utc).isoformat()
    try:
        result=submit(copy.deepcopy(_payload(record)),reservation)
        attach_request_id(root,reservation,result['request_id'])
        cash=_money(result['cash_usd']);credits=_money(result['credits_usd'])
        if cash>record['max_cash_usd'] or credits>record['max_credits_usd']:
            raise ValueError('Reported charge exceeds the approved bound; billing needs review.')
        with reservation_lock(root):
            append_jsonl(root/'cost-reservations.jsonl', {
                'reservation_id': reservation, 'cash_usd': cash, 'credits_usd': credits,
                'audio_approval_receipt_id': approval['receipt_id']})
        data=download(copy.deepcopy(result))
        if not isinstance(data,bytes) or not data:raise ValueError('The audio download is empty or invalid.')
        output=root/'assets'/'audio'/(record['request_id']+'.audio')
        output.parent.mkdir(parents=True,exist_ok=True)
        from lib.pathsafe import staging_file, validate_output_parent
        output = validate_output_parent(output, root)
        staging = staging_file(root, '.audio')
        with staging.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        total=round(cash+credits,4)
        with reservation_lock(root):
            stage_generation_wal(root,execution_id=reservation,
                outputs=[{'staging_path':str(staging),'output_path':str(output),'output_sha256':hashlib.sha256(data).hexdigest()}],
                reservation_id=reservation,actual_usd=total,
                receipt={'tool':'elevenlabs_audio','normalized_inputs_hash':record['input_sha256'],
                    'parameters_hash':record['input_sha256'],'model_endpoint':record['model'],
                    'provider_request_id':result['request_id'],'cost_usd':total,'started_at':started,
                    'audio_charge':{'cash_usd':cash,'credits_usd':credits,'audio_approval_receipt_id':approval['receipt_id']}})
            return complete_generation(root,reservation,tracker=tracker)
    except Exception:
        # Do not settle the tracker or free its reservation on a local failure.
        # A known provider acceptance followed by a bad download is not a refund.
        with reservation_lock(root):
            if load_reservations(root)[reservation]['state'] not in ('completed','failed'):
                reconcile_paid_call(root,reservation,bound,state='pending_billing')
        raise
