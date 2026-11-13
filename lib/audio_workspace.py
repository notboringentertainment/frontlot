"""Local voice/music takes. Paid submissions still belong to the audio boundary."""
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from lib.canonical_json import record_sha256
from lib import receipts
from tools.audio._governed import _root
from tools.audio._legacy_guard import _checked_path

TEST_ONLY = 'For the test trailer only until a person at ElevenLabs confirms otherwise in writing.'
PRICE_UNAVAILABLE = 'A firm maximum charge for this account, voice and model is not established. This request cannot be approved.'


def _directory(root):
    root=_root(root)
    directory=root/'.audio-workspace'
    if directory.is_symlink():raise ValueError('Audio records must not use links.')
    directory.mkdir(exist_ok=True)
    return directory


def _save(root,record):
    directory=_directory(root)
    with (directory/(record['id']+'.json')).open('x') as f:json.dump(record,f,ensure_ascii=False,indent=2)
    return record


def read(root,identity):
    if not isinstance(identity,str) or str(uuid.UUID(identity))!=identity:raise ValueError('Invalid audio take.')
    path=_directory(root)/(identity+'.json')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('Audio records must be plain files.')
        with os.fdopen(os.dup(fd)) as f:record=json.load(f)
    finally:os.close(fd)
    if record.get('id')!=identity or record.get('project_id')!=Path(root).name:raise ValueError('Audio belongs to another production.')
    generated=receipts.verified_generation_receipts(root)
    actual=None
    if isinstance(record.get('path'),str):
        local=_checked_path(Path(root)/record['path']);local.relative_to(Path(root).resolve())
        if local.is_file():actual=hashlib.sha256(local.read_bytes()).hexdigest()
    matching=[r for r in generated if r['output_sha256'] in {record.get('sha256'),actual}
              or r['receipt_id']==record.get('generation_receipt_id')]
    approved={r['receipt_id']:r['record'] for r in receipts.verified_approvals(root,'audio_spend')}
    sources=[(r,approved.get(r.get('audio_approval_receipt_id'))) for r in generated]
    sources=[(r,request) for r,request in sources if request and (r['receipt_id']==record.get('generation_receipt_id') or request.get('request_id')==identity)]
    if sources and any(record.get('origin')!='generated' or record.get('kind')!=request['kind'] for r,request in sources):
        raise ValueError('Generated audio provenance cannot be changed by editing its record. Generated music stays marked as test-only.')
    if record.get('origin')=='imported' and matching:
        raise ValueError('Generated audio provenance cannot be changed to imported. Generated music stays marked as test-only.')
    # Release status is fixed by provenance, never editable take metadata.
    if record.get('origin')=='generated' and record.get('kind')=='music':
        record['release_note']=TEST_ONLY
    return record


def line(root,index):
    from lib.trailer_contract import edit_digest
    path=Path(root)/'artifacts/edit_decisions.json'
    _checked_path(path)
    edit=require_checked_paper(root)['current']
    if edit.get('edit_revision_sha256')!=edit_digest(edit):raise ValueError('The saved paper cut cannot be verified.')
    if type(index) is not int or index<0:raise ValueError('Choose a spoken line from the paper cut.')
    rows=edit.get('paper_cut',{}).get('lines',[])
    if index>=len(rows):raise ValueError('That spoken line is no longer in the paper cut.')
    row=rows[index]
    if not isinstance(row.get('text'),str) or not row['text'].strip():raise ValueError('The spoken line needs exact words.')
    if any(type(row.get(k)) is not int for k in ('start_frame','duration_frames')) or row['start_frame']<0 or row['duration_frames']<1:
        raise ValueError('The spoken line needs whole-frame timing.')
    return row


def require_checked_paper(root):
    from lib.trailer_edit import paper_state
    state=paper_state(root)
    if not state.get('usable_downstream'):
        raise ValueError('Audio needs a checked, current paper cut before approval or sending. Review the story and save the paper cut first.')
    return state


def import_music(root,source,origin,licence):
    if not isinstance(licence,str) or not licence.strip():raise ValueError('Write a licence note before importing music.')
    if not isinstance(origin,str) or not origin.strip():raise ValueError('Record where this music came from.')
    raw=Path(source).expanduser()
    if not raw.is_absolute() or '..' in raw.parts:raise ValueError('Choose a direct file path without path tricks or links.')
    path=_checked_path(raw)
    parent=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent);parent=child
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
    finally:os.close(parent)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('Choose a plain audio file, without links.')
        with os.fdopen(os.dup(fd),'rb') as f:data=f.read(100_000_001)
    finally:os.close(fd)
    if not data or len(data)>100_000_000:raise ValueError('Choose an audio file smaller than one hundred megabytes.')
    if any(r['output_sha256']==hashlib.sha256(data).hexdigest() for r in receipts.verified_generation_receipts(root)):
        raise ValueError('This file matches audio generated by this production. Generated music stays marked as test-only and cannot be imported as licensed music.')
    identity=str(uuid.uuid4());directory=_directory(root)
    target=directory/(identity+path.suffix.lower())
    if path.suffix.lower() not in ('.mp3','.wav','.m4a','.aiff','.flac'):raise ValueError('Choose a supported audio file.')
    with target.open('xb') as f:f.write(data)
    measured=probe_audio(target,maximum=None)
    seconds=measured['duration_seconds']
    return _save(root,{'id':identity,'project_id':Path(root).name,'kind':'music','origin':'imported',
        'source':origin.strip(),'licence_note':licence.strip(),'sha256':hashlib.sha256(data).hexdigest(),
        'path':str(target.relative_to(root)),'duration_seconds':seconds,'codec':measured['codec']})


def _take_decisions(root,take):
    """Verify the subject of every signature, not just the ledger's entity label."""
    rows=receipts.verified_approvals(root,'audio_take',entity_id=take['id'])
    records=[row['record'] for row in rows]
    declines=[record for record in records if isinstance(record,dict)
              and record.get('take')==take and record.get('decision')=='decline']
    if declines:return declines
    if any(not isinstance(record,dict) or record.get('decision') not in ('accept','decline')
           or record.get('take')!=take for record in records):
        raise ValueError('The audio decision history cannot be verified. Nothing can be used.')
    return records


def decision_record(root,identity,decision):
    take=read(root,identity)
    records=_take_decisions(root,take)
    if any(record['decision']=='decline' for record in records):
        raise ValueError('This take was declined. Ask for a new take with its own request and approval.')
    if decision=='accept' and records:
        raise ValueError('This take is already accepted. No second acceptance can be signed.')
    return _decision_material(root,identity,decision)


def _decision_material(root,identity,decision):
    if decision not in ('accept','decline'):raise ValueError('Accept or decline this take.')
    take=read(root,identity)
    if take.get('origin')=='generated':
        from tools.audio._governed import read_request
        request=read_request(root,take.get('approved_request_id'))
        approval=receipts.find_approval(root,'audio_spend',record_sha256=record_sha256(request),entity_id=request['request_id'])
        rows=receipts.verified_generation_receipts(root)
        if (not approval or record_sha256(request)!=take.get('approval_record_sha256')
            or take['id']!=request['request_id'] or take['kind']!=request['kind']
            or take.get('binding')!=request.get('binding')
            or take.get('requested_duration_seconds')!=request['duration_seconds']
            or take.get('path')!='assets/audio/'+request['request_id']+'.audio'
            or not any(r['receipt_id']==take.get('generation_receipt_id') and r['output_sha256']==take['sha256']
                and r.get('audio_approval_receipt_id')==approval['receipt_id']
                and r.get('normalized_inputs_hash')==request['input_sha256']
                and r.get('model_endpoint')==request['model'] and r.get('tool')=='elevenlabs_audio' for r in rows)):
            raise ValueError('The generated audio provenance cannot be verified.')
    elif take.get('origin')!='imported':raise ValueError('The audio origin cannot be verified.')
    path=_checked_path(Path(root)/take['path']);path.relative_to(Path(root).resolve())
    if hashlib.sha256(path.read_bytes()).hexdigest()!=take['sha256']:raise ValueError('The audio file changed. Nothing was approved.')
    if take['origin']=='imported' and not take.get('licence_note','').strip():raise ValueError('A licence note is required.')
    if decision=='accept' and take.get('binding') is not None:
        check_binding(root,dict(kind=take['kind'],prompt=binding_source(root,take['binding'])['text'],duration_seconds=take.get('requested_duration_seconds',take['duration_seconds']),binding=take['binding']))
    if decision=='accept' and take.get('line_index') is not None and record_sha256(line(root,take['line_index']))!=take.get('line_sha256'):
        raise ValueError('The spoken line changed. This take needs a fresh look.')
    return {'take':take,'decision':decision}


def request_decision(root,identity,decision):
    record=decision_record(root,identity,decision)
    return gate_request(root,'audio_take',identity,record,'Review this audio take, then sign your decision.')


def gate_request(root,kind,entity,record,summary):
    from lib.run_common import request_paths
    identity='audio-'+str(uuid.uuid4())
    directory=Path(root)/'.gate-requests'
    if directory.is_symlink():raise ValueError('Signing requests must not use links.')
    directory.mkdir(exist_ok=True)
    request={'request_id':identity,'kind':kind,'entity_id':entity,'approval_record':record,
             'record_sha256':record_sha256(record),'summary':summary,'stage':'assets','status':'pending','project_id':Path(root).name,'scope':'audio:'+entity}
    with request_paths(root,identity)['pending'].open('x') as f:json.dump(request,f,indent=2)
    return request


def take_state(root,take):
    try:records=_take_decisions(root,take)
    except ValueError:return 'Needs a fresh look'
    if not records:return 'Waiting for your decision'
    if any(record['decision']=='decline' for record in records):return 'Declined'
    try:current=_decision_material(root,take['id'],'accept')
    except (ValueError,OSError):return 'Needs a fresh look'
    if any(record!=current for record in records):return 'Needs a fresh look'
    return 'Accepted'


def state(root):
    takes=[]
    for path in _directory(root).glob('*.json'):
        take=read(root,path.stem)
        take['status']=take_state(root,take)
        if take['origin']=='generated' and take['kind']=='music':take['release_note']=TEST_ONLY
        takes.append(take)
    from tools.cost_tracker import nonterminal_reservations
    from tools.audio._governed import read_request
    unresolved=[]
    for reservation in nonterminal_reservations(root):
        hint=reservation.get('output_hint') or {}
        if hint.get('kind')!='audio':continue
        try:request=reservation_request(root,reservation)
        except (ValueError,OSError,TypeError):request=None
        unresolved.append({'reservation':reservation,'request':request})
    return {'takes':takes,'unresolved_requests':unresolved}


def reservation_request(root,reservation):
    """Account review uses the original signature, never an editable draft."""
    hint=reservation.get('output_hint') or {}
    for row in receipts.verified_approvals(root,'audio_spend',entity_id=hint.get('request_id')):
        record=row['record']
        if row['receipt_id']==hint.get('audio_approval_receipt_id') and record.get('input_sha256')==reservation.get('normalized_inputs_hash'):
            return record
    raise ValueError('The original signed audio request cannot be verified. Nothing was resolved.')


def resolution_record(root,identity,charged,cash,credits,note):
    if receipts.verified_approvals(root,'audio_resolution',entity_id=identity):
        raise ValueError('This request already has a signed account outcome. Apply that outcome; nothing was changed.')
    return _resolution_material(root,identity,charged,cash,credits,note)


def _resolution_material(root,identity,charged,cash,credits,note):
    from tools.cost_tracker import load_reservations
    from tools.audio._governed import _money
    if type(charged) is not bool:raise ValueError('Say whether this request was charged.')
    _money(cash);_money(credits)
    if not isinstance(note,str) or not note.strip():raise ValueError('Record what you checked in your ElevenLabs account.')
    if not charged and (cash or credits):raise ValueError('An uncharged request must have no cash or credit charge.')
    reservation=load_reservations(root).get(identity)
    if not reservation or (reservation.get('output_hint') or {}).get('kind')!='audio':raise ValueError('Choose an unresolved audio request.')
    if reservation['state'] in ('completed','failed'):raise ValueError('This charge has already been resolved.')
    return {'reservation_id':identity,'reservation':reservation,'request':reservation_request(root,reservation),'charged':charged,'cash_usd':cash,'credits_usd':credits,'note':note.strip()}


def resolve_charge(root,identity,charged,cash,credits,note):
    from tools.cost_tracker import reservation_lock,reconcile_paid_call
    from tools.audio._governed import _tracker
    from lib.project_config import load_current_verified_project_config
    with call_lock(root), reservation_lock(root):
        record=_resolution_material(root,identity,charged,cash,credits,note)
        rows=receipts.verified_approvals(root,'audio_resolution',entity_id=identity)
        if len(rows)>1:
            raise ValueError('This request has conflicting signed account outcomes. Nothing was resolved.')
        approval=receipts.find_approval(root,'audio_resolution',record_sha256=record_sha256(record),entity_id=identity)
        if approval is None:raise ValueError("This resolution needs Ben's signed approval.")
        tracker=_tracker(root,load_current_verified_project_config(root))
        reconcile_paid_call(root,identity,round(cash+credits,4),'completed' if charged else 'failed',tracker)
        return {'message':'The account outcome has been recorded. This approval cannot submit the request again.'}


def save_music_brief(root,text,seconds,identity=None):
    import math
    from lib.state_io import atomic_write_json
    if not isinstance(text,str) or not text.strip():raise ValueError('Write the exact music brief.')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 3<=seconds<=120 or seconds*24!=round(seconds*24):
        raise ValueError('Music needs three seconds to two minutes in whole frames.')
    directory=_directory(root)/'briefs'
    if directory.is_symlink():raise ValueError('Music briefs must not use links.')
    directory.mkdir(exist_ok=True)
    identity=identity or str(uuid.uuid4())
    if str(uuid.UUID(identity))!=identity:raise ValueError('Invalid music brief.')
    path=directory/(identity+'.json')
    if path.is_symlink():raise ValueError('Music briefs must not use links.')
    if path.exists():
        old=path.read_bytes();archive=directory/(identity+'.'+hashlib.sha256(old).hexdigest()+'.history')
        if not archive.exists():
            with archive.open('xb') as f:f.write(old)
    value={'text':text,'duration_seconds':seconds,'instrumental':True}
    atomic_write_json(path,value)
    return identity


def binding_source(root,binding):
    if not isinstance(binding,dict):raise ValueError('This audio needs its exact spoken line or music brief.')
    if binding.get('kind')=='speech':return line(root,binding.get('line_index'))
    if binding.get('kind')!='music':raise ValueError('Unknown audio source.')
    marker_path=Path(root)/'project.json'
    marker=json.loads(marker_path.read_text()) if marker_path.exists() else {}
    from lib.trailer_contract import signed_trailer_pin,checkpoint_trailer_evidence
    if ((Path(root)/'artifacts/edit_decisions.json').exists() or marker.get('production_kind')=='trailer'
        or marker.get('pipeline_type')=='authored-film@1.6' or signed_trailer_pin(root) or checkpoint_trailer_evidence(root)):
        require_checked_paper(root)
    identity=binding.get('brief_id')
    if not isinstance(identity,str) or str(uuid.UUID(identity))!=identity:raise ValueError('Invalid music brief.')
    path=_checked_path(_directory(root)/'briefs'/(identity+'.json'))
    value=json.loads(path.read_text())
    import math
    if (not isinstance(value,dict) or value.get('instrumental') is not True or not isinstance(value.get('text'),str) or not value['text'].strip()
        or type(value.get('duration_seconds')) not in (int,float) or not math.isfinite(value['duration_seconds'])
        or not 3<=value['duration_seconds']<=120 or value['duration_seconds']*24!=round(value['duration_seconds']*24)):
        raise ValueError('Music needs an instrumental brief and whole-frame timing.')
    return value


def check_binding(root,record):
    binding=record.get('binding')
    current=binding_source(root,binding)
    if record_sha256(current)!=binding.get('source_sha256'):
        raise ValueError('The spoken line or music brief changed. This take needs a fresh look.')
    prompt=current.get('text');seconds=current.get('duration_frames',0)/24 if binding['kind']=='speech' else current.get('duration_seconds')
    if record['kind']!=binding['kind'] or record['prompt']!=prompt or record['duration_seconds']!=seconds:
        raise ValueError('The request no longer matches its exact line or brief.')
    return current


def prepare_request(root,kind,model,*,line_index=None,voice=None,brief_id=None,performance_notes='',voice_label=None):
    from lib.audio_prices import quote
    from tools.audio import _governed
    import re
    if not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',model):raise ValueError('Choose an explicit audio model.')
    if kind=='speech' and (not isinstance(voice,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',voice)):
        raise ValueError('Choose an explicit voice from the intended account.')
    binding={'kind':kind}
    if kind=='speech':binding['line_index']=line_index
    elif kind=='music':binding['brief_id']=brief_id
    else:raise ValueError('Choose speech or instrumental music.')
    source=binding_source(root,binding);binding['source_sha256']=record_sha256(source)
    if not isinstance(performance_notes,str) or len(performance_notes)>2000:
        raise ValueError('Keep pronunciation and delivery notes under two thousand characters.')
    if performance_notes or voice_label is not None:
        binding.update(performance_notes=performance_notes,voice_label=voice_label,voice_rights_confirmed=True)
    seconds=source['duration_frames']/24 if kind=='speech' else source['duration_seconds']
    price=quote(root,kind,source['text'],seconds)
    record=_governed.prepare(root,kind=kind,model=model,voice=voice,prompt=source['text'],duration_seconds=seconds,
        max_cash_usd=price['max_cash_usd'],max_credits_usd=price['max_credits_usd'],price_expires_at=price['price_expires_at'],
        binding=binding,price_basis_sha256=price['price_basis_sha256'])
    gate_request(root,'audio_spend',record['request_id'],record,'Review this individual audio request. '+(TEST_ONLY if kind=='music' else 'One signature buys one call.'))
    return record


def check_price(root,record):
    from lib.audio_prices import quote
    price=quote(root,record['kind'],record['prompt'],record['duration_seconds'])
    for key in ('max_cash_usd','max_credits_usd','price_expires_at','price_basis_sha256'):
        if record.get(key)!=price[key]:raise ValueError('The audio price changed. Prepare a new request before approval.')
    check_binding(root,record)
    return price


def generated_take(root,record,row):
    identity=record['request_id']
    directory=_directory(root)
    if (directory/(identity+'.json')).exists():return read(root,identity)
    path=Path(root)/'assets/audio'/(identity+'.audio')
    _checked_path(path)
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if sha!=row['output_sha256']:raise ValueError('The saved audio no longer matches its receipt.')
    measured=probe_audio(path)
    take={'id':identity,'project_id':Path(root).name,'kind':record['kind'],'origin':'generated',
          'approved_request_id':identity,'approval_record_sha256':record_sha256(record),
          'generation_receipt_id':row['receipt_id'],'sha256':sha,'path':str(path.relative_to(root)),
          'duration_seconds':measured['duration_seconds'],'requested_duration_seconds':record['duration_seconds'],'codec':measured['codec'],'binding':record['binding']}
    if record['kind']=='music':take['release_note']=TEST_ONLY
    return _save(root,take)


def require_accepted(root,identity):
    take=read(root,identity)
    if take_state(root,take)!='Accepted':raise ValueError('Only an accepted, unchanged audio take can be used.')
    return take

def approved_source_request(root,take):
    row=receipts.find_approval(root,'audio_spend',entity_id=take['id'],record_sha256=take['approval_record_sha256'])
    if row is None:raise ValueError('The audio request cannot be verified. Stop and have its approval history checked.')
    return row['record']


from contextlib import contextmanager
@contextmanager
def call_lock(root):
    """One live service owner; signed recovery never settles that owner's call."""
    import fcntl
    path=_root(root)/'.audio-call.lock'
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('An audio call is still running. Wait for its result before account review.')
        yield
    finally:os.close(fd)


def request_resolution(root,identity,charged,cash,credits,note):
    with call_lock(root):
        record=resolution_record(root,identity,charged,cash,credits,note)
        return gate_request(root,'audio_resolution',identity,record,'After checking your ElevenLabs account, sign this charge outcome. No request will be submitted again.')


def probe_audio(path,maximum=120):
    import math,subprocess
    r=subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries','stream=codec_name:format=duration','-of','json',str(path)],capture_output=True,text=True,check=True,timeout=15)
    info=json.loads(r.stdout);seconds=float(info['format']['duration'])
    if not info.get('streams') or not math.isfinite(seconds) or seconds<=0 or (maximum is not None and seconds>maximum):
        raise ValueError('The take is not supported playable audio within its duration limit.')
    return {'duration_seconds':seconds,'codec':info['streams'][0]['codec_name']}
