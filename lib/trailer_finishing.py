"""Saved trailer finish and checks of the actual encoded picture and sound."""
import json,math,subprocess
from pathlib import Path
import numpy as np
from lib.pathsafe import sha256_file

RATE=48000
NEUTRAL={'brightness':0,'warmth':0,'contrast':0,'colour':0}

def mix_settings(value):
    if not isinstance(value,dict) or set(value)!={'speech_margin_db'}:
        raise ValueError('Set the speech margin between six and twenty-four decibels, then save the cut.')
    n=value['speech_margin_db']
    if type(n) not in (int,float) or not math.isfinite(n) or not 6<=n<=24:
        raise ValueError('Set the speech margin between six and twenty-four decibels, then save the cut.')
    return {'speech_margin_db':n}

def decode_audio(path):
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
    checks=[];block=4800
    for start in range(0,len(actual),block):
        s=speech[start:start+block:8].reshape(-1).astype(float);b=background[start:start+block:8].reshape(-1).astype(float)
        if not len(s) or np.sqrt(np.mean(s*s))<1e-5:continue
        y=actual[start:start+block:8].reshape(-1).astype(float);matrix=np.column_stack([s,b])
        result={'start_frame':round(start/2000),'end_frame':round(min(len(actual),start+block)/2000),'margin_db':None}
        if np.sqrt(np.mean(b*b))>1e-7 and np.linalg.cond(matrix)>1e5:
            result['status']='uncertain';checks.append(result);continue
        coefficients=np.linalg.lstsq(matrix,y,rcond=None)[0]
        voice=s*max(0,coefficients[0]);bed=b*max(0,coefficients[1]);residual=y-(s*coefficients[0]+b*coefficients[1])
        vr=np.sqrt(np.mean(voice*voice));br=np.sqrt(np.mean(bed*bed)+np.mean(residual*residual))
        measured=20*math.log10(max(vr,1e-12)/max(br,1e-12))
        result.update(margin_db=round(measured,2),status='clear' if vr>1e-6 and measured>=margin-1 else 'hard_to_hear');checks.append(result)
    return checks

def _effect_check(actual,ref,number):
    # Downsampling is for measurement only. Rendering and placement stay at
    # 48 kHz. A distinctive reference is required before claiming a match.
    y=actual.mean(axis=1)[::8].astype(float);r=ref.mean(axis=1)[::8].astype(float)
    active=np.flatnonzero(np.abs(r)>1e-5)
    if not len(active):return {'number':number,'status':'uncertain','expected_frame':0,'observed_frame':None}
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
    checks=_speech_checks(actual,speech,bed,refs['speech_margin_db'])
    effects=[_effect_check(actual,_fit(decode_audio(e['path']),n),e['number']) for e in refs.get('effects',[])]
    block=2400;quiet=[float(np.sqrt(np.mean(actual[i:i+block]**2)))<.001 for i in range(0,n,block)]
    silences=_intervals(quiet,block,n,.25)
    integrated=_finite(stats['input_i']);peak=_finite(stats['input_tp'])
    channels=audio.get('channels',0);rate=int(audio.get('sample_rate',0))
    on_target=integrated is not None and abs(integrated+16)<=1 and peak is not None and peak<=-1 and channels==2 and rate==48000
    messages=list(refs.get('source_warnings',[]))
    messages.append('Loudness is on target for this trailer.' if integrated is not None and abs(integrated+16)<=1 else 'The overall sound is too quiet or too loud for the target. Adjust the sound levels and prepare playback again.' if integrated is not None else 'No audible sound was measured. Place kept sound in the cut, then prepare playback again.')
    messages.append('Playback peaks are safely below the ceiling.' if peak is not None and peak<=-1 else 'Playback peaks exceed the ceiling or could not be measured. Lower loud sounds and prepare playback again.')
    if channels!=2 or rate!=48000:messages.append('The output is not the required stereo sound. Prepare playback again before reviewing it.')
    hard=[x for x in checks if x['status']!='clear']
    if hard:
        start=hard[0]['start_frame'];end=hard[-1]['end_frame']
        messages.append(f'Speech is hard to hear from {time_words(start)} to {time_words(end)}.' if any(x['status']=='hard_to_hear' for x in hard) else f'Speech balance cannot be confirmed from {time_words(start)} to {time_words(end)} because the sounds are too similar.')
        messages.append('Lower the other sounds or choose a clearer voice take, then listen again.')
    elif checks:messages.append(f'Speech sits clearly above the other sounds in measured passages, with about {refs["speech_margin_db"]} decibels of space. Listen to confirm every word is clear.')
    for interval in silences:messages.append(f'Silence from {time_words(interval["start_frame"])} to {time_words(interval["end_frame"])}. Check that this pause is deliberate; add or move a kept sound if it is not.')
    for effect in effects:
        number=effect['number'];expected=time_words(effect['expected_frame'])
        if effect['status']=='on_time':messages.append(f'Effect {number} is heard at its saved time near {expected}.')
        elif effect['status']=='wrong_time':messages.append(f'Effect {number} is heard near {time_words(effect["observed_frame"])} instead of {expected}. Check its placement and prepare playback again.')
        else:messages.append(f'Effect {number} could not be identified confidently near {expected}. Listen at that moment and choose a more distinct take if needed.')
    return {'output_sha256':sha256_file(path),'integrated_lufs':integrated,'true_peak_dbtp':peak,'channels':channels,'sample_rate':rate,
            'on_target':on_target,'speech_checks':checks,'effect_checks':effects,'silences':silences,'messages':messages}
