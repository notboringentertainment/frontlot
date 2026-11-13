"""One purchase lock, budget arithmetic and signed account review for paid media."""
import json
import math
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from lib import receipts
from lib.canonical_json import record_sha256
from lib.config_model import BudgetMode
from lib.project_config import load_current_verified_project_config
from lib.run_common import resolve_project_root
from tools.cost_tracker import CostTracker, IndeterminatePaidCallError

def _money(value):
    if isinstance(value, bool) or not isinstance(value, (float,int)) or not math.isfinite(value) or value < 0 or round(value,4) != value:
        raise ValueError('A firm charge bound in dollars, to four decimal places, is required.')
    return value

def _root(root):
    root=Path(root).resolve()
    if resolve_project_root(root.name)!=root:
        raise ValueError('Paid media requires a registered production.')
    return root

def _tracker(root,config):
    return CostTracker(budget_total_usd=config.budget_usd_cap,reserve_pct=0,
        single_action_approval_usd=float('inf'),require_approval_for_new_paid_tool=False,
        mode=BudgetMode.CAP,cost_log_path=root/'cost_log.json')


def check_quote(record, price, medium):
    """A caller's maximum is not authority; it must equal the signed quote."""
    for key in ('max_cash_usd','max_credits_usd','price_expires_at','price_basis_sha256'):
        if record.get(key)!=price[key]:
            raise ValueError(f'The {medium} price changed. Prepare a new request from signed price figures before approval.')
    return price


def take_decisions(root, kind, identity, take, *, identity_key, subject=None, label='take'):
    """Only verified decisions count, and a decline of this take is final."""
    subject = subject or {}
    records = [row['record'] for row in receipts.verified_approvals(root,kind,entity_id=identity)]
    def same_subject(record):
        return isinstance(record,dict) and all(record.get(k)==v for k,v in subject.items())
    declines = [record for record in records if same_subject(record)
                and record.get('decision')=='decline' and isinstance(record.get('take'),dict)
                and record['take'].get(identity_key)==identity]
    if declines:return declines
    if any(not same_subject(record) or record.get('take')!=take
           or record.get('decision') not in ('accept','decline') for record in records):
        raise ValueError(f'The {label} decision history cannot be verified. Nothing can be used.')
    return records

def gate_request(root,kind,entity,record,summary):
    from lib.run_common import request_paths
    identity='media-'+str(uuid.uuid4())
    directory=Path(root)/'.gate-requests'
    if directory.is_symlink():raise ValueError('Signing requests must not use links.')
    directory.mkdir(exist_ok=True)
    request={'request_id':identity,'kind':kind,'entity_id':entity,'approval_record':record,
             'record_sha256':record_sha256(record),'summary':summary,'stage':'assets','status':'pending','project_id':Path(root).name,'scope':'media:'+entity}
    with request_paths(root,identity)['pending'].open('x') as f:json.dump(request,f,indent=2)
    return request

def reservation_request(root,reservation):
    """Account review uses the original signature, never an editable draft."""
    hint=reservation.get('output_hint') or {}
    footage = bool(hint.get('footage_request_id'))
    kind = 'footage_spend' if footage else 'audio_spend'
    identity = hint.get('footage_request_id') if footage else hint.get('request_id')
    approval_id = hint.get('footage_approval_receipt_id') if footage else hint.get('audio_approval_receipt_id')
    for row in receipts.verified_approvals(root,kind,entity_id=identity):
        record=row['record']
        if footage:
            from lib.footage_requests import tool_inputs
            from tools.base_tool import normalized_inputs_hash
            fingerprint = normalized_inputs_hash(tool_inputs(root,record))
        else:fingerprint = record.get('input_sha256')
        if row['receipt_id']==approval_id and fingerprint==reservation.get('normalized_inputs_hash'):
            return record
    raise ValueError('The original signed request cannot be verified. Nothing was resolved.')


def resolution_record(root,identity,charged,cash,credits,note):
    with call_lock(root,reentrant=False):
        if receipts.verified_approvals(root,'audio_resolution',entity_id=identity):
            raise ValueError('This request already has a signed account outcome. Apply that outcome; nothing was changed.')
        return _resolution_material(root,identity,charged,cash,credits,note)


def _resolution_material(root,identity,charged,cash,credits,note):
    from tools.cost_tracker import load_reservations
    if type(charged) is not bool:raise ValueError('Say whether this request was charged.')
    _money(cash);_money(credits)
    if not isinstance(note,str) or not note.strip():raise ValueError('Record what you checked in the service account.')
    if not charged and (cash or credits):raise ValueError('An uncharged request must have no cash or credit charge.')
    reservation=load_reservations(root).get(identity)
    hint = (reservation or {}).get('output_hint') or {}
    if not reservation or not (hint.get('kind')=='audio' or hint.get('footage_request_id')):
        raise ValueError('Choose an unresolved signed audio or footage request.')
    if reservation['state'] in ('completed','failed'):raise ValueError('This charge has already been resolved.')
    return {'reservation_id':identity,'reservation':reservation,'request':reservation_request(root,reservation),'charged':charged,'cash_usd':cash,'credits_usd':credits,'note':note.strip()}


def resolve_charge(root,identity,charged,cash,credits,note):
    from tools.cost_tracker import reservation_lock,reconcile_paid_call
    with call_lock(root,reentrant=False), reservation_lock(root):
        record=_resolution_material(root,identity,charged,cash,credits,note)
        rows=receipts.verified_approvals(root,'audio_resolution',entity_id=identity)
        if len(rows)>1:
            raise ValueError('This request has conflicting signed account outcomes. Nothing was resolved.')
        approval=receipts.find_approval(root,'audio_resolution',record_sha256=record_sha256(record),entity_id=identity)
        if approval is None:raise ValueError("This resolution needs Ben's signed approval.")
        tracker=_tracker(root,load_current_verified_project_config(root))
        reconcile_paid_call(root,identity,round(cash+credits,4),'completed' if charged else 'failed',tracker)
        return {'message':'The account outcome has been recorded. This approval cannot submit the request again.'}

_call_owners = threading.local()


class PaidCallBusy(ValueError, IndeterminatePaidCallError):
    def __init__(self):
        self.reservations=[]
        ValueError.__init__(self,'An audio or footage operation is still running. Wait for it to finish.')


@contextmanager
def call_lock(root, *, reentrant=True):
    """One live service owner; signed recovery never settles that owner's call."""
    import fcntl
    root = _root(root)
    info = root.stat();identity = (info.st_dev,info.st_ino)
    held = getattr(_call_owners,'held',None)
    if held is None:held=_call_owners.held=set()
    if identity in held:
        if not reentrant:raise PaidCallBusy()
        yield
        return
    path=root/'.audio-call.lock'
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise PaidCallBusy() from None
        held.add(identity)
        try:yield
        finally:held.discard(identity)
    finally:os.close(fd)


def request_resolution(root,identity,charged,cash,credits,note):
    record=resolution_record(root,identity,charged,cash,credits,note)
    return gate_request(root,'audio_resolution',identity,record,'After checking the service account, sign this charge outcome. No request will be submitted again.')

