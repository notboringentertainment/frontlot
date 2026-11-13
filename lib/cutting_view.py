"""Frame-based cut validation. Never generates media or approves a stage."""
import copy
import math
from pathlib import Path
from lib import trailer_edit,footage_requests,audio_workspace
from lib.pathsafe import resolve_input,sha256_file

def source_problem(medium,number,state):
    label=f'{"Picture" if medium=="picture" else "Sound"} {number}'
    if state=='Declined':reason='This take was declined. Replace or remove this clip.'
    elif state=='Needs a fresh look':reason='This take needs a fresh look. Review a new kept take, or remove this clip.'
    elif medium=='audio':reason='Choose a kept voice, music or sound effect. A picture take cannot be used as sound.'
    else:reason='This picture is no longer kept, is missing or has changed. Replace or remove this clip.'
    return label+' is no longer usable. '+reason

def require_source(root,row,medium,number):
    try:
        take=footage_requests.require_accepted(root,row.get('shot_id'),row.get('take_id')) if medium=='picture' else audio_workspace.require_accepted(root,row.get('take_id'))
        if medium=='audio' and take.get('kind') not in ('speech','music','effect'):raise ValueError()
        return take
    except (ValueError,OSError,KeyError,TypeError,RuntimeError):
        try:
            state=footage_requests.take_state(root,row.get('shot_id'),row.get('take_id')) if medium=='picture' else audio_workspace.take_state(root,audio_workspace.read(root,row.get('take_id')))
        except (ValueError,OSError,KeyError,TypeError,RuntimeError):state=None
        raise ValueError(source_problem(medium,number,state)) from None

def _frame(value,label):
    if type(value) is not int or value<0:raise ValueError(label+' must use non-negative whole frames.')
    return value

def validate_cut(root,timeline,*,verify_files=False,release=False):
    if not isinstance(timeline,dict) or set(timeline)!={'fps','picture','audio'} or type(timeline['fps']) is not int or timeline['fps']!=24:
        raise ValueError('Save picture and sound together at twenty-four frames per second.')
    if not isinstance(timeline['picture'],list) or not 1<=len(timeline['picture'])<=60 or not isinstance(timeline['audio'],list) or len(timeline['audio'])>24:
        raise ValueError('Use one to sixty picture clips and up to twenty-four sound clips.')
    picture=[];audio=[];end=0
    for medium,rows in (('picture',timeline['picture']),('audio',timeline['audio'])):
        for number,supplied in enumerate(rows,1):
            keys={'take_id','start_frame','source_in_frame','source_out_frame'}|({'shot_id'} if medium=='picture' else {'level_db','fade_in_frames','fade_out_frames'})
            if not isinstance(supplied,dict) or set(supplied)!=keys:raise ValueError('Choose a kept take and fill in its timing controls.')
            row=copy.deepcopy(supplied)
            for key in ('start_frame','source_in_frame','source_out_frame'):_frame(row[key],'Clip timing')
            take=require_source(root,row,medium,number)
            start=row['source_in_frame'];stop=row['source_out_frame'];duration=stop-start
            if duration<=0 or stop>math.floor(take['duration_seconds']*24+1e-6):raise ValueError('The source trim is outside the kept take. Choose a shorter source range.')
            row.update(path=take['path'],asset_id=take['sha256'],duration_frames=duration)
            if row['start_frame']+duration>2880:raise ValueError('The cut cannot exceed two minutes.')
            if medium=='picture':
                if row['start_frame']<end:raise ValueError('Picture clips cannot overlap. Change their positions or trims.')
                end=row['start_frame']+duration;row.update(id=row['shot_id'],kind='footage',source_timebase_fps=24);picture.append(row)
            else:
                if take['kind'] not in ('speech','music','effect'):raise ValueError('Choose kept voice, music or sound effects.')
                level=row['level_db']
                if type(level) not in (float,int) or not math.isfinite(level) or not -60<=level<=6:raise ValueError('Set a sound level between minus sixty and plus six decibels.')
                for key in ('fade_in_frames','fade_out_frames'):_frame(row[key],'Each fade')
                if row['fade_in_frames']+row['fade_out_frames']>duration:raise ValueError('The fades must fit inside the sound clip.')
                row.update(kind=take['kind'],test_only=bool(take.get('release_note')))
                if release and row['test_only']:raise ValueError('This music is for the test trailer only. It cannot enter a release export.')
                audio.append(row)
            if verify_files:
                path=resolve_input(take['path'],Path(root))
                if sha256_file(path)!=take['sha256']:raise ValueError('A kept media file is missing or changed. Review a new take.')
    from lib.trailer_contract import require_contract
    if end>round(require_contract(root)['target_duration_seconds']*24)+1:raise ValueError('The cut exceeds this trailer’s signed target. Shorten it or review the target length first.')
    if any(r['start_frame']+r['duration_frames']>end for r in audio):raise ValueError('Sound ends after the picture. Trim it or extend the picture first.')
    return {'fps':24,'picture':picture,'audio':audio,'cards':[]}

def editable(timeline,*,display=False):
    result={'fps':24,'picture':[],'audio':[]}
    for kind in ('picture','audio'):
        keys={'take_id','start_frame','source_in_frame','source_out_frame'}|({'shot_id'} if kind=='picture' else {'level_db','fade_in_frames','fade_out_frames'})
        for row in timeline[kind]:
            if display:
                defaults={'take_id':'','shot_id':'','start_frame':0,'source_in_frame':0,'source_out_frame':row.get('duration_frames',0),'level_db':-6,'fade_in_frames':0,'fade_out_frames':0}
                result[kind].append({k:row.get(k,defaults[k]) for k in keys})
            else:result[kind].append({k:row[k] for k in keys})
    return result

def save_cut(root,timeline,expected_revision):
    root=Path(root)
    # Audio freshness reads the current paper cut under its own edit lock.
    # Verify before acquiring the writer lock, then compare the revision while
    # holding it. Playback verifies all takes again before using any bytes.
    validated=validate_cut(root,timeline,verify_files=True)
    with trailer_edit._edit_lock(root):
        old=trailer_edit._read_current(root);trailer_edit._expect(old,expected_revision)
        if old is None:raise ValueError('Save a checked paper cut before cutting takes.')
        inputs=trailer_edit._inputs(root)
        check=trailer_edit._story_check(root,old['paper_cut'])
        if check['status']!='checked' or old.get('inputs')!=inputs:raise ValueError('Review the story and save a current checked paper cut before cutting takes.')
        value=copy.deepcopy(old);value['timeline']=validated
        value.update(paper_cut_pending=False,unfinished=True,cutting_view=True)
        return trailer_edit._commit(root,value,old,inputs)

def render_preview(root,revision):
    import subprocess,uuid
    root=Path(root);saved=trailer_edit.read_revision(root)
    if not saved or saved['edit_revision_sha256']!=revision:raise ValueError('The saved cut changed. Reopen it before making a preview.')
    timeline=validate_cut(root,editable(saved['timeline']),verify_files=True)
    end=max(p['start_frame']+p['duration_frames'] for p in timeline['picture'])
    args=['ffmpeg','-v','error','-n','-f','lavfi','-i',f'color=c=black:s=640x360:r=24:d={end/24}']
    filters=[];base='0:v';index=1
    for p in timeline['picture']:
        args+=['-i',str(resolve_input(p['path'],root))]
        filters.append(f'[{index}:v]fps=24,trim=start_frame={p["source_in_frame"]}:end_frame={p["source_out_frame"]},setpts=PTS-STARTPTS+{p["start_frame"]}/24/TB,scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1[v{index}]')
        filters.append(f'[{base}][v{index}]overlay=eof_action=pass:enable=\'gte(t,{p["start_frame"]/24})*lt(t,{(p["start_frame"]+p["duration_frames"])/24})\'[p{index}]')
        base=f'p{index}';index+=1
    sounds=[]
    for a in timeline['audio']:
        args+=['-i',str(resolve_input(a['path'],root))]
        chain=f'[{index}:a]aresample=48000,atrim=start_sample={a["source_in_frame"]*2000}:end_sample={a["source_out_frame"]*2000},asetpts=PTS-STARTPTS,volume={a["level_db"]}dB'
        if a['fade_in_frames']:chain+=f',afade=t=in:st=0:d={a["fade_in_frames"]/24}'
        if a['fade_out_frames']:chain+=f',afade=t=out:st={(a["duration_frames"]-a["fade_out_frames"])/24}:d={a["fade_out_frames"]/24}'
        chain+=f',adelay={a["start_frame"]*2000}S:all=1[a{index}]';filters.append(chain);sounds.append(f'[a{index}]');index+=1
    if sounds:filters.append(''.join(sounds)+f'amix=inputs={len(sounds)}:normalize=0,apad,atrim=duration={end/24}[sound]')
    directory=root/'artifacts'/'cut-previews'
    if directory.is_symlink():raise ValueError('Preview files must stay inside this production.')
    directory.mkdir(exist_ok=True);target=directory/(revision+'-'+uuid.uuid4().hex+'.mp4')
    args+=['-filter_complex',';'.join(filters),'-map',f'[{base}]']
    if sounds:args+=['-map','[sound]','-c:a','aac']
    args+=['-c:v','libx264','-pix_fmt','yuv420p','-r','24','-t',str(end/24),'-movflags','+faststart',str(target)]
    subprocess.run(args,capture_output=True,check=True,timeout=180)
    current=trailer_edit.read_revision(root)
    if current['edit_revision_sha256']!=revision:raise ValueError('The cut changed while preparing playback. Make a new preview.')
    validate_cut(root,editable(current['timeline']),verify_files=True)
    return target
