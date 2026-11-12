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
    return record


def line(root,index):
    from lib.trailer_contract import edit_digest
    path=Path(root)/'artifacts/edit_decisions.json'
    _checked_path(path)
    edit=json.loads(path.read_text())
    if edit.get('edit_revision_sha256')!=edit_digest(edit):raise ValueError('The saved paper cut cannot be verified.')
    if type(index) is not int or index<0:raise ValueError('Choose a spoken line from the paper cut.')
    rows=edit.get('paper_cut',{}).get('lines',[])
    if index>=len(rows):raise ValueError('That spoken line is no longer in the paper cut.')
    row=rows[index]
    if not isinstance(row.get('text'),str) or not row['text'].strip():raise ValueError('The spoken line needs exact words.')
    if any(type(row.get(k)) is not int for k in ('start_frame','duration_frames')) or row['start_frame']<0 or row['duration_frames']<1:
        raise ValueError('The spoken line needs whole-frame timing.')
    return row


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
    identity=str(uuid.uuid4());directory=_directory(root)
    target=directory/(identity+path.suffix.lower())
    if path.suffix.lower() not in ('.mp3','.wav','.m4a','.aiff','.flac'):raise ValueError('Choose a supported audio file.')
    with target.open('xb') as f:f.write(data)
    # Probe the saved bytes, never an unconfined path supplied by a page.
    import subprocess
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(target)],capture_output=True,text=True,check=True,timeout=15)
    seconds=float(json.loads(result.stdout)['format']['duration'])
    if not __import__('math').isfinite(seconds) or not 0<seconds<=120:raise ValueError('Music must last no more than two minutes.')
    return _save(root,{'id':identity,'project_id':Path(root).name,'kind':'music','origin':'imported',
        'source':origin.strip(),'licence_note':licence.strip(),'sha256':hashlib.sha256(data).hexdigest(),
        'path':str(target.relative_to(root)),'duration_seconds':seconds})


def decision_record(root,identity,decision):
    if decision not in ('accept','decline'):raise ValueError('Accept or decline this take.')
    take=read(root,identity)
    if take.get('origin')=='generated':
        from tools.audio._governed import read_request
        request=read_request(root,take.get('approved_request_id'))
        approval=receipts.find_approval(root,'audio_spend',record_sha256=record_sha256(request),entity_id=request['request_id'])
        rows=receipts.verified_generation_receipts(root)
        if not approval or record_sha256(request)!=take.get('approval_record_sha256') or not any(
                r['receipt_id']==take.get('generation_receipt_id') and r['output_sha256']==take['sha256'] and r.get('audio_approval_receipt_id')==approval['receipt_id'] for r in rows):
            raise ValueError('The generated audio provenance cannot be verified.')
    elif take.get('origin')!='imported':raise ValueError('The audio origin cannot be verified.')
    path=_checked_path(Path(root)/take['path']);path.relative_to(Path(root).resolve())
    if hashlib.sha256(path.read_bytes()).hexdigest()!=take['sha256']:raise ValueError('The audio file changed. Nothing was approved.')
    if take['origin']=='imported' and not take.get('licence_note','').strip():raise ValueError('A licence note is required.')
    if take.get('binding') is not None:
        check_binding(root,dict(kind=take['kind'],prompt=binding_source(root,take['binding'])['text'],duration_seconds=take['duration_seconds'],binding=take['binding']))
    if take.get('line_index') is not None and record_sha256(line(root,take['line_index']))!=take.get('line_sha256'):
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
    rows=receipts.verified_approvals(root,'audio_take',entity_id=take['id'])
    if not rows:return 'Waiting for your decision'
    newest=rows[-1]['record']
    try:current=decision_record(root,take['id'],newest['decision'])
    except (ValueError,OSError):return 'Needs a fresh look'
    if newest!=current:return 'Needs a fresh look'
    return 'Accepted' if newest['decision']=='accept' else 'Declined'


def state(root):
    takes=[]
    for path in _directory(root).glob('*.json'):
        take=read(root,path.stem)
        take['status']=take_state(root,take)
        if take['origin']=='generated' and take['kind']=='music':take['release_note']=TEST_ONLY
        takes.append(take)
    return {'takes':takes,'price_message':PRICE_UNAVAILABLE}


def resolution_record(root,identity,charged,cash,credits,note):
    from tools.cost_tracker import load_reservations
    from tools.audio._governed import _money
    if type(charged) is not bool:raise ValueError('Say whether this request was charged.')
    _money(cash);_money(credits)
    if not isinstance(note,str) or not note.strip():raise ValueError('Record what you checked in your ElevenLabs account.')
    if not charged and (cash or credits):raise ValueError('An uncharged request must have no cash or credit charge.')
    reservation=load_reservations(root).get(identity)
    if not reservation or (reservation.get('output_hint') or {}).get('kind')!='audio':raise ValueError('Choose an unresolved audio request.')
    if reservation['state'] in ('completed','failed'):raise ValueError('This charge has already been resolved.')
    return {'reservation_id':identity,'reservation':reservation,'charged':charged,'cash_usd':cash,'credits_usd':credits,'note':note.strip()}


def resolve_charge(root,identity,charged,cash,credits,note):
    from tools.cost_tracker import reservation_lock,reconcile_paid_call
    from tools.audio._governed import _tracker
    from lib.project_config import load_current_verified_project_config
    with reservation_lock(root):
        record=resolution_record(root,identity,charged,cash,credits,note)
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
    identity=binding.get('brief_id')
    if not isinstance(identity,str) or str(uuid.UUID(identity))!=identity:raise ValueError('Invalid music brief.')
    path=_checked_path(_directory(root)/'briefs'/(identity+'.json'))
    return json.loads(path.read_text())


def check_binding(root,record):
    binding=record.get('binding')
    current=binding_source(root,binding)
    if record_sha256(current)!=binding.get('source_sha256'):
        raise ValueError('The spoken line or music brief changed. This take needs a fresh look.')
    prompt=current.get('text');seconds=current.get('duration_frames',0)/24 if binding['kind']=='speech' else current.get('duration_seconds')
    if record['kind']!=binding['kind'] or record['prompt']!=prompt or record['duration_seconds']!=seconds:
        raise ValueError('The request no longer matches its exact line or brief.')
    return current


def prepare_request(root,kind,model,*,line_index=None,voice=None,brief_id=None):
    from lib.audio_prices import quote
    from tools.audio import _governed
    binding={'kind':kind}
    if kind=='speech':binding['line_index']=line_index
    elif kind=='music':binding['brief_id']=brief_id
    else:raise ValueError('Choose speech or instrumental music.')
    source=binding_source(root,binding);binding['source_sha256']=record_sha256(source)
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
    take={'id':identity,'project_id':Path(root).name,'kind':record['kind'],'origin':'generated',
          'approved_request_id':identity,'approval_record_sha256':record_sha256(record),
          'generation_receipt_id':row['receipt_id'],'sha256':sha,'path':str(path.relative_to(root)),
          'duration_seconds':record['duration_seconds'],'binding':record['binding']}
    if record['kind']=='music':take['release_note']=TEST_ONLY
    return _save(root,take)


def require_accepted(root,identity):
    take=read(root,identity)
    if take_state(root,take)!='Accepted':raise ValueError('Only an accepted, unchanged audio take can be used.')
    return take
