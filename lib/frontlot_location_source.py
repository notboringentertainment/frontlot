"""Exact, production-bound location writing for the existing look signer.

No text extraction, image generation or transport happens here. WriterOS and
Wayfinder sources keep their existing parsers and request constructor.
"""
import hashlib,json,uuid
from pathlib import Path
from lib import gates,receipts
from lib.canonical_json import canonical_bytes,record_sha256
from lib.look_spec import validate_look_spec,generation_sufficient,LookSpecError
from lib.pathsafe import resolve_input
from lib.state_io import atomic_write_bytes,atomic_write_json
from lib.story_ingest import root_checked

DIRECTORY='.frontlot-location-looks'
ENTITY='trailer-location'
FIELDS={'entity_kind','name','text','fictional_subject_attestation','minor','shape_only','spoiler','depends_on',
    'establishing_view','time_of_day_default','palette_anchors','architecture_or_terrain','dressing',
    'weather_or_light_rules','continuity_risks','negative_lines'}

def external_sources(root):
    from lib.look_ingest import (writeros_package_for,parse_writeros_export,wayfinder_root_for,
        _resolved_tickets,parse_look_ticket,RESOLVED_SUBDIR)
    from lib.project_config import read_project_config
    root=root_checked(root);settings,_=read_project_config(root);found=[]
    package=writeros_package_for(root)
    if package is not None:
        for look in parse_writeros_export(package).values():
            found.append({'source':'writeros','entity_kind':look.entity_kind,'entity_id':look.entity_id,'look':look})
    if settings.get('wayfinder_root'):
        wf=wayfinder_root_for(root)
        for path,meta,_ in _resolved_tickets(wf/RESOLVED_SUBDIR,wf/'unused-exclusion'):
            if meta.get('area')!='look':continue
            look=parse_look_ticket(path,wayfinder_root=wf)
            found.append({'source':'wayfinder','entity_kind':look.entity_kind,'entity_id':look.entity_id,
                'ticket_path':path.relative_to(wf).as_posix(),'look':look})
    return found

def _spec(data):
    if not isinstance(data,dict) or set(data)!=FIELDS:
        raise ValueError('Fill in the location name, exact description and every required visual answer. Nothing is invented for you.')
    if data['entity_kind']!='location':raise ValueError('Only location looks can be written in Front Lot here. Person and character looks must use their existing sources.')
    if not isinstance(data['name'],str) or not data['name'].strip() or len(data['name'])>200:
        raise ValueError('Give this invented location a name of at most two hundred characters.')
    if not isinstance(data['text'],str):raise ValueError('Write the exact location description before preparing its approval.')
    try:data['text'].encode('utf-8')
    except UnicodeError:raise ValueError('The location description must be readable UTF-8 text.') from None
    if data['depends_on']!=[]:raise ValueError('This first-location form is for a standalone look. Cited WriterOS or Wayfinder decisions must use their existing source route.')
    spec={k:v for k,v in data.items() if k not in {'name','text'}}
    spec.update(version='1.1',entity_id=ENTITY,prompt_safe_description=data['text'])
    try:validate_look_spec(spec)
    except LookSpecError as exc:raise ValueError('Complete the required location fields: '+str(exc)) from None
    ok,_=generation_sufficient(spec)
    if not ok:raise ValueError('Confirm a complete invented location depicting no real person or minor before preparing its look for use.')
    return spec

def _store(root,relative,raw):
    directory=root/DIRECTORY
    if directory.is_symlink():raise ValueError('Location source storage cannot use links.')
    directory.mkdir(exist_ok=True)
    path=directory/Path(relative).name
    if path.is_symlink():raise ValueError('Location source storage cannot use links.')
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('A saved location source no longer matches its fingerprint. Nothing was replaced.')
    else:atomic_write_bytes(path,raw)

def prepare(root,data):
    from lib.look_ingest import IngestedLook,active_looks,look_lock_request
    root=root_checked(root);spec=_spec(data)
    with gates.receipt_lock(root.name,'approval'):
        if external_sources(root) or any(not v.writer_source_ref for v in active_looks(root).values()):
            raise ValueError('This production has existing look material. Prepare and sign its WriterOS or Wayfinder source instead of replacing it here.')
        raw=data['text'].encode('utf-8');text_sha=hashlib.sha256(raw).hexdigest()
        text_path=f'{DIRECTORY}/text-{text_sha}.txt'
        document={'version':1,'source':'written in Front Lot','project_id':root.name,'name':data['name'],
            'text_path':text_path,'text_sha256':text_sha,'look_spec':spec}
        source=canonical_bytes(document);digest=hashlib.sha256(source).hexdigest()
        relative=f'{DIRECTORY}/{digest}.json'
        ref={'project_id':root.name,'path':relative,'sha256':digest}
        _store(root,text_path,raw);_store(root,relative,source)
        look=IngestedLook('location',ENTITY,spec,record_sha256(spec),writer_source_ref=ref)
        path=look_lock_request(root,root.name,look,request_id='location-look-'+uuid.uuid4().hex,
            summary='Review the exact location look written in Front Lot. This approves no picture, upload, purchase or whole stage.')
        pointer=root/DIRECTORY/'current.json'
        if pointer.is_symlink():raise ValueError('Location source storage cannot use links.')
        atomic_write_json(pointer,ref)
        return {'gate':json.loads(path.read_text()),'look_refs':[{'entity_kind':'location','entity_id':ENTITY,'look_hash':look.look_hash}]}

def read(root,ref=None):
    root=root_checked(root)
    try:
        if ref is None:ref=json.loads(resolve_input(f'{DIRECTORY}/current.json',root).read_text())
        if not isinstance(ref,dict) or set(ref)!={'project_id','path','sha256'} or ref['project_id']!=root.name:raise ValueError()
        if ref['path']!=f"{DIRECTORY}/{ref['sha256']}.json":raise ValueError()
        path=resolve_input(ref['path'],root)
        if path.stat().st_size>32768:raise ValueError()
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError()
        doc=json.loads(raw)
        if set(doc)!={'version','source','project_id','name','text_path','text_sha256','look_spec'}:raise ValueError()
        if doc['project_id']!=root.name or doc['version']!=1 or doc['source']!='written in Front Lot':raise ValueError()
        spec=doc['look_spec'];validate_look_spec(spec)
        if spec['entity_kind']!='location' or spec['entity_id']!=ENTITY:raise ValueError()
        if not generation_sufficient(spec)[0]:raise ValueError()
        if doc['text_path']!=f"{DIRECTORY}/text-{doc['text_sha256']}.txt":raise ValueError()
        text=resolve_input(doc['text_path'],root).read_bytes()
        if hashlib.sha256(text).hexdigest()!=doc['text_sha256'] or text.decode('utf-8')!=spec['prompt_safe_description']:raise ValueError()
        return doc
    except (OSError,ValueError,KeyError,TypeError):
        raise ValueError('The location source changed or cannot be verified for this production. Review and sign its exact current wording again.') from None

def require_current(root,ref,payload=None):
    doc=read(root,ref)
    current=json.loads(resolve_input(f'{DIRECTORY}/current.json',root).read_text())
    if current!=ref or (payload is not None and doc['look_spec']!=payload):
        raise ValueError('The location written in Front Lot changed. It needs a fresh signature; takes based on the earlier look need a fresh look.')
    return doc

def construct(root,req):
    from scripts.gate_approve import Constructed,GateHandlerError
    from lib.look_ingest import active_look_for
    try:
        ref=req['writer_source_ref'];doc=require_current(root,ref)
        if req.get('entity_id')!=ENTITY or req.get('scope')!='location:'+ENTITY:raise ValueError('This location request names another production or subject.')
        if external_sources(root):raise ValueError('Existing WriterOS or Wayfinder look material must be reviewed through its own source route.')
        current=active_look_for(root,'location',ENTITY)
        envelope={'action':'activate','entity_kind':'location','look_hash':record_sha256(doc['look_spec']),
            'supersedes_look_hash':current.look_hash if current else None,'writer_source_ref':ref}
        def recheck():require_current(root,ref,doc['look_spec'])
        return Constructed(doc['look_spec'],envelope,ENTITY,pre_commit_check=recheck,
            evidence=('Written in Front Lot for this production. The exact text is kept without rewriting. No picture, upload, purchase or whole stage is approved.',))
    except (ValueError,KeyError,OSError) as exc:raise GateHandlerError(str(exc)) from None

def prepare_existing(root,source,entity_id):
    from lib.look_ingest import look_lock_request
    root=root_checked(root)
    with gates.receipt_lock(root.name,'approval'):
        choices=[v for v in external_sources(root) if v['source']==source and v['entity_id']==entity_id and v['entity_kind']=='location']
        if len(choices)!=1:raise ValueError('Choose one verified existing location source. Character looks stay with their existing controls.')
        item=choices[0]
        path=look_lock_request(root,root.name,item['look'],request_id='location-look-'+uuid.uuid4().hex,
            ticket_path=item.get('ticket_path'))
        return {'gate':json.loads(path.read_text())}

def apply(root,request_id):
    from lib.look_ingest import active_look_for
    from lib.run_common import read_request
    from lib.checkpoint import write_checkpoint
    root=root_checked(root)
    with gates.receipt_lock(root.name,'approval'):
        req=read_request(root,request_id)
        if not req or req.get('kind')!='look_lock':raise ValueError('Choose the signed location-look request.')
        row=receipts.find_approval(root,'look_lock',entity_id=req['entity_id'],record_sha256=record_sha256(req['approval_record']))
        if row is None:raise ValueError('Sign this location look before applying it. Nothing is approved by saving.')
        current=active_look_for(root,'location',req['entity_id'])
        if current is None or current.receipt_id!=row['receipt_id']:raise ValueError('This is no longer the current location look. Review its latest source.')
        if row.get('writer_source_ref'):
            require_current(root,row['writer_source_ref'],row['record']);source={'writer_source_ref':row['writer_source_ref']}
        else:
            choices=[v for v in external_sources(root) if v['entity_id']==req['entity_id'] and v['look'].look_hash==current.look_hash]
            if len(choices)!=1:raise ValueError('The existing look source changed. Review its source before applying the signed look.')
            look=choices[0]['look'];source={'source_ref':look.source_ref} if look.is_writeros else {'source_ticket_ref':look.source_ticket_ref}
        path=root/'checkpoint_look_lock.json'
        packet=json.loads(resolve_input(path.name,root).read_text()).get('artifacts',{}).get('look_packet',{}) if path.exists() else {}
        entry={'entity_kind':'location','entity_id':current.entity_id,'look_spec':current.payload,'look_hash':current.look_hash,
            'receipt_id':current.receipt_id,**source}
        entries=[v for v in packet.get('looks',[]) if (v['entity_kind'],v['entity_id'])!=('location',current.entity_id)]+[entry]
        result={'version':'1.0','looks':entries,'complete':False}
        if packet==result:return {'message':'The signed location look is already available. No stage was completed.'}
        write_checkpoint(root.parent,root.name,'look_lock','in_progress',{'look_packet':result})
        return {'message':'The signed location look is available for reference selection. No picture was made and no whole stage was completed.'}
