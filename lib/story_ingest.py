"""Verbatim source inventory feeding the existing canon and stage signer.

This adapter extracts no meaning. The source is one exact passage, and only
the writer can designate protected wording or spoilers. No transport lives here.
"""
import hashlib,json,stat,re,unicodedata
from pathlib import Path
from lib import gates,receipts
from lib.canonical_json import record_sha256
from lib.checkpoint import write_checkpoint,read_checkpoint
from lib.media_spending import gate_request,_root
from lib.pathsafe import resolve_input,PathSafetyError
from lib.state_io import atomic_write_bytes,atomic_move
from lib.trailer_contract import signed_trailer_pin,require_stage_approval

# Stay below the existing 128 KiB request and 2 MiB detail-display bounds,
# including worst-case JSON escaping of the exact passage in the checkpoint.
MAX_STORY_BYTES=128*1024
SOURCE_KEY='verbatim_story'
UNREADABLE='The supplied story changed or cannot be verified. Supply it again and sign its exact text before using it.'
REQUEST_UNREADABLE='A saved signing request could not be read safely. Reopen its panel and prepare it again before signing this story.'

def story_json(root,relative):
    try:
        value=json.loads(resolve_input(relative,root).read_text(encoding='utf-8'))
        if not isinstance(value,dict):raise ValueError()
        return value
    except (PathSafetyError,OSError,ValueError,TypeError):raise ValueError(UNREADABLE) from None

def story_checkpoint(root):
    path=root/'checkpoint_canon_ingest.json'
    if not path.exists() and not path.is_symlink():return None
    story_json(root,'checkpoint_canon_ingest.json')
    try:return read_checkpoint(root.parent,root.name,'canon_ingest')
    except (PathSafetyError,OSError,ValueError,KeyError,TypeError,AttributeError):raise ValueError(UNREADABLE) from None

def pending_story_requests(root):
    """Do not follow links or silently hide damaged pending signing material."""
    base=root/'.gate-requests'
    if not base.exists() and not base.is_symlink():return []
    try:
        resolve_input('.gate-requests',root)
        return [(path,story_json(root,path.relative_to(root))) for path in sorted(base.glob('*.json'))]
    except (PathSafetyError,OSError,ValueError):raise ValueError(REQUEST_UNREADABLE) from None

def retire_unsigned(root,current):
    signed={row['record_sha256'] for row in receipts.verified_approvals(root,'artifact_review',project_id=root.name)}
    for path,request in pending_story_requests(root):
        if (request.get('project_id')!=root.name or request.get('request_id')==current or
            request.get('kind')!='artifact_review' or request.get('scope')!='trailer-stage' or request.get('stage')!='canon_ingest' or
            not isinstance(request.get('approval_record'),dict) or request.get('approval_receipt_id') or 'declined_note' in request):continue
        if record_sha256(request['approval_record']) in signed:continue
        archive=root/'.gate-requests/abandoned'
        if archive.is_symlink():raise ValueError(REQUEST_UNREADABLE)
        archive.mkdir(exist_ok=True)
        destination=archive/path.name
        if destination.exists() or destination.is_symlink():raise ValueError(REQUEST_UNREADABLE)
        atomic_move(path,destination)

def root_checked(root):
    from tools.audio._legacy_guard import _checked_path
    root=_root(_checked_path(root))
    if signed_trailer_pin(root) is None:raise ValueError('Sign this trailer’s creation before supplying its story.')
    return root

def text_bytes(raw):
    if not isinstance(raw,bytes) or len(raw)>MAX_STORY_BYTES:
        raise ValueError('Choose a plain UTF-8 text story no larger than 128 kilobytes. Nothing was saved.')
    try:text=raw.decode('utf-8')
    except UnicodeDecodeError:raise ValueError('This file is not readable UTF-8 text. Choose a plain text or Markdown file.') from None
    if not any(not c.isspace() and unicodedata.category(c)[0] not in {'C','M'} and c not in '\u2800\u3164\uffa0' for c in text):
        raise ValueError('Supply a story with some words before saving it for review.')
    if any(ord(c)<32 and c not in '\t\r\n' for c in text) or '\x7f' in text:
        raise ValueError('This file is not plain text. Choose a plain text or Markdown story.')
    return text

def annotations(values,text,label):
    if not isinstance(values,list) or len(values)>50 or any(not isinstance(v,str) or not v.strip() or v not in text for v in values):
        raise ValueError(f'Each {label} must be an exact passage from this story, with at most fifty passages.')
    if len(values)!=len(set(values)):raise ValueError(f'Use each {label} once.')
    return values

def supply(root,raw,*,protected=None,spoilers=None,name='Pasted story'):
    root=root_checked(root);text=text_bytes(raw)
    protected=annotations([] if protected is None else protected,text,'protected wording')
    spoilers=annotations([] if spoilers is None else spoilers,text,'spoiler passage')
    if sum(len(v.encode('utf-8')) for v in protected+spoilers)>MAX_STORY_BYTES:
        raise ValueError('Keep the designated protected wording and spoiler passages within 128 kilobytes in total.')
    if not isinstance(name,str) or not name or len(name)>160 or Path(name).name!=name or '/' in name or '\\' in name:
        raise ValueError('Choose a story file by its name; the board does not read a typed file path.')
    digest=hashlib.sha256(raw).hexdigest();relative=f'sources/story/{digest}.txt'
    metadata={'project_id':root.name,'path':relative,'sha256':digest,'byte_length':len(raw),'characters':len(text),'name':name}
    packet={'version':'1.1','project_title':json.loads((root/'project.json').read_text())['title'],'format':'trailer',
        'source_documents':[{'path':relative,'doc_type':'other','authority':'writing_document','sha256':digest,'title':name}],
        'locks':[{'id':f'spoiler-{i+1}','decision':v,'spoiler':True,'source_path':relative,'source_type':'writer_statement'} for i,v in enumerate(spoilers)],
        'protected_lines':[{'line':v,'source_path':relative} for v in protected],
        'tone':{'tone_words':['Not interpreted; use the supplied story']},'structure':{'structure_source':relative,'beats':[{'id':'supplied-story','summary':text,'source_path':relative}]},
        'open_questions':[],'metadata':{SOURCE_KEY:metadata}}
    from schemas.artifacts import validate_artifact
    validate_artifact('canon_packet',packet)
    with gates.receipt_lock(root.name,'approval'):
        pending_story_requests(root)
        directory=root/'sources'/'story'
        for p in (root/'sources',directory):
            if p.is_symlink():raise ValueError('Story storage must not use links. Nothing was saved.')
        directory.mkdir(parents=True,exist_ok=True)
        path=directory/Path(relative).name
        if path.is_symlink():raise ValueError('Story storage must not use links. Nothing was saved.')
        if path.exists():
            if path.read_bytes()!=raw:raise ValueError('The saved story fingerprint does not match its bytes. Nothing was replaced.')
        else:atomic_write_bytes(path,raw)
        cp=root/'checkpoint_canon_ingest.json'
        if cp.is_symlink():raise ValueError('The story approval record must not use links.')
        write_checkpoint(root.parent,root.name,'canon_ingest','awaiting_human',{'canon_packet':packet},human_approval_required=True)
        from lib.trailer_contract import approval_record
        record=approval_record(root,signed_trailer_pin(root),'canon_ingest',{'canon_packet':packet})
        gate=gate_request(root,'artifact_review',None,record,'Review the exact supplied story and its protected passages. This approves the story source only; it buys nothing.',scope='trailer-stage',stage='canon_ingest')
        retire_unsigned(root,gate['request_id'])
        return {**metadata,'gate':gate}

def material(root,packet=None):
    try:
        root=root_checked(root)
        if packet is None:packet=story_json(root,'checkpoint_canon_ingest.json')['artifacts']['canon_packet']
        meta=packet['metadata'][SOURCE_KEY]
        if meta['project_id']!=root.name:raise ValueError()
        if not isinstance(meta['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',meta['sha256']):raise ValueError()
        expected=f"sources/story/{meta['sha256']}.txt"
        if meta['path']!=expected:raise ValueError()
        path=resolve_input(expected,root)
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_STORY_BYTES:raise ValueError()
        raw=path.read_bytes();text=text_bytes(raw)
        if len(raw)!=meta['byte_length'] or len(text)!=meta['characters'] or hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError()
        if packet['structure']['beats']!=[{'id':'supplied-story','summary':text,'source_path':expected}]:raise ValueError()
        if packet['source_documents']!=[{'path':expected,'doc_type':'other','authority':'writing_document','sha256':meta['sha256'],'title':meta['name']}]:raise ValueError()
        annotations([v['line'] for v in packet.get('protected_lines',[])],text,'protected wording')
        annotations([v['decision'] for v in packet.get('locks',[])],text,'spoiler passage')
        styles=set(re.findall(r'\r\n|\r|\n',text))
        labels={'\r\n':'Windows line endings','\r':'old Mac carriage returns','\n':'modern Mac and Unix line endings'}
        ending_words=('No line breaks.' if not styles else 'Line breaks use '+labels[next(iter(styles))]+'.' if len(styles)==1 else 'Mixed line endings: '+', '.join(labels[v] for v in ('\r\n','\r','\n') if v in styles)+'.')
        return {**meta,'text':text,'line_endings':ending_words,'protected':[v['line'] for v in packet.get('protected_lines',[])],'spoilers':[v['decision'] for v in packet.get('locks',[])]}
    except (KeyError,TypeError,ValueError,OSError,PathSafetyError,AttributeError):
        raise ValueError(UNREADABLE) from None

def complete(root):
    root=root_checked(root)
    with gates.receipt_lock(root.name,'approval'):
        pending_story_requests(root)
        cp=story_json(root,'checkpoint_canon_ingest.json')
        material(root,cp['artifacts']['canon_packet'])
        try:require_stage_approval(root,signed_trailer_pin(root),'canon_ingest',cp['artifacts'])
        except ValueError:raise ValueError('Nothing has been signed for this current story. Review and sign its exact text in Story workspace before applying its approval.') from None
        if cp.get('status')!='completed' or not cp.get('human_approved'):
            write_checkpoint(root.parent,root.name,'canon_ingest','completed',cp['artifacts'],human_approval_required=True,human_approved=True)
    return {'message':'Your exact story is signed. Check the saved paper cut against it before making sound or footage.'}

def source_warning(root,text):
    from lib.canon_enforcement import exact_passage_warning
    cp=story_checkpoint(Path(root))
    canon=(cp.get('artifacts',{}).get('canon_packet',{}) if cp and cp.get('status')=='completed' else {})
    return exact_passage_warning(canon,text,'This spoken line')

def check_paper(root):
    from lib import trailer_edit as edit
    root=root_checked(root)
    cp=story_checkpoint(root)
    if not cp or cp.get('status')!='completed':raise ValueError('Sign the current story first, then check the saved paper cut.')
    current=edit.read_revision(root)
    if not current:raise ValueError('Save a paper cut first, then check it against the approved story.')
    result=edit.save_revision(root,current['paper_cut'],current['edit_revision_sha256'])
    return {'message':'The saved paper cut was compared with the approved story. Review its warnings beside your lines and cards.','paper_cut':result}

def status(root):
    root=root_checked(root)
    with gates.receipt_lock(root.name,'approval'):
        return _status(root)

def _status(root):
    pending=pending_story_requests(root)
    if not (root/'checkpoint_canon_ingest.json').exists() and not (root/'checkpoint_canon_ingest.json').is_symlink():
        if (root/'sources/story').exists() or (root/'sources/story').is_symlink() or any(row.get('scope')=='trailer-stage' and row.get('stage')=='canon_ingest' for _,row in pending):raise ValueError(UNREADABLE)
        return {'message':'Supply the exact story this trailer is drawn from. Saving it approves nothing.','current':None}
    cp=story_json(root,'checkpoint_canon_ingest.json')
    if SOURCE_KEY not in cp.get('artifacts',{}).get('canon_packet',{}).get('metadata',{}):
        return {'message':'This production has existing story material. Supplying a new story requires a fresh signature.','current':None}
    current=material(root,cp['artifacts']['canon_packet'])
    verified=story_checkpoint(root)
    approved=bool(verified and verified.get('status')=='completed' and verified.get('human_approved'))
    from lib.trailer_contract import approval_record
    current_record=approval_record(root,signed_trailer_pin(root),'canon_ingest',cp['artifacts'])
    gate_id=None
    for _,row in reversed(pending):
        if row.get('project_id')!=root.name or row.get('kind')!='artifact_review' or row.get('scope')!='trailer-stage' or row.get('stage')!='canon_ingest':continue
        if row.get('approval_record')==current_record:gate_id=row['request_id'];break
    signed=False
    try:require_stage_approval(root,signed_trailer_pin(root),'canon_ingest',cp['artifacts']);signed=True
    except ValueError:pass
    if not signed and not gate_id:raise ValueError(REQUEST_UNREADABLE)
    return {'current':current,'approved':approved,'signed_unapplied':signed and not approved,'gate_id':gate_id,
        'message':('Your exact story is signed. Check the saved paper cut against it before making sound or footage.' if approved else 'Your story signature is recorded. Apply it without signing again.' if signed else 'Saved for review. Nothing is approved until you sign this exact story.')}
