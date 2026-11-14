"""Local trailer exports and exact approvals through the existing signed gates."""
import copy
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from lib import cutting_view as cut, trailer_edit, trailer_contract as contract, receipts
from lib.canonical_json import record_sha256
from lib.pathsafe import resolve_input, validate_output_parent, sha256_file

ROUGH_REQUIRED='Sign the rough cut for this saved version before making the final file.'
CHANGED='This file or its cut needs a fresh look. Reopen the saved cut, sign it and make a new final file.'
SPEECH_NOTES='These timed speech notes are listening warnings. Listen to those passages; you can approve this exact file if the pauses and balance are right to your ear.'

def _saved(root,revision=None):
    from lib.footage_requests import require_trailer
    root=Path(root);require_trailer(root)
    saved=trailer_edit.read_revision(root)
    if not saved or not saved.get('cutting_view'):raise ValueError('Save a cut made from kept pictures and sounds first.')
    if revision is not None and saved['edit_revision_sha256']!=revision:raise ValueError('The saved cut changed. Reopen it before reviewing or exporting.')
    if not trailer_edit.paper_state(root)['usable_downstream']:raise ValueError('Check the paper cut against the approved story before reviewing or exporting.')
    timeline=cut.validate_cut(root,cut.editable(saved['timeline']),verify_files=True)
    if 'mix' not in saved['timeline']:raise ValueError('Save the colour and sound settings before reviewing the rough cut.')
    return saved,timeline

def _pin(root):
    pin=contract.signed_trailer_pin(root)
    if pin is None:raise ValueError('Sign this trailer’s creation before reviewing its cut.')
    return pin

def rough_record(root):
    saved,_=_saved(root)
    if saved.get('unfinished'):raise ValueError('Prepare this saved cut for rough-cut signing first.')
    return contract.approval_record(root,_pin(root),'edit',{'edit_decisions':saved})

def request_rough(root,revision):
    from lib.media_spending import gate_request
    saved,_=_saved(root,revision)
    if saved.get('unfinished'):
        ready=copy.deepcopy(saved);ready['unfinished']=False
        # A numbered readiness revision changes no picture, sound or card.
        saved=trailer_edit.save_edit_revision(root,ready,revision)
    record=rough_record(root)
    return gate_request(root,'artifact_review','edit',record,
        'Watch this saved rough cut. Your signature approves this exact saved version, not a later change. Nothing is sent or spent.',scope='trailer-stage',stage='edit')

def require_rough(root,revision=None):
    try:
        saved,timeline=_saved(root,revision)
        contract.require_stage_approval(root,_pin(root),'edit',{'edit_decisions':saved})
        return saved,timeline
    except (ValueError,OSError,KeyError) as e:
        raise ValueError(ROUGH_REQUIRED+' '+str(e)) from None

def rough_state(root):
    try:
        saved,_=_saved(root)
        return _approval_state(root,'edit',{'edit_decisions':saved},'Needs your signature','Signed by you')
    except (ValueError,OSError,KeyError):return 'Needs a fresh look'

def _approval_state(root,stage,artifacts,pending,approved):
    rows=[r for r in receipts.verified_approvals(root,'artifact_review') if r.get('stage')==stage and r.get('scope')=='trailer-stage']
    if not rows:return pending
    try:contract.require_stage_approval(root,_pin(root),stage,artifacts);return approved
    except (ValueError,OSError,KeyError):return 'Needs a fresh look'

def _directory(root):
    root=Path(root);path=validate_output_parent(root/'artifacts'/'deliveries',root)
    path.mkdir(exist_ok=True);return path

def _job(root,identity):
    if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{32}',identity):raise ValueError('Choose a final file made by this trailer’s delivery controls.')
    path=validate_output_parent(_directory(root)/identity,root)
    if path.is_symlink():raise ValueError('Delivery files must stay inside this production and must not use links.')
    return path

def _publish(path,value):
    """One exclusive commit marker; killed work never becomes a finished file."""
    temporary=path.with_name('.'+path.name+'-'+uuid.uuid4().hex+'.partial')
    with temporary.open('x') as f:
        json.dump(value,f,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.link(temporary,path)
    trailer_edit._sync_dir(path.parent)

def render_final(root,revision,directory):
    return cut.render_saved(root,revision,resolution=(1920,1080),directory=directory)

def _probe(path,frames):
    value=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],timeout=30))
    video=next((s for s in value['streams'] if s['codec_type']=='video'),{})
    audio=next((s for s in value['streams'] if s['codec_type']=='audio'),{})
    duration=float(value['format']['duration'])
    if (video.get('codec_name')!='h264' or (video.get('width'),video.get('height'))!=(1920,1080)
        or video.get('r_frame_rate')!='24/1' or video.get('avg_frame_rate')!='24/1'
        or any(video.get(k)!='bt709' for k in ('color_space','color_transfer','color_primaries'))
        or video.get('color_range')!='tv' or audio.get('codec_name')!='aac' or audio.get('channels')!=2
        or audio.get('sample_rate')!='48000' or abs(duration-frames/24)>1/24+1e-9 or duration>120
        or int(video.get('nb_frames',0))!=frames):
        raise ValueError('The exported file does not match the saved timing or required picture and sound format. Keep the saved cut and make the file again.')
    return {'valid_container':True,'duration_seconds':duration,'resolution':'1920x1080','fps':24,'has_audio':True,
            'codec':'h264','file_size_bytes':path.stat().st_size,'issues':[]}

def _boundaries(timeline):
    end=max(r['start_frame']+r['duration_frames'] for r in timeline['picture'])
    points={0,end-1}
    for r in timeline['picture']+timeline['cards']:
        for frame in (r['start_frame'],r['start_frame']+r['duration_frames']):
            points.update(n for n in (frame-1,frame) if 0<=n<end)
    return sorted(points)

def _samples(root,path,timeline,directory):
    from PIL import Image
    rows=[]
    for frame in _boundaries(timeline):
        target=directory/f'frame-{frame}.png'
        subprocess.run(['ffmpeg','-v','error','-n','-i',str(path),'-vf',f'select=eq(n\\,{frame})','-frames:v','1',str(target)],capture_output=True,check=True,timeout=60)
        with Image.open(target) as image:
            if image.size!=(1920,1080):raise ValueError('A boundary picture could not be read at full size. Make the final file again.')
        rows.append({'frame':frame,'seconds':frame/24,'path':str(target.relative_to(root)),'sha256':sha256_file(target)})
    return rows

def _sound_ok(measured):
    failures=measured.get('source_failures');voices=measured.get('voice_checks')
    def present(row):
        return (row.get('status') in ('clear','quiet','partial','hard_to_hear','uncertain','unheard')
                and all(isinstance(row.get(k),dict) and row[k].get('sustained') is True
                        for k in ('presence','reference_presence')))
    return (measured['on_target'] and isinstance(failures,list) and not failures
            and isinstance(voices,list) and all(present(r) for r in voices)
            and all(r['status']=='on_time' for r in measured['effect_checks']))

def _measure(root,path,saved,timeline,refs):
    from lib.trailer_finishing import review_output
    end=max(r['start_frame']+r['duration_frames'] for r in timeline['picture'])
    probe=_probe(path,end);measured=review_output(path,refs)
    return probe,measured

def export(root,revision):
    root=Path(root);saved,timeline=require_rough(root,revision)
    if __import__('shutil').disk_usage(root).free<60*1024**3:raise ValueError('There is less than sixty gigabytes free. Make room on this disk before exporting; the saved cut is safe.')
    identity=uuid.uuid4().hex;job=_job(root,identity);job.mkdir()
    work=job/'work';work.mkdir()
    target,_,refs=render_final(root,revision,work)
    # Retain the existing compose review separately from the authoritative
    # actual-file measurements. No transcription or remote judge is invoked.
    from tools.video.video_compose import VideoCompose
    generic=VideoCompose()._run_final_review(target,edit_decisions=saved)
    probe,measured=_measure(root,target,saved,timeline,refs)
    frames=job/'frames';frames.mkdir();samples=_samples(root,target,timeline,frames)
    require_rough(root,revision)
    if saved!=trailer_edit.read_revision(root):raise ValueError('The cut changed during export. Nothing was published. Reopen and sign the saved version before trying again.')
    output=job/'trailer.mp4';relative=str(output.relative_to(root));digest=sha256_file(target)
    test_only=any(r.get('test_only') for r in timeline['audio'])
    warnings=list(measured['messages'])
    sound_ok=_sound_ok(measured)
    speech_notes=sound_ok and (bool(measured['source_warnings']) or any(r['status']!='clear' for r in measured['voice_checks']+measured['speech_checks']))
    if speech_notes:warnings.append(SPEECH_NOTES)
    if test_only:warnings.append('Generated ElevenLabs music is for the test trailer only until ElevenLabs confirms otherwise in writing. This file is not cleared for release.')
    review={'version':'1.0','trailer':contract.require_contract(root),'edit_revision_sha256':revision,'export_sha256':digest,
        'output_path':relative,'status':'pass' if sound_ok else 'revise','recommended_action':'present_to_user' if sound_ok else 'revise_edit',
        'checks':{'technical_probe':probe,'visual_spotcheck':{'frames_sampled':len(samples),'frame_paths':[r['path'] for r in samples],'issues':[]},
            'audio_spotcheck':{'issues':warnings},'promise_preservation':{'render_runtime_used':'remotion','runtime_swap_detected':False,'issues':[]},'subtitle_check':{'subtitles_expected':False,'issues':[]}},
        'issues_found':warnings,'metadata':{'measurements':measured,'boundary_samples':samples,'test_only':test_only,'speech_warnings_advisory':speech_notes,
            'review_scope':'Local technical checks only. Watch and listen; acting, story meaning, rights and every word’s intelligibility are not automatically certified.',
            'compose_review':generic}}
    report={'version':'1.0','trailer':contract.require_contract(root),'edit_revision_sha256':revision,'export_sha256':digest,
        'outputs':[{'path':relative,'format':'mp4','codec':'h264','audio_codec':'aac','resolution':'1920x1080','fps':24,'duration_seconds':probe['duration_seconds'],'file_size_bytes':probe['file_size_bytes']}],
        'verification_notes':warnings,'metadata':{'delivery_id':identity,'measurements_sha256':record_sha256(measured),'test_only':test_only}}
    from schemas.artifacts import validate_artifact
    validate_artifact('render_report',report);validate_artifact('final_review',review)
    value={'id':identity,'render_report':report,'final_review':review}
    from datetime import datetime,timezone
    now=datetime.now(timezone.utc).isoformat()
    # Receipt the local render RECORD, not the movie's content provenance.
    # Identical movie bytes can belong to distinct numbered export records.
    made=receipts.record_generation(root,execution_id=identity,tool='trailer_delivery_record',
        normalized_inputs_hash=revision,output_sha256=record_sha256(value),cost_usd=0,
        started_at=now,finished_at=now,generator_kind='local',local_tool='ffmpeg-remotion',
        local_tool_version='trailer-delivery-1',parameters_hash=record_sha256(saved['timeline']))
    value['generation_receipt_id']=made['receipt_id']
    os.link(target,output)
    _publish(job/'complete.json',value)
    return read_export(root,identity)

def read_export(root,identity):
    root=Path(root);job=_job(root,identity)
    record=json.loads(resolve_input(str((job/'complete.json').relative_to(root)),root).read_text())
    if record.get('id')!=identity:raise ValueError(CHANGED)
    try:
        value={k:record[k] for k in ('id','render_report','final_review')}
        made=receipts.find_generation_by_id(root,record.get('generation_receipt_id'),output_sha256=record_sha256(value))
        if (not made or made.get('tool')!='trailer_delivery_record' or made.get('generator_kind')!='local'
            or made.get('normalized_inputs_hash')!=record['render_report']['edit_revision_sha256']):raise ValueError()
    except (KeyError,TypeError,ValueError):
        raise ValueError('This local render record is missing or changed. Make a fresh final file; an editable report cannot approve a different cut.') from None
    return record

def _artifacts(root,identity,*,remeasure=False):
    root=Path(root);saved,timeline=require_rough(root)
    return _file_artifacts(root,identity,saved,timeline,remeasure=remeasure)

def _file_artifacts(root,identity,saved,timeline,*,remeasure=False):
    item=read_export(root,identity);report=item['render_report'];review=item['final_review']
    if report.get('edit_revision_sha256')!=saved['edit_revision_sha256'] or review.get('edit_revision_sha256')!=saved['edit_revision_sha256']:
        raise ValueError(CHANGED)
    path=resolve_input(report['outputs'][0]['path'],root)
    if path!=_job(root,identity)/'trailer.mp4' or sha256_file(path)!=report.get('export_sha256') or review.get('export_sha256')!=report.get('export_sha256'):raise ValueError(CHANGED)
    if review.get('output_path')!=report['outputs'][0]['path']:raise ValueError(CHANGED)
    measured=review['metadata']['measurements']
    if measured.get('output_sha256')!=report['export_sha256'] or record_sha256(measured)!=report['metadata'].get('measurements_sha256'):raise ValueError('The final measurements changed. Make and review a fresh export.')
    _probe(path,max(r['start_frame']+r['duration_frames'] for r in timeline['picture']))
    for row in review['metadata']['boundary_samples']:
        if sha256_file(resolve_input(row['path'],root))!=row['sha256']:raise ValueError('The final frame samples changed. Make and review a fresh export.')
    if [r['frame'] for r in review['metadata']['boundary_samples']]!=_boundaries(timeline):raise ValueError('The final boundary checks are incomplete. Make a fresh export.')
    if remeasure:
        from tools.audio.audio_mixer import AudioMixer
        directory=_job(root,identity)/('check-'+uuid.uuid4().hex);directory.mkdir()
        tracks=[{**a,'path':str(resolve_input(a['path'],root)),'role':'sfx' if a['kind']=='effect' else a['kind']} for a in timeline['audio']]
        result=AudioMixer()._full_mix({'timeline_tracks':tracks,'target_frames':max(r['start_frame']+r['duration_frames'] for r in timeline['picture']),
            'speech_margin_db':timeline['mix']['speech_margin_db'],'automatic':True,'output_path':str(directory/'mix.wav')})
        if not result.success:raise ValueError('The final sound could not be checked. Make a fresh export.')
        probe,fresh=_measure(root,path,saved,timeline,result.data)
        if fresh!=measured or probe!=review['checks']['technical_probe']:raise ValueError('The final measurements no longer match this file. Make and review a fresh export.')
        if not _sound_ok(fresh) or review.get('status')!='pass':raise ValueError('The final sound check needs changes. Read its findings, adjust the saved cut and make a new file before approval.')
    return {'render_report':report,'final_review':review}

def final_record(root,identity):
    artifacts=_artifacts(root,identity,remeasure=True)
    return contract.approval_record(root,_pin(root),'compose',artifacts)

def request_final(root,identity):
    from lib.media_spending import gate_request
    record=final_record(root,identity)
    return gate_request(root,'artifact_review',identity,record,
        'Watch and listen to this exact final file. Your signature binds its bytes, the saved cut and the measured checks. It permits no upload or spending.',scope='trailer-stage',stage='compose')

def final_state(root,identity):
    try:
        artifacts=_artifacts(root,identity)
        pending='Needs your signature' if artifacts['final_review']['status']=='pass' else 'Sound check needs changes'
        return _approval_state(root,'compose',artifacts,pending,'Approved by you')
    except (ValueError,OSError,KeyError):return 'Needs a fresh look'

def complete_stage(root,stage,identity=None):
    """A signature is necessary; existing upstream checkpoint rules still apply."""
    from lib.checkpoint import write_checkpoint,CheckpointValidationError
    root=Path(root)
    artifacts={'edit_decisions':require_rough(root)[0]} if stage=='edit' else _artifacts(root,identity,remeasure=True)
    contract.require_stage_approval(root,_pin(root),stage,artifacts)
    try:write_checkpoint(root.parent,root.name,stage,'completed',artifacts,pipeline_type='authored-film@1.6',human_approved=True)
    except CheckpointValidationError:
        return 'Your signature is recorded. Earlier stage reviews or the separate story review are still unfinished, so this stage has not been marked finished.'
    return 'Your signed stage approval is recorded.'

def playback(root,identity):
    artifacts=_artifacts(root,identity)
    return resolve_input(artifacts['render_report']['outputs'][0]['path'],Path(root))

def reveal(root,identity):
    if final_state(root,identity)!='Approved by you':raise ValueError('Watch and sign final approval of this exact file before revealing the approved delivery.')
    path=playback(root,identity)
    subprocess.run(['open','-R',str(path)],check=True,timeout=15)
    return {'message':'The approved trailer is saved in this production’s delivery folder and has been shown in Finder.','path':str(path)}

def snapshot(root):
    root=Path(root);saved,timeline=_saved(root);items=[]
    rough=_approval_state(root,'edit',{'edit_decisions':saved},'Needs your signature','Signed by you')
    for marker in sorted(_directory(root).glob('*/complete.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            item=read_export(root,marker.parent.name)
            status='Needs a fresh look'
            if rough=='Signed by you':
                try:
                    artifacts=_file_artifacts(root,item['id'],saved,timeline)
                    pending='Needs your signature' if artifacts['final_review']['status']=='pass' else 'Sound check needs changes'
                    status=_approval_state(root,'compose',artifacts,pending,'Approved by you')
                except (ValueError,OSError,KeyError):pass
            items.append({'id':item['id'],'status':status,'review':item['final_review'],'path':item['render_report']['outputs'][0]['path']})
        except (ValueError,OSError,KeyError):continue
    return {'revision':saved['edit_revision_sha256'],'number':saved['revision_number'],'rough_status':rough,'exports':items}
