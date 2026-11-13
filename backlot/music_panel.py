"""Music controls use the existing audio boundary and existing signature gates."""
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from lib import audio_workspace as audio
from backlot.voice_panel import root_checked,_gate_id,account_status

MODELS={'music':'music_v1','effect':'eleven_text_to_sound_v3'}
_choices={}
_choice_lock=threading.Lock()

def save_brief(root,data):
    root=root_checked(root)
    if not isinstance(data,dict) or set(data)!={'text','seconds','brief_id'}:
        raise ValueError('Use the music controls for the exact brief and length. Music is always instrumental.')
    identity=audio.save_music_brief(root,data['text'],data['seconds'],data['brief_id'])
    return {'brief_id':identity,'message':'Music brief saved. Prepare a spending card to request a new take.'}

def save_effect(root,data):
    root=root_checked(root)
    if not isinstance(data,dict) or set(data)!={'text','seconds','loop','start_frame','shot_index','brief_id'}:
        raise ValueError('Use the effect controls for the description, moment, length and loop choice.')
    identity=audio.save_effect_brief(root,data['text'],data['seconds'],data['loop'],data['start_frame'],data['shot_index'],data['brief_id'])
    return {'brief_id':identity,'message':'Effect brief saved. Prepare a spending card to request a new take.'}

def prepare_music(root,data):return prepare_cue(root,data,kind='music')

def prepare_cue(root,data,*,kind):
    root=root_checked(root)
    if not isinstance(data,dict) or set(data)!={'brief_id'}:
        raise ValueError('Save the music brief, then prepare its spending card with the music controls.')
    account=account_status()
    if not account['configured']:raise ValueError(account['message'])
    from lib.project_config import load_current_verified_project_config,ProjectConfigError
    config=load_current_verified_project_config(root)
    try:config.require_egress('elevenlabs','effect_prompt' if kind=='effect' else 'music_prompt')
    except ProjectConfigError:
        if kind=='effect':raise ValueError('Sign permission to send sound-effect descriptions in the audio settings before preparing this request.') from None
        raise ValueError('Sign the audio account settings to permit instrumental music briefs before preparing a music request.') from None
    record=audio.prepare_request(root,kind,MODELS[kind],brief_id=data['brief_id'])
    return {'request':record,'gate_request_id':_gate_id(root,record['request_id'],'audio_spend')}

def send(root,identity,*,kind='music'):
    from tools.audio.music_gen import MusicGen
    from tools.audio.elevenlabs_sfx import ElevenLabsSFX
    root=root_checked(root)
    account=account_status()
    if not account['configured']:return {'success':False,'message':account['message']}
    tool=ElevenLabsSFX() if kind=='effect' else MusicGen()
    result=tool.execute({'project_dir':str(root),'audio_request_id':identity})
    return {'success':result.success,**({'take':result.data['take']} if result.success else {'message':result.error+' Review the request and signed settings before trying again.'})}

def decide(root,identity,decision,*,kind='music'):
    root=root_checked(root)
    if audio.read(root,identity).get('kind')!=kind:raise ValueError('Choose a take in its matching audio controls.')
    return audio.request_decision(root,identity,decision)

def current_brief(root,*,kind='music'):
    directory=audio._checked_path(Path(root)/'.audio-workspace'/('effects' if kind=='effect' else 'briefs'))
    paths=sorted(directory.glob('*.json'),key=lambda p:p.lstat().st_mtime_ns,reverse=True)
    if not paths:return None
    identity=paths[0].stem
    value=audio.binding_source(root,{'kind':kind,'brief_id':identity})
    return {'brief_id':identity,**value}

def choose_file(*,kind='music'):
    if sys.platform!='darwin':raise ValueError('Audio file selection needs the Mac app. Open this board on the Mac running Front Lot.')
    try:
        prompt='Choose a sound effect for this trailer' if kind=='effect' else 'Choose music for this trailer'
        result=subprocess.run(['/usr/bin/osascript','-e',f'POSIX path of (choose file with prompt "{prompt}" of type {{"public.audio"}})'],capture_output=True,text=True,timeout=120)
    except (OSError,subprocess.TimeoutExpired):
        raise ValueError('The audio chooser could not finish. Choose a file again on the Mac running Front Lot.') from None
    if result.returncode:
        if '-128' in result.stderr:return None
        raise ValueError('The audio chooser could not open. Allow Front Lot to show the Mac file chooser, then try again.')
    value=result.stdout.rstrip('\r\n')
    if not value:raise ValueError('No readable file was selected. Choose an audio file again.')
    return value

def choose(root,*,kind='music'):
    root=root_checked(root)
    selected=choose_file(kind=kind) if kind=='effect' else choose_file()
    if selected is None:return {'cancelled':True}
    path=Path(selected).expanduser()
    if not path.is_absolute() or '..' in path.parts:raise ValueError('Choose a direct audio file without path tricks or links.')
    audio._checked_path(path)
    if not path.is_file():raise ValueError('Choose a plain audio file without links.')
    identity=str(uuid.uuid4())
    with _choice_lock:
        now=time.time()
        for old in list(_choices):
            if _choices[old]['expires_at']<=now:_choices.pop(old)
        _choices[identity]={'root':str(root),'path':str(path),'expires_at':now+900,'kind':kind}
    return {'choice_id':identity,'name':path.name}

def import_selected(root,data,*,kind='music'):
    root=root_checked(root)
    noun='effect' if kind=='effect' else 'music'
    if not isinstance(data,dict) or set(data)!=({'choice_id','source','licence_note'}|({'brief_id'} if kind=='effect' else set())):
        raise ValueError(f'Choose a {noun} file and fill in where it came from and its licence note.')
    if not isinstance(data['choice_id'],str):raise ValueError(f'Choose the {noun} file again. Its selection could not be read.')
    with _choice_lock:
        choice=_choices.get(data['choice_id'])
        if not choice or choice['root']!=str(root) or choice['expires_at']<=time.time() or choice['kind']!=kind:
            raise ValueError(f'Choose the {noun} file again. Its selection is missing, expired or belongs to another trailer or kind of audio.')
        try:take=audio.import_music(root,choice['path'],data['source'],data['licence_note'],kind=kind,brief_id=data.get('brief_id'))
        except (subprocess.SubprocessError,KeyError):
            raise ValueError(f'This file could not be read as playable {noun}. Choose a supported audio file and try again.') from None
        _choices.pop(data['choice_id'])
    return {'take':take,'message':('Effect' if kind=='effect' else 'Music')+' brought in at no charge. Listen, then sign keeping or declining this take.'}

def mount(app,require_writer,safe_root,invalidate,*,kind='music'):
    import asyncio
    from fastapi import HTTPException,Request
    from functools import partial
    prefix='/api/project/{project_id}/'+('effects' if kind=='effect' else 'music')
    async def form(request):
        try:body=await request.json()
        except ValueError:raise HTTPException(400,'The audio form could not be read. Reload the board and fill it in again.') from None
        if not isinstance(body,dict):raise HTTPException(400,'Use the audio form controls and try again.')
        return body
    async def run(project_id,fn,*args):
        try:return await asyncio.to_thread(fn,safe_root(project_id),*args)
        except (ValueError,OSError,RuntimeError) as exc:
            raise HTTPException(400,str(exc) if isinstance(exc,ValueError) else 'Audio could not be read safely. Review the file, story and signed settings, then try again.') from None
    @app.post(prefix+'/brief')
    async def brief(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,save_effect if kind=='effect' else save_brief,await form(request));invalidate(project_id);return result
    @app.post(prefix+'/prepare')
    async def prepare(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,partial(prepare_cue,kind=kind),await form(request));invalidate(project_id);return result
    @app.post(prefix+'/import/choose')
    async def select(project_id:str,request:Request):
        require_writer(request);return await run(project_id,partial(choose,kind=kind))
    @app.post(prefix+'/import')
    async def bring_in(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,partial(import_selected,kind=kind),await form(request));invalidate(project_id);return result
    @app.post(prefix+'/{identity}/send')
    async def submit(project_id:str,identity:str,request:Request):
        require_writer(request);result=await run(project_id,partial(send,kind=kind),identity);invalidate(project_id);return result
    @app.post(prefix+'/{identity}/decision')
    async def decision(project_id:str,identity:str,request:Request):
        require_writer(request);body=await form(request);result=await run(project_id,partial(decide,kind=kind),identity,body.get('decision'));invalidate(project_id);return result
