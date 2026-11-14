"""Saved trailer finish and checks of the actual encoded picture and sound."""
import json,math,subprocess
from pathlib import Path
import numpy as np
from lib.pathsafe import sha256_file

RATE=48000
PASSAGE=4800  # one hundred milliseconds, shared by detection and review
AUDIBLE_RMS=.001  # minus sixty dBFS; also the silence floor
QUIET_RMS=.01  # below minus forty dBFS: audible is not a claim of clarity
NEUTRAL={'brightness':0,'warmth':0,'contrast':0,'colour':0}

def passage_levels(samples):
    return np.array([math.sqrt(float(np.mean(samples[i:i+PASSAGE].astype(float)**2))) for i in range(0,len(samples),PASSAGE)])

def voice_warning(number,start,end):
    return f'Voice in sound {number} has no audible speech at its saved level from {time_words(start)} to {time_words(end)}. Choose an audible voice take or raise its level, save and prepare playback again; listen there before approval.'

def mix_settings(value):
    if not isinstance(value,dict) or set(value)!={'speech_margin_db'}:
        raise ValueError('Set the speech margin between six and twenty-four decibels, then save the cut.')
    n=value['speech_margin_db']
    if type(n) not in (int,float) or not math.isfinite(n) or not 6<=n<=24:
        raise ValueError('Set the speech margin between six and twenty-four decibels, then save the cut.')
    return {'speech_margin_db':n}

def decode_audio(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Choose an existing local file from a kept sound take before preparing playback.')
    result=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-ar','48000','-ac','2','-f','f32le','pipe:1'],capture_output=True,timeout=180)
    if result.returncode:raise ValueError('This file has no readable sound. Choose a kept sound take and prepare playback again.')
    data=np.frombuffer(result.stdout,dtype='<f4').reshape(-1,2).copy()
    if not np.isfinite(data).all():raise ValueError('This sound contains invalid samples. Choose another kept take.')
    return data

def loudness_stats(path):
    result=subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(path),'-vn','-af','loudnorm=I=-16:LRA=11:TP=-2:print_format=json','-f','null','-'],capture_output=True,text=True,timeout=180)
    if result.returncode:raise ValueError('The sound could not be measured. Check the kept files and prepare playback again.')
    start=result.stderr.rfind('{');end=result.stderr.rfind('}')
    try:return json.loads(result.stderr[start:end+1])
    except (ValueError,KeyError):raise ValueError('The loudness check did not return a result. Prepare playback again.') from None

def time_words(frame):
    seconds=frame/24
    return f'{int(seconds//60)}:{seconds%60:06.3f}'

def _finite(value):
    n=float(value)
    return n if math.isfinite(n) else None

def _fit(a,n):return np.pad(a[:n],((0,max(0,n-len(a))),(0,0)))

def _intervals(mask,block,total,min_seconds=0):
    rows=[];start=None
    for i,yes in enumerate([*mask,False]):
        if yes and start is None:start=i*block
        if not yes and start is not None:
            end=min(i*block,total)
            if end-start>=RATE*min_seconds:rows.append({'start_frame':round(start/2000),'end_frame':round(end/2000)})
            start=None
    return rows

def _speech_checks(actual,speech,background,margin):
    checks=[];block=PASSAGE
    for start in range(0,len(actual),block):
        s=speech[start:start+block:8].reshape(-1).astype(float);b=background[start:start+block:8].reshape(-1).astype(float)
        if not len(s) or np.sqrt(np.mean(s*s))<AUDIBLE_RMS:continue
        y=actual[start:start+block:8].reshape(-1).astype(float);matrix=np.column_stack([s,b])
        result={'start_frame':round(start/2000),'end_frame':round(min(len(actual),start+block)/2000),'margin_db':None}
        if np.sqrt(np.mean(b*b))>1e-7 and np.linalg.cond(matrix)>1e5:
            result['status']='uncertain';checks.append(result);continue
        coefficients=np.linalg.lstsq(matrix,y,rcond=None)[0]
        voice=s*max(0,coefficients[0]);bed=b*max(0,coefficients[1]);residual=y-(s*coefficients[0]+b*coefficients[1])
        vr=np.sqrt(np.mean(voice*voice));br=np.sqrt(np.mean(bed*bed)+np.mean(residual*residual))
        measured=20*math.log10(max(vr,1e-12)/max(br,1e-12))
        status='unheard' if vr<AUDIBLE_RMS else 'hard_to_hear' if measured<margin-1 else 'quiet' if vr<QUIET_RMS else 'clear'
        result.update(margin_db=round(measured,2),voice_rms_dbfs=round(20*math.log10(max(vr,1e-12)),2),status=status);checks.append(result)
    return checks

def _effect_check(actual,ref,number,start_frame=0,*,nuisance=()):
    # Downsampling is for measurement only. Rendering and placement stay at
    # 48 kHz. A distinctive reference is required before claiming a match.
    channel=int(np.argmax(np.sum(ref.astype(float)**2,axis=0)))
    y=actual[::8,channel].astype(float);r=ref[::8,channel].astype(float)
    # The spoken reference can dominate a deliberately ducked effect. Remove
    # only the independently known voice and music, never the effect being
    # tested or the bus containing it. A misplaced effect must stay misplaced.
    if nuisance:
        columns=np.column_stack([x[::8,channel].astype(float) for x in nuisance])
        for start in range(0,len(y),PASSAGE//8):
            stop=min(len(y),start+PASSAGE//8);matrix=columns[start:stop]
            if np.any(matrix):
                coefficients=np.linalg.lstsq(matrix,y[start:stop],rcond=None)[0]
                if np.isfinite(coefficients).all():
                    y[start:stop]-=sum(matrix[:,i]*coefficients[i] for i in range(matrix.shape[1]))
    active=np.flatnonzero(np.abs(r)>1e-5)
    if not len(active):return {'number':number,'status':'uncertain','expected_frame':start_frame,'observed_frame':None}
    first,last=int(active[0]),int(active[-1])+1;r=r[first:last]
    expected=round(first/6000*24)
    if len(r)>len(y):return {'number':number,'status':'uncertain','expected_frame':expected,'observed_frame':None}
    # Match changes in the waveform, so a steady low music note cannot hide
    # a distinctive quiet effect. This changes measurement, never playback.
    y=np.diff(y,prepend=y[0]);r=np.diff(r,prepend=r[0])
    size=1<<(len(y)+len(r)-1).bit_length()
    corr=np.fft.irfft(np.fft.rfft(y,size)*np.conj(np.fft.rfft(r,size)),size)[:len(y)-len(r)+1]
    cumulative=np.r_[0,np.cumsum(y*y)];energy=cumulative[len(r):]-cumulative[:-len(r)]
    scores=np.abs(corr)/np.sqrt(np.maximum(energy,1e-20)*max(float(np.sum(r*r)),1e-20))
    best=int(np.argmax(scores));score=float(scores[best]);other=scores.copy()
    other[max(0,best-250):min(len(other),best+251)]=0
    certain=score>.2 and (not len(other) or np.max(other)<score*.9)
    observed=round(best/6000*24)
    return {'number':number,'expected_frame':expected,'observed_frame':observed,'confidence':round(score,3),
            'status':('on_time' if abs(observed-expected)<=1 else 'wrong_time') if certain else 'uncertain'}

def review_output(path,refs):
    path=Path(path);stats=loudness_stats(path)
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],timeout=30))
    audio=next((s for s in probe['streams'] if s['codec_type']=='audio'),{})
    n=refs['target_frames']*2000;actual=_fit(decode_audio(path),n)
    speech=_fit(decode_audio(refs['speech_path']),n);bed=_fit(decode_audio(refs['background_path']),n)
    checks=[];voices=[]
    for row in refs.get('voices',[]):
        voice=_fit(decode_audio(row['path']),n)
        passages=_speech_checks(actual,voice,bed+speech-voice,refs['speech_margin_db'])
        for p in passages:p['sound_number']=row['number']
        checks.extend(passages)
        statuses={p['status'] for p in passages}
        status=next((s for s in ('unheard','uncertain','hard_to_hear','quiet') if s in statuses),'clear') if passages else 'unmeasured'
        voices.append({'number':row['number'],'start_frame':row['start_frame'],'end_frame':row['end_frame'],'status':status,
                       'minimum_margin_db':min((p['margin_db'] for p in passages if p['margin_db'] is not None),default=None)})
    music=_fit(decode_audio(refs['music_path']),n)
    effects=[_effect_check(actual,_fit(decode_audio(e['path']),n),e['number'],e.get('start_frame',0),nuisance=(speech,music)) for e in refs.get('effects',[])]
    block=PASSAGE;quiet=passage_levels(actual)<AUDIBLE_RMS
    silences=_intervals(quiet,block,n,.25)
    integrated=_finite(stats['input_i']);peak=_finite(stats['input_tp'])
    channels=audio.get('channels',0);rate=int(audio.get('sample_rate',0))
    on_target=integrated is not None and abs(integrated+16)<=1 and peak is not None and peak<=-1 and channels==2 and rate==48000
    messages=list(refs.get('source_warnings',[]))
    messages.append('Loudness is on target for this trailer.' if integrated is not None and abs(integrated+16)<=1 else 'The overall sound is too quiet or too loud for the target. Adjust the sound levels and prepare playback again.' if integrated is not None else 'No audible sound was measured. Place kept sound in the cut, then prepare playback again.')
    if integrated is not None:messages.append('Playback peaks are safely below the ceiling.' if peak is not None and peak<=-1 else 'Playback peaks exceed the ceiling or could not be measured. Lower loud sounds and prepare playback again.')
    if channels!=2 or rate!=48000:messages.append('The output is not the required stereo sound. Prepare playback again before reviewing it.')
    for row in voices:
        if row['status']=='quiet':
            messages.append(f'Voice in sound {row["number"]} is present but quiet from {time_words(row["start_frame"])} to {time_words(row["end_frame"])}. Listen there; raise its level if the words are hard to hear, then save and prepare playback again.')
        elif row['status'] in ('unheard','unmeasured','uncertain') and not any(f'Voice in sound {row["number"]} has no audible speech' in m for m in messages):
            messages.append(f'Voice in sound {row["number"]} could not be confirmed as audibly present from {time_words(row["start_frame"])} to {time_words(row["end_frame"])}. Listen there, choose a clearer take or raise its level, then save and prepare playback again.')
    hard=[x for x in checks if x['status'] in ('hard_to_hear','uncertain','unheard')]
    if hard:
        start=hard[0]['start_frame'];end=hard[-1]['end_frame']
        messages.append(f'Speech is hard to hear from {time_words(start)} to {time_words(end)}.' if any(x['status'] in ('hard_to_hear','unheard') for x in hard) else f'Speech balance cannot be confirmed from {time_words(start)} to {time_words(end)} because the sounds are too similar.')
        messages.append('Lower the other sounds or choose a clearer voice take, then listen again.')
    elif checks and voices and all(row['status']=='clear' for row in voices):
        minimum=min(p['margin_db'] for p in checks if p['margin_db'] is not None)
        messages.append(f'The voice takes contain audible sound above the other sounds in the measured passages. The lowest measured margin is {minimum:.1f} decibels. Listen to confirm every word is clear.')
    for interval in silences:messages.append(f'Silence from {time_words(interval["start_frame"])} to {time_words(interval["end_frame"])}. Check that this pause is deliberate; add or move a kept sound if it is not.')
    for effect in effects:
        number=effect['number'];expected=time_words(effect['expected_frame'])
        if effect['status']=='on_time':messages.append(f'Effect {number} is heard at its saved time near {expected}.')
        elif effect['status']=='wrong_time':messages.append(f'Effect {number} is heard near {time_words(effect["observed_frame"])} instead of {expected}. Check its placement and prepare playback again.')
        else:messages.append(f'Effect {number} could not be identified confidently near {expected}. Listen at that moment and choose a more distinct take if needed.')
    return {'output_sha256':sha256_file(path),'source_warnings':list(refs.get('source_warnings',[])),'integrated_lufs':integrated,'true_peak_dbtp':peak,'channels':channels,'sample_rate':rate,
            'on_target':on_target,'speech_checks':checks,'voice_checks':voices,'effect_checks':effects,'silences':silences,'messages':messages}
