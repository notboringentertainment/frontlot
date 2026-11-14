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

def validate_cut(root,timeline,*,verify_files=False,release=False,require_card_approval=True):
    if not isinstance(timeline,dict) or not {'fps','picture','audio'}<=set(timeline) or set(timeline)-{'fps','picture','audio','cards','mix'} or type(timeline['fps']) is not int or timeline['fps']!=24:
        raise ValueError('Save picture and sound together at twenty-four frames per second.')
    if not isinstance(timeline['picture'],list) or not 1<=len(timeline['picture'])<=60 or not isinstance(timeline['audio'],list) or len(timeline['audio'])>24:
        raise ValueError('Use one to sixty picture clips and up to twenty-four sound clips.')
    picture=[];audio=[];end=0
    from lib.trailer_finishing import mix_settings,NEUTRAL
    from tools.enhancement.color_grade import ColorGrade
    mix=mix_settings(timeline.get('mix',{'speech_margin_db':12}))
    for medium,rows in (('picture',timeline['picture']),('audio',timeline['audio'])):
        for number,supplied in enumerate(rows,1):
            keys={'take_id','start_frame','source_in_frame','source_out_frame'}|({'shot_id'} if medium=='picture' else {'level_db','fade_in_frames','fade_out_frames'})
            allowed=keys|({'colour'} if medium=='picture' else set())
            if not isinstance(supplied,dict) or not keys<=set(supplied) or set(supplied)-allowed:raise ValueError('Choose a kept take and fill in its timing controls.')
            row=copy.deepcopy(supplied)
            for key in ('start_frame','source_in_frame','source_out_frame'):_frame(row[key],'Clip timing')
            take=require_source(root,row,medium,number)
            start=row['source_in_frame'];stop=row['source_out_frame'];duration=stop-start
            if duration<=0 or stop>math.floor(take['duration_seconds']*24+1e-6):raise ValueError('The source trim is outside the kept take. Choose a shorter source range.')
            row.update(path=take['path'],asset_id=take['sha256'],duration_frames=duration)
            if row['start_frame']+duration>2880:raise ValueError('The cut cannot exceed two minutes.')
            if medium=='picture':
                row['colour']=ColorGrade.trailer_adjustments(row.get('colour',NEUTRAL))
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
    from lib.trailer_titles import validate_cards
    cards=validate_cards(root,timeline.get('cards',[]),end,require_approval=require_card_approval)
    return {'fps':24,'picture':picture,'audio':audio,'cards':cards,'mix':mix}

def editable(timeline,*,display=False):
    from lib.trailer_finishing import NEUTRAL
    import copy
    result={'fps':24,'picture':[],'audio':[]}
    for kind in ('picture','audio'):
        keys={'take_id','start_frame','source_in_frame','source_out_frame'}|({'shot_id'} if kind=='picture' else {'level_db','fade_in_frames','fade_out_frames'})
        for row in timeline[kind]:
            if display:
                defaults={'take_id':'','shot_id':'','start_frame':0,'source_in_frame':0,'source_out_frame':row.get('duration_frames',0),'level_db':-6,'fade_in_frames':0,'fade_out_frames':0}
                result[kind].append({k:row.get(k,defaults[k]) for k in keys})
            else:result[kind].append({k:row[k] for k in keys})
            if kind=='picture':result[kind][-1]['colour']=copy.deepcopy(row.get('colour',NEUTRAL))
    result['mix']=copy.deepcopy(timeline.get('mix',{'speech_margin_db':12}))
    if timeline.get('cards'):
        from lib.trailer_titles import CARD_FIELDS
        result['cards']=[{k:r[k] for k in CARD_FIELDS} for r in timeline['cards']]
    return result

def save_cut(root,timeline,expected_revision):
    root=Path(root)
    # Audio freshness reads the current paper cut under its own edit lock.
    # Verify before acquiring the writer lock, then compare the revision while
    # holding it. Playback verifies all takes again before using any bytes.
    validated=validate_cut(root,timeline,verify_files=True,require_card_approval=False)
    with trailer_edit._edit_lock(root):
        old=trailer_edit._read_current(root);trailer_edit._expect(old,expected_revision)
        if old is None:raise ValueError('Save a checked paper cut before cutting takes.')
        inputs=trailer_edit._inputs(root)
        check=trailer_edit._story_check(root,old['paper_cut'])
        if check['status']!='checked' or old.get('inputs')!=inputs:raise ValueError('Review the story and save a current checked paper cut before cutting takes.')
        value=copy.deepcopy(old);value['timeline']=validated
        value.update(paper_cut_pending=False,unfinished=True,cutting_view=True)
        return trailer_edit._commit(root,value,old,inputs)

def render_preview(root,revision,comparison='after'):
    import subprocess,uuid,json
    from lib.trailer_finishing import review_output,NEUTRAL
    from lib.pathsafe import validate_output_parent
    from lib.canonical_json import record_sha256
    from tools.enhancement.color_grade import ColorGrade
    from tools.audio.audio_mixer import AudioMixer
    if comparison not in ('before','after'):raise ValueError('Choose before or after, then prepare the saved cut again.')
    root=Path(root);saved=trailer_edit.read_revision(root)
    if not saved or saved['edit_revision_sha256']!=revision:raise ValueError('The saved cut changed. Reopen it before making a preview.')
    timeline=validate_cut(root,editable(saved['timeline']),verify_files=True)
    if comparison=='after' and 'mix' not in saved['timeline']:
        raise ValueError('Save the colour and sound settings first, then prepare balanced playback.')
    end=max(p['start_frame']+p['duration_frames'] for p in timeline['picture'])
    directory=root/'artifacts'/'cut-previews'
    validate_output_parent(directory,root);directory.mkdir(exist_ok=True)
    target=validate_output_parent(directory/(revision+'-'+uuid.uuid4().hex+'.mp4'),root)
    mixfile=target.with_suffix('.wav')
    tracks=[{**a,'path':str(resolve_input(a['path'],root)),'role':'sfx' if a['kind']=='effect' else a['kind']} for a in timeline['audio']]
    try:
        mixed=AudioMixer()._full_mix({'timeline_tracks':tracks,'target_frames':end,'speech_margin_db':timeline['mix']['speech_margin_db'],'automatic':comparison=='after','output_path':str(mixfile)})
    except subprocess.SubprocessError:
        raise ValueError('The local sound mix failed. Check the kept files and trims, then prepare playback again.') from None
    if not mixed.success:raise ValueError('The local sound mix failed. Check the kept sounds, then prepare playback again.')
    args=['ffmpeg','-v','error','-n','-filter_complex_threads','1','-f','lavfi','-i',f'color=c=black:s=640x360:r=24:d={end/24}']
    filters=[];base='0:v';index=1
    colour_notes=[];untagged=[];layer=None
    for p in timeline['picture']:
        source=resolve_input(p['path'],root)
        try:probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_streams','-of','json',str(source)],timeout=30))['streams'][0]
        except (subprocess.SubprocessError,KeyError,IndexError):raise ValueError('A picture could not be read. Choose another kept take and prepare playback again.') from None
        grade=ColorGrade()._build_filter({'trailer_adjustments':p['colour'] if comparison=='after' else NEUTRAL,'source_colour':probe})
        if probe.get('color_space') not in ('bt709','bt470bg','smpte170m'):
            untagged.append(index)
        args+=['-i',str(source)]
        filters.append(f'[{index}:v]fps=24,trim=start_frame={p["source_in_frame"]}:end_frame={p["source_out_frame"]},setpts=PTS-STARTPTS+{p["start_frame"]}/24/TB,scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1[v{index}]')
        filters[-1]=filters[-1].replace(f'[v{index}]',f',{grade}[v{index}]')
        filters.append(f'[{base}][v{index}]overlay=eof_action=pass:enable=\'gte(t,{p["start_frame"]/24})*lt(t,{(p["start_frame"]+p["duration_frames"])/24})\'[p{index}]')
        base=f'p{index}';index+=1
    audio_index=index;args+=['-i',str(mixfile)];index+=1
    if timeline['cards']:
        from lib.trailer_titles import render_layer
        layer=render_layer(root,timeline['cards'],end,directory)
        args+=['-i',str(layer)]
        filters.append(f'[{base}][{index}:v]overlay=eof_action=pass:format=auto[titled]')
        base='titled'
    filters.append(f'[{base}]setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[finished]')
    base='finished'
    args+=['-filter_complex',';'.join(filters),'-map',f'[{base}]']
    args+=['-map',f'{audio_index}:a','-c:a','aac','-b:a','192k','-ar','48000','-ac','2']
    args+=['-c:v','libx264','-pix_fmt','yuv420p','-r','24','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-color_range','tv','-t',str(end/24),'-movflags','+faststart',str(target)]
    try:subprocess.run(args,capture_output=True,check=True,timeout=180)
    except subprocess.SubprocessError:raise ValueError('The local picture preview failed. Check the kept picture files and prepare playback again.') from None
    review=review_output(target,mixed.data)
    review.update(edit_revision_sha256=revision,settings_sha256=record_sha256(saved['timeline']),comparison=comparison)
    if untagged:
        numbers=' and '.join(map(str,untagged)) if len(untagged)<3 else ', '.join(map(str,untagged[:-1]))+' and '+str(untagged[-1])
        colour_notes.append(f'Picture {numbers} has no recognised colour label. It is treated as ordinary standard-range picture; compare it with its original.' if len(untagged)==1 else f'Pictures {numbers} have no recognised colour label. They are treated as ordinary standard-range picture; compare them with their originals.')
    review['messages']+=colour_notes
    with target.with_suffix('.review.json').open('x') as file:json.dump(review,file,allow_nan=False,indent=2)
    if comparison=='after' and review['true_peak_dbtp'] is not None and review['true_peak_dbtp']>-1:
        from lib.cut_preview_files import finish_preview
        finish_preview(root,target,mixfile,mixed.data,layer,rejected=True)
        raise ValueError('The balanced preview still peaks above the ceiling. Lower the loud sounds, save and prepare playback again.')
    current=trailer_edit.read_revision(root)
    if current['edit_revision_sha256']!=revision:raise ValueError('The cut changed while preparing playback. Make a new preview.')
    validate_cut(root,editable(current['timeline']),verify_files=True)
    from lib.cut_preview_files import finish_preview
    finish_preview(root,target,mixfile,mixed.data,layer)
    return target
