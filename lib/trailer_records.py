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
    # A pending settings draft is not a new signature. Derived records refer
    # to what was actually signed; paid operations still verify the live file.
    return {'config_sha256':rows[-1]['record']['config_sha256'],
            'approval_receipt_id':rows[-1]['receipt_id']}


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


def paper_artifacts(root):
    """Pure projection of the current saved paper, used by save and signing."""
    from lib import trailer_edit
    root=Path(root)
    saved=trailer_edit.read_revision(root)
    if not saved or 'paper_cut' not in saved:return {}
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
    return {'script':script,'scene_plan':{'version':'1.0','trailer':saved['trailer'],'scenes':scenes,'metadata':metadata}}


def _write_artifact(root,name,artifact):
    from schemas.artifacts import validate_artifact
    validate_artifact(name,artifact)
    path=validate_output_parent(root/'artifacts'/f'{name}.json',root)
    if not path.exists() or json.loads(path.read_text())!=artifact:atomic_write_json(path,artifact)


def sync_paper(root):
    """Idempotent: a retry repairs derived facts without another edit revision."""
    from lib.trailer_contract import signed_trailer_pin
    from lib.trailer_policy import trimmed
    root=Path(root)
    directory=validate_output_parent(root/'artifacts',root)
    lock=validate_output_parent(directory/'.canonical-records.lock',root)
    with lock.open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        artifacts=paper_artifacts(root)
        if not artifacts:return
        if trimmed(signed_trailer_pin(root)):
            path=validate_output_parent(root/'checkpoint_script.json',root)
            existing=json.loads(path.read_text()) if path.exists() else {}
            if existing.get('artifacts')!=artifacts:write_checkpoint(root.parent,root.name,'script','in_progress',artifacts)
            for name,value in artifacts.items():_write_artifact(root,name,value)
        else:
            for stage,name in [('script','script'),('scene_plan','scene_plan')]:_publish(root,stage,name,artifacts[name])
        _settings_decision(root,_settings(root))


def sync_media(root):
    """Only currently kept, verified takes enter the canonical media facts."""
    from lib import audio_workspace as audio,footage_requests as footage
    root=Path(root)
    directory=validate_output_parent(root/'artifacts',root)
    with validate_output_parent(directory/'.canonical-records.lock',root).open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        rows=receipts.verified_approvals(root,'audio_take')+receipts.verified_approvals(root,'footage_take')
        sources={}
        for row in rows:
            kind=row.get('kind');record=row.get('record') or {};take=record.get('take') or {}
            if kind not in ('audio_take','footage_take'):continue
            identity=take.get('id') if kind=='audio_take' else take.get('take_id')
            if identity:sources[(kind,identity)]=row
        if not sources:return
        assets=[];kept=[];unusable=[]
        for (kind,identity),row in sources.items():
            record=row['record'];candidate=record['take']
            if kind=='audio_take':
                take=audio.read(root,identity);state=audio.take_state(root,take)
            else:
                state=footage.take_state(root,record['shot_id'],identity)
                take=footage._take(root,record['shot_id'],identity)
            if state!='Accepted':
                unusable.append({'take_id':identity,'state':state});continue
            take=audio.require_accepted(root,identity) if kind=='audio_take' else footage.require_accepted(root,record['shot_id'],identity)
            if take!=candidate:raise ValueError('A kept take differs from its signed decision. Restore the exact take or review a new one.')
            if kind=='audio_take':
                asset={'id':identity,'type':{'speech':'narration','music':'music','effect':'sfx'}[take['kind']],
                       'path':take['path'],'scene_id':'','source_tool':'elevenlabs_audio' if take['origin']=='generated' else 'file_import',
                       'duration_seconds':take['duration_seconds']}
                if take['origin']=='imported':asset['license']=take['licence_note']
            else:
                asset={'id':identity,'type':'video','path':take['path'],'scene_id':record['shot_id'],
                       'source_tool':'kling_reference_video','duration_seconds':take['duration_seconds'],
                       'cost_usd':take['cost_usd']}
            assets.append(asset)
            kept.append({'take':take,'approval_receipt_id':row['receipt_id']})
        artifact={'version':'1.0','assets':assets,'metadata':{'take_records':kept,'unusable_takes':unusable,
            'signed_settings':_settings(root),'approval':'These are verified take decisions. No media stage is approved.'}}
        from lib.trailer_contract import signed_trailer_pin
        from lib.trailer_policy import trimmed
        if trimmed(signed_trailer_pin(root)):_write_artifact(root,'asset_manifest',artifact)
        else:_publish(root,'assets','asset_manifest',artifact)


def after_signature(root,kind):
    """Projection only, after the existing receipt and request have committed."""
    from lib.trailer_contract import signed_trailer_pin
    if signed_trailer_pin(root) is None:return
    from lib.checkpoint import CheckpointValidationError
    from tools.cost_tracker import IndeterminatePaidCallError
    try:
        if kind in ('audio_take','footage_take'):sync_media(root)
        elif kind in ('config','audio_resolution'):
            sync_paper(root)
            _settings_decision(Path(root),_settings(root))
            if kind=='audio_resolution':sync_media(root)
    except CheckpointValidationError as exc:
        # The receipt has already committed. A held charge must still stop
        # checkpoint writes, not turn a signed local decision into a failed
        # signature. The existing signed account-resolution operation replays
        # these derived facts after the hold is actually resolved.
        if not isinstance(exc.__cause__,IndeterminatePaidCallError):raise
