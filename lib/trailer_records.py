"""Canonical facts projected from saved paper and verified media decisions.

These are incomplete stage records, never approvals. The saved paper and
signed receipts remain the authority and make interrupted projection replayable.
"""
import copy
import fcntl
import json
from pathlib import Path

from lib import receipts
from lib.canonical_json import record_sha256
from lib.checkpoint import write_checkpoint,_merge_decision_log
from lib.pathsafe import validate_output_parent
from lib.state_io import atomic_write_json


def _settings(root):
    rows=receipts.verified_approvals(root,'config')
    if not rows:return None
    from lib.project_config import load_current_verified_project_config
    config=load_current_verified_project_config(root)
    return {'config_sha256':config.digest,'approval_receipt_id':rows[-1]['receipt_id']}


def _settings_decision(root,reference):
    if reference is None:return
    identity='signed-settings-'+reference['approval_receipt_id']
    path=root/'decision_log.json'
    if path.is_symlink():raise ValueError('Production records must not use links.')
    if path.exists() and any(d.get('decision_id')==identity for d in json.loads(path.read_text()).get('decisions',[])):return
    _merge_decision_log(root.parent,root.name,{'version':'1.0','project_id':root.name,'decisions':[{
        'decision_id':identity,'stage':'proposal','category':'approval_policy',
        'subject':'Signed production settings','options_considered':[{
            'option_id':'signed-settings','label':'Use the settings already signed','score':1,
            'reason':'The existing verified signature approves these settings.'}],
        'selected':'signed-settings','reason':'This records the existing settings signature. It grants no additional approval.',
        'question_id':'config_sha256: '+reference['config_sha256'],'user_approved':True,'user_visible':True}]})


def _publish(root,stage,name,artifact):
    from schemas.artifacts import validate_artifact
    validate_artifact(name,artifact)
    path=validate_output_parent(root/'artifacts'/f'{name}.json',root)
    checkpoint=root/f'checkpoint_{stage}.json'
    if checkpoint.is_symlink():raise ValueError('Production records must not use links.')
    existing=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    if existing.get('artifacts',{}).get(name)!=artifact:
        write_checkpoint(root.parent,root.name,stage,'in_progress',{name:artifact})
    if not path.exists() or json.loads(path.read_text())!=artifact:atomic_write_json(path,artifact)


def sync_paper(root):
    """Idempotent: a retry repairs derived facts without another edit revision."""
    from lib import trailer_edit
    root=Path(root)
    directory=validate_output_parent(root/'artifacts',root)
    lock=validate_output_parent(directory/'.canonical-records.lock',root)
    with lock.open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        saved=trailer_edit.read_revision(root)
        if not saved or 'paper_cut' not in saved:return
        paper=copy.deepcopy(saved['paper_cut']);settings=_settings(root)
        metadata={'paper_cut':paper,'paper_cut_sha256':record_sha256(paper),
                  'story_inputs':saved['inputs'],'story_check':saved['story_check'],
                  'signed_settings':settings,'approval':'This records the saved paper cut. No stage is approved.'}
        sections=[]
        for key,label in [('lines','Spoken line'),('cards','Title card')]:
            for i,row in enumerate(paper[key],1):
                section={'id':f'{key}-{i}','label':f'{label} {i}','text':row['text'],
                    'start_seconds':row['start_frame']/24,'end_seconds':(row['start_frame']+row['duration_frames'])/24}
                if row.get('source_ref'):section['source_ref']=row['source_ref']
                sections.append(section)
        marker=json.loads((root/'project.json').read_text())
        script={'version':'1.0','title':marker['title'],'trailer':saved['trailer'],
                'total_duration_seconds':saved['trailer']['target_duration_seconds'],
                'sections':sections,'metadata':metadata}
        scenes=[];frame=0
        for row in paper['shots']:
            scenes.append({'id':row['id'],'type':'broll','description':row['purpose'],
                           'start_seconds':frame/24,'end_seconds':(frame+row['duration_frames'])/24})
            frame+=row['duration_frames']
        plan={'version':'1.0','trailer':saved['trailer'],'scenes':scenes,'metadata':metadata}
        _publish(root,'script','script',script)
        _publish(root,'scene_plan','scene_plan',plan)
        _settings_decision(root,settings)
