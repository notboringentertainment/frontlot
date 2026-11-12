"""Local paper cuts. One atomic edit record, immutable revisions, no generation."""
import fcntl
import json
import math
import os
import secrets
from pathlib import Path

from lib.canonical_json import record_sha256
from lib.trailer_contract import make_contract, require_contract, signed_trailer_pin, edit_digest


def create_trailer(base, title, seconds, ceiling):
    make_contract(seconds)
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError('Give the trailer a name of up to two hundred characters.')
    if isinstance(ceiling, bool) or not isinstance(ceiling, (float, int)) or not math.isfinite(ceiling) or ceiling < 0:
        raise ValueError('Choose a total spending ceiling of zero dollars or more.')
    from lib.checkpoint import init_project
    from lib.pipeline_pin import prepare_migration_request
    slug = 'trailer-' + secrets.token_hex(8)
    root = init_project(slug, title=title.strip(), pipeline_type='authored-film@1.6',
                        pipeline_dir=Path(base), target_duration_seconds=seconds)
    marker = json.loads((root/'project.json').read_text())
    marker['spending_ceiling_usd'] = ceiling
    marker['trailer_creation_pending'] = True
    (root/'project.json').write_text(json.dumps(marker, indent=2))
    (root/'project.yaml').write_text(f'pipeline: authored-film\nbudget_usd_cap: {ceiling}\n')
    prepare_migration_request(root, slug, 'authored-film', '1.6', trailer_creation=True,
        summary=f'Start {title.strip()}, lasting {seconds:g} seconds, with a total ceiling of ${ceiling:.2f}. This signs creation only. It approves no stage and authorises no spending.')
    return root


def _inputs(root):
    paths = [root/'project.json'] + [root/f'checkpoint_{stage}.json' for stage in ('canon_ingest','proposal','script') if (root/f'checkpoint_{stage}.json').exists()]
    return {p.name: record_sha256(json.loads(p.read_text())) for p in paths}


def read_revision(root, revision=None):
    root = Path(root)
    if revision is not None:
        if not isinstance(revision,str) or len(revision)!=64 or any(c not in '0123456789abcdef' for c in revision):
            raise ValueError('That saved version could not be found.')
        path = root/'artifacts'/'edit-history'/f'{revision}.json'
    else:
        path = root/'artifacts'/'edit_decisions.json'
    if not path.exists(): return None
    value = json.loads(path.read_text())
    if value.get('edit_revision_sha256') != edit_digest(value):
        raise ValueError('The saved paper cut is damaged. Earlier versions have been kept.')
    return value


def validate_timeline(timeline, target_frames):
    if timeline.get('fps') != 24: raise ValueError('Trailer timing uses twenty-four frames per second.')
    end = 0
    for entry in timeline.get('picture', []):
        start, duration = entry.get('start_frame'), entry.get('duration_frames')
        if any(type(v) is not int for v in (start,duration)) or start < end or duration < 1:
            raise ValueError('Shots need whole-frame lengths and cannot overlap.')
        end = start + duration
    if end > 2880: raise ValueError('The sequence cannot exceed two minutes.')
    delta = end-target_frames
    return ('The sequence matches the target.' if delta==0 else
            f'The sequence is {abs(delta)} frames {"over" if delta>0 else "short of"} the target.')


def _validate_paper(root, paper):
    if not isinstance(paper,dict) or set(paper) != {'beats','lines','cards','shots'}:
        raise ValueError('Save the beats, spoken lines, title cards and shots together.')
    if [b.get('kind') for b in paper['beats']] != ['hook','escalation','reversal','invitation']:
        raise ValueError('Include a hook, escalation, reversal and final invitation in that order.')
    for b in paper['beats']:
        if not isinstance(b.get('text'),str) or not b['text'].strip(): raise ValueError('Write each beat in words.')
    for row in paper['lines'] + paper['cards']:
        if not isinstance(row.get('text'),str) or not row['text'].strip(): raise ValueError('Give each line and card its exact wording.')
        for key in ('start_frame','duration_frames'):
            if type(row.get(key)) is not int or row[key] < (1 if key=='duration_frames' else 0):
                raise ValueError('Lines and cards need whole-frame timing.')
        if row['start_frame']+row['duration_frames'] > round(require_contract(root)['target_duration_seconds']*24):
            raise ValueError('A line or card ends after the target length.')
    if not paper['shots']: raise ValueError('Add at least one shot to the timed list.')
    for shot in paper['shots']:
        if type(shot.get('duration_frames')) is not int or shot['duration_frames']<1:
            raise ValueError('Each shot needs a positive whole-frame screen time.')
        if not all(isinstance(shot.get(k),str) and shot[k].strip() for k in ('purpose','difficulty')) or not isinstance(shot.get('references'),list):
            raise ValueError('Each shot needs its purpose, references and difficulty.')
    # Use the same canon checker as checkpoint writes, never a second dialogue checker.
    from lib.canon_enforcement import _load_canon, _check_script, _canon_ids
    canon = _load_canon(root.parent,root.name,{})
    if canon is None:
        if paper['lines'] or paper['cards']:
            raise ValueError('Approve the story material before saving spoken lines or title cards.')
    else:
        from lib.checkpoint import read_checkpoint
        cp = read_checkpoint(root.parent,root.name,'canon_ingest')
        if not cp or cp.get('status') != 'completed':
            raise ValueError('The story needs fresh signed approval before saving lines or cards.')
        script = {'trailer_paper_cut':True, 'sections': paper['lines']+paper['cards'], 'metadata': {'canon_check': {
            'locks_honored':[x['id'] for x in canon.get('locks',[])], 'tensions':[]}}}
        _check_script(script,canon,_canon_ids(canon,[]),'completed')


def save_revision(root, paper, expected_revision):
    root=Path(root)
    if signed_trailer_pin(root) is None: raise ValueError('Sign trailer creation before saving its paper cut.')
    directory=root/'artifacts'
    for p in [directory,directory/'edit-history',directory/'edit_decisions.json',directory/'.paper-cut.lock']:
        if p.is_symlink(): raise ValueError('The paper cut must stay inside this production.')
    directory.mkdir(exist_ok=True)
    with (directory/'.paper-cut.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        old=read_revision(root)
        if (old or {}).get('edit_revision_sha256') != expected_revision:
            raise ValueError('The paper cut changed in another window. Reload it before saving; your earlier version is safe.')
        inputs=_inputs(root)
        _validate_paper(root,paper)
        paper=json.loads(json.dumps(paper))
        for shot in paper['shots']:
            shot.setdefault('id','shot-'+secrets.token_hex(8))
        ids=[s['id'] for s in paper['shots']]
        if any(not isinstance(x,str) or not x for x in ids) or len(set(ids))!=len(ids):
            raise ValueError('Each shot must keep its own identity. Reload the paper cut and try again.')
        contract=require_contract(root)
        picture=[]; start=0
        for i,shot in enumerate(paper['shots']):
            picture.append({'id':shot['id'], 'kind':'placeholder', 'label':'Unfinished placeholder',
                            'start_frame':start,'duration_frames':shot['duration_frames']})
            start+=shot['duration_frames']
        timeline={'fps':24,'picture':picture,'audio':[], 'cards':paper['cards']}
        value={'version':'1.0','render_runtime':'remotion','trailer':contract,'cuts':[],
               'paper_cut':paper,'timeline':timeline,'unfinished':True,'inputs':inputs,
               'previous_revision':(old or {}).get('edit_revision_sha256'),
               'timing_message':validate_timeline(timeline,round(contract['target_duration_seconds']*24))}
        value['edit_revision_sha256']=edit_digest(value)
        data=json.dumps(value,indent=2)
        history=directory/'edit-history';history.mkdir(exist_ok=True)
        archive=history/f"{value['edit_revision_sha256']}.json"
        try:
            with archive.open('x') as f:
                f.write(data);f.flush();os.fsync(f.fileno())
        except FileExistsError:
            if archive.read_text()!=data: raise ValueError('An earlier saved version is damaged. Nothing was overwritten.')
        temp=directory/f'.paper-{secrets.token_hex(8)}.tmp'
        with temp.open('x') as f: f.write(data);f.flush();os.fsync(f.fileno())
        if inputs != _inputs(root): raise ValueError('The story or trailer settings changed while saving. Review them and save again.')
        os.replace(temp,directory/'edit_decisions.json')
        fd=os.open(directory,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        return value


def paper_state(root):
    try:
        current=read_revision(root)
        from lib.canon_enforcement import _load_canon
        canon=_load_canon(Path(root).parent,Path(root).name,{}) or {}
        sources=[{'value':x['id'],'label':x.get('summary') or x.get('decision') or 'Story material'} for x in canon.get('locks',[])+canon.get('structure',{}).get('beats',[]) if x.get('id') and not x.get('spoiler')]
        return {'sources':sources, 'current':current,'needs_review':bool(current and current['inputs']!=_inputs(Path(root))),
                'history':[p.stem for p in sorted((Path(root)/'artifacts'/'edit-history').glob('*.json'))]}
    except (ValueError,OSError,KeyError):
        return {'current':None,'needs_review':True,'error':'The paper cut could not be read. Earlier versions have been kept.'}


def creation_facts(root):
    marker=json.loads((Path(root)/'project.json').read_text())
    contract=require_contract(root)
    ceiling=marker.get('spending_ceiling_usd')
    if isinstance(ceiling,bool) or not isinstance(ceiling,(int,float)) or not math.isfinite(ceiling) or ceiling<0:
        raise ValueError('The total spending ceiling is missing or invalid.')
    return {'title':marker['title'],'trailer':contract,'spending_ceiling_usd':ceiling}
