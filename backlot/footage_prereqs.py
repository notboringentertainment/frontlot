"""Local controls for the existing look, picture-import and settings signers."""
import base64,binascii,json,uuid
from pathlib import Path
from lib import frontlot_location_source as locations,reference_import as pictures,receipts
from lib.story_ingest import root_checked
from lib.pathsafe import resolve_input,PathSafetyError
from lib.state_io import atomic_write_bytes
from lib.media_spending import PaidCallBusy

def checked_look(root,ref):
    from lib.look_ingest import verify_look_refs,LookIngestError
    if not isinstance(ref,dict) or set(ref)!={'entity_kind','entity_id','look_hash'} or ref['entity_kind']!='location':
        raise ValueError('Choose an approved location look before importing or using a picture. Person and character looks are outside this form.')
    try:verify_look_refs(root,[ref])
    except (LookIngestError,ValueError,KeyError,PathSafetyError,OSError):
        raise ValueError('This location look is unsigned or has changed. Review and sign its current description before using a picture.') from None
    return ref

def prepare_picture(root,data):
    root=root_checked(root)
    keys={'file','look','origin_class','machine_generated','no_real_person','origin_tool'}
    if not isinstance(data,dict) or set(data)!=keys:raise ValueError('Use the picture chooser, approved location and origin statement before preparing its signature.')
    if data['origin_class']!='imported_synthetic' or data['machine_generated'] is not True or data['no_real_person'] is not True:
        raise ValueError('Only a machine-generated picture depicting no real person can be used here. Personal photographs, real-person inspiration and other origins are refused.')
    checked_look(root,data['look'])
    if not isinstance(data['origin_tool'],str) or not data['origin_tool'].strip() or len(data['origin_tool'])>200:
        raise ValueError('Name the tool that made this machine-generated picture before signing its origin.')
    file=data['file']
    if not isinstance(file,dict) or set(file)!={'name','base64'} or not isinstance(file['name'],str) or Path(file['name']).name!=file['name'] or '/' in file['name'] or '\\' in file['name']:
        raise ValueError('Choose the picture file on your Mac. The board does not read a typed path.')
    suffix=Path(file['name']).suffix.lower()
    if suffix not in {'.png','.jpg','.jpeg'}:raise ValueError('Choose a PNG or JPEG picture for this import.')
    try:raw=base64.b64decode(file['base64'],validate=True)
    except (ValueError,TypeError):raise ValueError('The picture could not be read. Choose it again.') from None
    if not raw or len(raw)>pictures.MAX_SOURCE_BYTES:raise ValueError('Choose a readable picture no larger than 64 megabytes.')
    staging=root/'.staging'
    if staging.is_symlink():raise ValueError('Picture storage cannot use links. Nothing was imported.')
    staging.mkdir(exist_ok=True)
    path=staging/('reference-source-'+uuid.uuid4().hex+suffix)
    atomic_write_bytes(path,raw)
    try:result=pictures.prepare_reference_import(root,root.name,path,origin_class='imported_synthetic',origin_tool=data['origin_tool'])
    except pictures.TaintError:raise ValueError('This picture is already recorded as real-person inspiration. It cannot be used as a synthetic reference. Choose a different machine-generated picture depicting no real person.') from None
    except pictures.ReferenceImportError:raise ValueError('The picture could not be imported under the existing image and origin checks. Choose a readable, unaltered PNG or JPEG machine-generated picture depicting no real person.') from None
    return {'gate':json.loads(result.request_path.read_text())}

def use_picture(root,data):
    from lib import trailer_edit as edit
    root=root_checked(root)
    if not isinstance(data,dict) or set(data)!={'receipt_id','shot_id','look'}:raise ValueError('Choose the signed picture, its approved look and the paper-cut shot.')
    checked_look(root,data['look'])
    row=next((r for r in pictures.reference_import_receipts(root,project_id=root.name) if r['receipt_id']==data['receipt_id']),None)
    if row is None or row.get('origin_class')!='imported_synthetic':raise ValueError('Sign this machine-generated picture’s origin for this production before using it.')
    current=edit.read_revision(root)
    if not current or 'paper_cut' not in current:raise ValueError('Save the paper cut and its shots before assigning a picture.')
    shot=next((s for s in current['paper_cut']['shots'] if s['id']==data['shot_id']),None)
    if shot is None:raise ValueError('This paper-cut shot changed or is missing. Choose its current shot again.')
    # The existing import creates an imported lineage root only from its real
    # receipt. No generated-footage record or whole-stage completion is made.
    try:imported=pictures.finalize_reference_import(root,data['receipt_id'],project_id=root.name)
    except pictures.ReferenceImportError:raise ValueError('The signed picture changed or is missing. Choose and sign its origin again before using it.') from None
    relative=imported.path.relative_to(root).as_posix()
    if relative not in shot['references']:
        shot['references'].append(relative)
        edit.save_revision(root,current['paper_cut'],current['edit_revision_sha256'])
    return {'message':'The signed picture is in the selected shot. Choose its approved look on the footage card; that exact pairing is bound by each footage signature. No clip was made.'}

def snapshot(root):
    from lib.look_ingest import active_looks,verify_look_refs,LookIngestError
    from lib.trailer_edit import paper_state
    from backlot.state import _gate_rows
    from lib.project_config import read_project_config,load_current_verified_project_config,ProjectConfigError
    root=root_checked(root);doc=None;location_error=None
    pointer=root/locations.DIRECTORY/'current.json'
    if pointer.exists() or pointer.is_symlink():
        try:doc=locations.read(root)
        except ValueError as exc:location_error=str(exc)
    approved=[]
    for (kind,entity),look in active_looks(root).items():
        if kind!='location':continue
        ref={'entity_kind':kind,'entity_id':entity,'look_hash':look.look_hash}
        try:verify_look_refs(root,[ref])
        except (LookIngestError,ValueError):continue
        name=doc['name'] if doc and look.writer_source_ref else entity.replace('-',' ').title()
        approved.append({'name':name,'ref':ref,'payload':look.payload})
    external=locations.external_sources(root)
    existing=[{'source':v['source'],'entity_id':v['entity_id'],'name':v['look'].payload['prompt_safe_description'][:100]}
        for v in external if v['entity_kind']=='location']
    gate_rows=_gate_rows(root,root.name,limit=None)
    if gate_rows.get('error'):raise ValueError('The saved signing requests could not be read safely. Reopen their review before continuing.')
    pending=[r for r in gate_rows['requests'] if r.get('state')=='pending' and (r.get('kind')=='reference_import' or r.get('kind')=='look_lock' and str(r.get('scope','')).startswith('location:'))]
    imports=[{'receipt_id':r['receipt_id'],'tool':r['record'].get('origin_tool'),'hash':r['normalized_pixel_hash']}
        for r in pictures.reference_import_receipts(root,project_id=root.name) if r.get('origin_class')=='imported_synthetic']
    state=paper_state(root);settings,_=read_project_config(root)
    try:load_current_verified_project_config(root);signed=True
    except ProjectConfigError:signed=False
    return {'location':doc,'location_error':location_error,'approved_looks':approved,'existing_sources':existing,'external_material':bool(external),'pending':pending,
        'pictures':imports,'shots':((state.get('current') or {}).get('paper_cut') or {}).get('shots',[]),
        'price_settings_signed':signed,'footage_prices':settings.get('footage_price_basis'),
        'message':('The location look is signed and current.' if approved else 'Location looks only. Write and sign an invented place, or review its existing WriterOS or Wayfinder source. No person or character look can be created here.')}

def mount(app,require_writer,safe_root,invalidate):
    import asyncio
    from fastapi import HTTPException,Request
    from lib.media_settings import prepare_footage
    prefix='/api/project/{project_id}/footage-setup'
    async def run(project_id,fn,*args):
        try:return await asyncio.to_thread(fn,safe_root(project_id),*args)
        except PaidCallBusy:
            raise HTTPException(400,'A sound or footage operation is still running. Wait for it to finish before changing these settings.') from None
        except (PathSafetyError,UnicodeError,json.JSONDecodeError):raise HTTPException(400,'The saved footage setup could not be read safely. Reopen its source and try again.') from None
        except (ValueError,OSError,RuntimeError,KeyError,TypeError) as exc:
            raise HTTPException(400,str(exc) if isinstance(exc,ValueError) else 'Footage setup could not be read safely. Review the current story, location look and signed settings.') from None
    async def form(request):
        require_writer(request);body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>pictures.MAX_SOURCE_BYTES*2:raise HTTPException(413,'Choose a picture no larger than 64 megabytes.')
        try:value=json.loads(body)
        except (ValueError,UnicodeError):raise HTTPException(400,'This form could not be read. Fill in the controls and try again.') from None
        if not isinstance(value,dict):raise HTTPException(400,'Fill in the footage setup controls and try again.')
        return value
    @app.get(prefix)
    async def state(project_id:str):return await run(project_id,snapshot)
    @app.post(prefix+'/look')
    async def look(project_id:str,request:Request):
        result=await run(project_id,locations.prepare,await form(request));invalidate(project_id);return result
    @app.post(prefix+'/look/existing')
    async def existing(project_id:str,request:Request):
        body=await form(request);result=await run(project_id,locations.prepare_existing,body.get('source'),body.get('entity_id'));invalidate(project_id);return result
    @app.post(prefix+'/look/{identity}/apply')
    async def apply(project_id:str,identity:str,request:Request):
        require_writer(request);result=await run(project_id,locations.apply,identity);invalidate(project_id);return result
    @app.post(prefix+'/picture')
    async def picture(project_id:str,request:Request):
        result=await run(project_id,prepare_picture,await form(request));invalidate(project_id);return result
    @app.post(prefix+'/picture/use')
    async def assign(project_id:str,request:Request):
        result=await run(project_id,use_picture,await form(request));invalidate(project_id);return result
    @app.post(prefix+'/prices')
    async def prices(project_id:str,request:Request):
        result=await run(project_id,prepare_footage,await form(request));invalidate(project_id);return result
