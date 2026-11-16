"""Observe signing readiness and retire unsigned cards, never their purchases."""
from lib import gates,receipts,media_settings
from lib.run_common import read_request
from lib.canonical_json import record_sha256
from tools.cost_tracker import load_reservations

KINDS={'audio_spend','footage_spend'}

def temporary_wait(root):
    """Observe the live owner without creating a lock or recovering its call."""
    import os,fcntl
    from lib.media_spending import _call_owners
    info=root.stat()
    own=(info.st_dev,info.st_ino) in getattr(_call_owners,'held',set())
    if not own and (root/'.audio-call.lock').exists():
        fd=os.open(root/'.audio-call.lock',os.O_RDONLY|os.O_NOFOLLOW)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError:return 'Another clip or sound take is being made. Wait for it to finish before reviewing this request.'
        finally:os.close(fd)
    if media_settings.pending(root):return media_settings.PAUSED
    from tools.cost_tracker import nonterminal_reservations
    if nonterminal_reservations(root):return 'A charge is held. Finish its signed account review before reviewing another spending request. This card has not been set aside.'
    return None

def readiness(root,request):
    waiting=temporary_wait(root)
    return {'waiting_reason':waiting,'signing_refusal':None if waiting else signing_refusal(root,request)}


def signing_refusal(root,request):
    """Use the signer's own current checks; this never mints a signature."""
    from scripts.gate_approve import construct
    try:
        waiting=temporary_wait(root)
        if waiting:return waiting
        state,saved=read_request(root,request['request_id'])
        if state!='pending':return None
        if (saved.get('project_id')!=root.name or saved.get('kind')!=request.get('kind')
                or saved.get('entity_id')!=request.get('entity_id')):
            raise ValueError('This spending card changed. Reopen its saved request before signing.')
        # Display hints are never authority. The signer also loads this file.
        request=saved
        if request['kind']=='footage_spend':
            from lib import footage_requests
            record=footage_requests._approval_material(root,footage_requests._read(root,request['entity_id']),observation=True)
            if request.get('approval_record')!=record or request.get('record_sha256')!=record_sha256(record):
                raise ValueError('This spending card changed. Reopen its saved request before signing.')
        else:construct(root,request)
    except Exception as exc:
        from tools.cost_tracker import IndeterminatePaidCallError
        if isinstance(exc,IndeterminatePaidCallError):
            return 'A charge is unresolved. Finish its signed account review before signing another spending request.'
        message=str(exc)
        if message=='Signed project settings changed after audio approval.':
            return 'The signed production settings changed after this request was prepared. Prepare a fresh spending card before signing.'
        if '/' in message or '_' in message or not message:
            return 'This request cannot be verified against the current saved material and signed settings. Set it aside and prepare a fresh spending card.'
        return message
    return None


def set_aside(root,kind,identity):
    """Share the signer's lock order so signing and withdrawal cannot race."""
    if kind not in KINDS:raise ValueError('Only an unsigned spending request can be set aside here.')
    from lib.media_settings import _retire
    from lib.pathsafe import resolve_input
    from lib.run_common import request_paths
    import json
    with media_settings.decision_lock(root,{'kind':kind}),gates.receipt_lock(root.name,'approval'):
        # Replay consumed approvals before deciding that a request is unsigned.
        receipts.recover_pending_approvals(root)
        signed=receipts.verified_approvals(root,kind)
        if any(r.get('entity_id')==identity for r in signed):
            raise ValueError('This request was signed. It cannot be set aside as unsigned. Review its take or account outcome instead.')
        for row in load_reservations(root).values():
            hint=row.get('output_hint') or {}
            if identity in (hint.get('request_id'),hint.get('footage_request_id')):
                raise ValueError('This request has a spending record. Review its take or account outcome; it cannot be set aside as unsigned.')
        waiting=temporary_wait(root)
        if waiting:raise ValueError(waiting)
        directory=resolve_input('.gate-requests',root)
        found=[]
        for path in directory.glob('*.json'):
            req=json.loads(resolve_input(path.relative_to(root),root).read_text())
            if req.get('kind')==kind and req.get('entity_id')==identity:
                state,current=read_request(root,req['request_id'])
                if state!='pending' or current!=req or req.get('project_id')!=root.name or req.get('approval_receipt_id'):
                    raise ValueError('This request is not an unsigned waiting card. Reopen its saved review.')
                record=req.get('approval_record')
                if not isinstance(record,dict) or record.get('request_id')!=identity or req.get('record_sha256')!=record_sha256(record):
                    raise ValueError('This spending card cannot be verified. Nothing was set aside.')
                found.append(path)
        if len(found)!=1:raise ValueError('Choose one unsigned waiting spending card to set aside.')
        _retire(root,found[0])
    return {'message':'The unsigned request was set aside. Nothing was sent, spent or approved. Prepare a fresh spending card from the current signed figures.'}
