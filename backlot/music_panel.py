"""Music controls use the existing audio boundary and existing signature gates."""
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from lib import audio_workspace as audio
from backlot.voice_panel import root_checked,_gate_id

MODEL='music_v1'
_choices={}
_choice_lock=threading.Lock()

def save_brief(root,data):
    root=root_checked(root)
    if not isinstance(data,dict) or set(data)!={'text','seconds','brief_id'}:
        raise ValueError('Use the music controls for the exact brief and length. Music is always instrumental.')
    identity=audio.save_music_brief(root,data['text'],data['seconds'],data['brief_id'])
    return {'brief_id':identity,'message':'Music brief saved. Prepare a spending card to request a new take.'}

def prepare_music(root,data):
    root=root_checked(root)
    if not isinstance(data,dict) or set(data)!={'brief_id'}:
        raise ValueError('Save the music brief, then prepare its spending card with the music controls.')
    from lib.project_config import load_current_verified_project_config,ProjectConfigError
    config=load_current_verified_project_config(root)
    try:config.require_egress('elevenlabs','music_prompt')
    except ProjectConfigError:
        raise ValueError('Sign the audio account settings to permit instrumental music briefs before preparing a music request.') from None
    record=audio.prepare_request(root,'music',MODEL,brief_id=data['brief_id'])
    return {'request':record,'gate_request_id':_gate_id(root,record['request_id'],'audio_spend')}

def send(root,identity):
    from tools.audio.music_gen import MusicGen
    root=root_checked(root)
    result=MusicGen().execute({'project_dir':str(root),'audio_request_id':identity})
    return {'success':result.success,**({'take':result.data['take']} if result.success else {'message':result.error+' Review the request and signed settings before trying again.'})}

def decide(root,identity,decision):
    root=root_checked(root)
    if audio.read(root,identity).get('kind')!='music':raise ValueError('Choose a music take in the music controls.')
    return audio.request_decision(root,identity,decision)

def current_brief(root):
    directory=audio._checked_path(Path(root)/'.audio-workspace/briefs')
    paths=sorted(directory.glob('*.json'),key=lambda p:p.lstat().st_mtime_ns,reverse=True)
    if not paths:return None
    identity=paths[0].stem
    value=audio.binding_source(root,{'kind':'music','brief_id':identity})
    return {'brief_id':identity,**value}

def choose_file():
    if sys.platform!='darwin':raise ValueError('Music file selection needs the Mac app. Open this board on the Mac running Front Lot.')
    try:
        result=subprocess.run(['/usr/bin/osascript','-e','POSIX path of (choose file with prompt "Choose music for this trailer" of type {"public.audio"})'],capture_output=True,text=True,timeout=120)
    except (OSError,subprocess.TimeoutExpired):
        raise ValueError('The music chooser could not finish. Choose a music file again on the Mac running Front Lot.') from None
    if result.returncode:
        if '-128' in result.stderr:return None
        raise ValueError('The music chooser could not open. Allow Front Lot to show the Mac file chooser, then try again.')
    value=result.stdout.rstrip('\r\n')
    if not value:raise ValueError('No readable file was selected. Choose a music file again.')
    return value

def choose(root):
    root=root_checked(root)
    selected=choose_file()
    if selected is None:return {'cancelled':True}
    path=Path(selected).expanduser()
    if not path.is_absolute() or '..' in path.parts:raise ValueError('Choose a direct music file without path tricks or links.')
    audio._checked_path(path)
    if not path.is_file():raise ValueError('Choose a plain music file without links.')
    identity=str(uuid.uuid4())
    with _choice_lock:
        now=time.time()
        for old in list(_choices):
            if _choices[old]['expires_at']<=now:_choices.pop(old)
        _choices[identity]={'root':str(root),'path':str(path),'expires_at':now+900}
    return {'choice_id':identity,'name':path.name}

def import_selected(root,data):
    root=root_checked(root)
    if not isinstance(data,dict) or set(data)!={'choice_id','source','licence_note'}:
        raise ValueError('Choose a music file and fill in where it came from and its licence note.')
    if not isinstance(data['choice_id'],str):raise ValueError('Choose the music file again. Its selection could not be read.')
    with _choice_lock:
        choice=_choices.get(data['choice_id'])
        if not choice or choice['root']!=str(root) or choice['expires_at']<=time.time():
            raise ValueError('Choose the music file again. Its selection is missing, expired or belongs to another trailer.')
        try:take=audio.import_music(root,choice['path'],data['source'],data['licence_note'])
        except (subprocess.SubprocessError,KeyError):
            raise ValueError('This file could not be read as playable music. Choose a supported audio file and try again.') from None
        _choices.pop(data['choice_id'])
    return {'take':take,'message':'Music brought in at no charge. Listen, then sign keeping or declining this take.'}

def mount(app,require_writer,safe_root,invalidate):
    import asyncio
    from fastapi import HTTPException,Request
    async def form(request):
        try:body=await request.json()
        except ValueError:raise HTTPException(400,'The music form could not be read. Reload the board and fill it in again.') from None
        if not isinstance(body,dict):raise HTTPException(400,'Use the music form controls and try again.')
        return body
    async def run(project_id,fn,*args):
        try:return await asyncio.to_thread(fn,safe_root(project_id),*args)
        except (ValueError,OSError,RuntimeError) as exc:
            raise HTTPException(400,str(exc) if isinstance(exc,ValueError) else 'Music could not be read safely. Review the file, story and signed settings, then try again.') from None
    @app.post('/api/project/{project_id}/music/brief')
    async def brief(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,save_brief,await form(request));invalidate(project_id);return result
    @app.post('/api/project/{project_id}/music/prepare')
    async def prepare(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,prepare_music,await form(request));invalidate(project_id);return result
    @app.post('/api/project/{project_id}/music/import/choose')
    async def select(project_id:str,request:Request):
        require_writer(request);return await run(project_id,choose)
    @app.post('/api/project/{project_id}/music/import')
    async def bring_in(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,import_selected,await form(request));invalidate(project_id);return result
    @app.post('/api/project/{project_id}/music/{identity}/send')
    async def submit(project_id:str,identity:str,request:Request):
        require_writer(request);result=await run(project_id,send,identity);invalidate(project_id);return result
    @app.post('/api/project/{project_id}/music/{identity}/decision')
    async def decision(project_id:str,identity:str,request:Request):
        require_writer(request);body=await form(request);result=await run(project_id,decide,identity,body.get('decision'));invalidate(project_id);return result
