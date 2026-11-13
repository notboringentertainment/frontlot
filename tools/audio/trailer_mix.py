"""One bounded, local speech-first path through the existing mixer."""
import math
from pathlib import Path
import numpy as np
from tools.base_tool import ToolResult

RATE=48000
BLOCK=960  # twenty milliseconds; placement itself remains sample-exact.

def _run(args,**kw):
    import subprocess
    return subprocess.run(args,capture_output=True,check=True,timeout=180,**kw)

def write_audio(path,samples):
    _run(['ffmpeg','-v','error','-n','-f','f32le','-ar','48000','-ac','2','-i','pipe:0','-c:a','pcm_f32le',str(path)],input=np.asarray(samples,dtype='<f4').tobytes())

def _fit(samples,count):
    return np.pad(samples[:count],((0,max(0,count-len(samples))),(0,0)))

def _flat_clip(a):
    high=np.max(np.abs(a),axis=1)>=.9999
    return bool(len(high)>=3 and np.any(high[:-2]&high[1:-1]&high[2:]))

def mix_timeline(tool,inputs):
    from lib.trailer_finishing import decode_audio,loudness_stats,mix_settings
    tracks=inputs['timeline_tracks'];frames=inputs['target_frames']
    if type(frames) is not int or not 1<=frames<=2880:raise ValueError('Keep the mix within the trailer’s two-minute timeline.')
    margin=mix_settings({'speech_margin_db':inputs['speech_margin_db']})['speech_margin_db']
    automatic=inputs.get('automatic',True);count=frames*2000
    if not isinstance(tracks,list) or len(tracks)>24:raise ValueError('Use up to twenty-four kept sound clips in this trailer.')
    for t in tracks:
        if not isinstance(t,dict) or t.get('role') not in ('speech','music','sfx'):
            raise ValueError('Choose kept voice, music or effects for the sound mix.')
        if any(type(t.get(k)) is not int or t[k]<0 for k in ('start_frame','source_in_frame','source_out_frame','fade_in_frames','fade_out_frames')):
            raise ValueError('Sound timing and fades must use non-negative whole frames.')
        duration=t['source_out_frame']-t['source_in_frame']
        if duration<=0 or t['start_frame']+duration>frames or t['fade_in_frames']+t['fade_out_frames']>duration:
            raise ValueError('Keep each sound trim, position and fades inside the cut using whole frames.')
        level=t.get('level_db')
        if type(level) not in (int,float) or not math.isfinite(level) or not -60<=level<=6:
            raise ValueError('Set a sound level between minus sixty and plus six decibels.')
    # Validate every control before any decoder or output starts. Check the
    # selected source only: an unused damaged ending is not part of this cut.
    warnings=[];effects=[]
    for i,t in enumerate(tracks,1):
        samples=decode_audio(t.get('path',''))
        if t['source_out_frame']*2000>len(samples):raise ValueError('The sound trim is outside the local recording. Choose a shorter source range.')
        selected=samples[t['source_in_frame']*2000:t['source_out_frame']*2000]
        if _flat_clip(selected):
            warnings.append(f'Sound {i} already clips or has flat full-scale peaks. Listen to the original and choose another take if it is distorted; turning it down cannot repair the recording.')
        if t['role']=='speech' and np.max(np.abs(selected))*10**(t['level_db']/20)<1e-5:
            warnings.append(f'Sound {i} has no audible speech in its chosen trim. Choose an audible voice take or raise its level, then prepare playback again.')
    target=Path(inputs['output_path']);directory=target.parent/(target.stem+'-parts')
    directory.mkdir()
    def stem(rows,name):
        path=directory/(name+'.wav')
        if rows:
            result=tool._mix({'tracks':rows,'normalize':False,'sum_without_attenuation':True,'float_output':True,'output_path':str(path)})
            if not result.success:raise ValueError('A sound could not be mixed. Check the kept files and source trims, then try again.')
            return _fit(decode_audio(path),count)
        return np.zeros((count,2),dtype=np.float32)
    speech=stem([r for r in tracks if r['role']=='speech'],'speech-source')
    background=stem([r for r in tracks if r['role']!='speech'],'other-source')
    gains=np.ones(math.ceil(count/BLOCK))
    if automatic:
        for i in range(len(gains)):
            s=speech[i*BLOCK:(i+1)*BLOCK];b=background[i*BLOCK:(i+1)*BLOCK]
            sr=math.sqrt(float(np.mean(s*s)));br=math.sqrt(float(np.mean(b*b)))
            if sr>1e-5 and br>0:gains[i]=min(1,sr/(br*10**(margin/20)))
        safe=np.minimum(gains,np.minimum(np.r_[gains[0],gains[:-1]],np.r_[gains[1:],gains[-1]]))
        for i in range(1,len(safe)):
            if safe[i]>safe[i-1]:safe[i]=min(safe[i],safe[i-1]+.12*(safe[i]-safe[i-1]))
        envelope=np.minimum(np.interp(np.arange(count),(np.arange(len(safe))+.5)*BLOCK,safe),np.repeat(gains,BLOCK)[:count])
        background*=envelope[:,None]
    else:envelope=np.ones(count)
    speech_path=directory/'speech.wav';other_path=directory/'other.wav'
    write_audio(speech_path,speech);write_audio(other_path,background)
    for i,t in enumerate((r for r in tracks if r['role']=='sfx'),1):
        effect=stem([t],f'effect-{i}-source')*envelope[:,None]
        ref=directory/f'effect-{i}.wav';write_audio(ref,effect)
        effects.append({'path':str(ref),'number':i,'start_frame':t['start_frame'],'duration_frames':t['source_out_frame']-t['source_in_frame']})
    summed=directory/'summed.wav'
    result=tool._mix({'tracks':[{'path':str(speech_path)},{'path':str(other_path)}],'normalize':False,'sum_without_attenuation':True,'float_output':True,'output_path':str(summed)})
    if not result.success:raise ValueError('The local mix failed. Check the sound files and try again.')
    stats=loudness_stats(summed)
    if automatic and math.isfinite(float(stats['input_i'])):
        graph=tool._loudnorm_filter({'loudnorm_target':-16,'true_peak_target':-2},'0:a','out')
        measured=f':measured_I={stats["input_i"]}:measured_TP={stats["input_tp"]}:measured_LRA={stats["input_lra"]}:measured_thresh={stats["input_thresh"]}:offset={stats["target_offset"]}:linear=true'
        graph=graph.replace('[out]',measured+'[out]')
        _run(['ffmpeg','-v','error','-n','-i',str(summed),'-filter_complex',graph,'-map','[out]','-ar','48000','-ac','2','-c:a','pcm_f32le',str(target)])
    else:write_audio(target,speech+background)
    return ToolResult(success=True,data={'speech_path':str(speech_path),'background_path':str(other_path),'effects':effects,'target_frames':frames,'speech_margin_db':margin,'source_warnings':warnings,'automatic':automatic},artifacts=[str(target)])
