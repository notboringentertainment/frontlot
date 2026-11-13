"""Local board adapters for the authoritative saved cut."""
from pathlib import Path
from lib import cutting_view as cut,trailer_edit,footage_requests,audio_workspace,supervised_production
from lib.pathsafe import resolve_input

def _kept(root,kind):
    from lib.receipts import verified_approvals
    return {r.get('entity_id'):r['approved_at'] for r in verified_approvals(root,kind) if r.get('record',{}).get('decision')=='accept'}

def _when(value):
    if not value:return 'not kept'
    from datetime import datetime
    return 'kept '+datetime.fromisoformat(value).astimezone().strftime('%d %b %Y at %H:%M:%S %Z')

def snapshot(root):
    footage_requests.require_trailer(root)
    saved=trailer_edit.read_revision(root);pictures=[];sounds=[];all_picture={};all_sound={}
    picture_kept=_kept(root,'footage_take');sound_kept=_kept(root,'audio_take')
    for n,shot in enumerate((saved or {}).get('paper_cut',{}).get('shots',[]),1):
        takes=[r for r in supervised_production.history(root,shot['id']) if r.get('kind')=='take']
        for number,row in enumerate(takes,1):
            kept=picture_kept.get(row['take_id'])
            item={'shot_id':shot['id'],'take_id':row['take_id'],'frames':int(row['duration_seconds']*24),'take_number':number,'kept_at':kept,
                  'thumbnail':f'/thumb/{Path(root).name}/{row["path"]}?w=160','label':f'Shot {n} · Take {number} · {_when(kept)} · {shot["purpose"]}'}
            item['state']=footage_requests.take_state(root,shot['id'],row['take_id'])
            all_picture[row['take_id']]=item
            if item['state']=='Accepted':pictures.append(item)
    counts={}
    for take in sorted(audio_workspace.state(root)['takes'],key=lambda t:(sound_kept.get(t['id'],''),t['id'])):
        kind=take['kind'];counts[kind]=counts.get(kind,0)+1;kept=sound_kept.get(take['id'])
        item={'take_id':take['id'],'frames':int(take['duration_seconds']*24),'take_number':counts[kind],'kept_at':kept,
              'thumbnail':f'/api/project/{Path(root).name}/cut/audio/{take["id"]}/waveform',
              'label':{'speech':'Voice','music':'Music','effect':'Sound effect'}[kind]+f' take {counts[kind]} · {_when(kept)}'+(' · test trailer only' if take.get('release_note') else '')}
        item['state']=take['status']
        if take['status']!='Accepted':item['thumbnail']=None
        all_sound[take['id']]=item
        if take['status']=='Accepted':sounds.append(item)
    timeline=(saved or {}).get('timeline',{})
    ready=(saved or {}).get('cutting_view') or timeline.get('picture') and all(x.get('kind')=='footage' for x in timeline['picture'])
    clips={'picture':[],'audio':[]}
    if ready:
        for medium,catalogue in (('picture',all_picture),('audio',all_sound)):
            for i,row in enumerate(timeline.get(medium,[]),1):
                item=catalogue.get(row.get('take_id'))
                state=(item or {}).get('state')
                # These states were verified above in this same snapshot.
                # Save, approval and playback independently verify them again.
                problem=None if state=='Accepted' else cut.source_problem(medium,i,state)
                clips[medium].append({**catalogue.get(row.get('take_id'),{'label':f'{"Picture" if medium=="picture" else "Sound"} {i} — original take is no longer available','kept_at':None,'take_number':None,'thumbnail':None}), 'take_id':row.get('take_id',''),'problem':problem})
    versions=[{'revision':v['edit_revision_sha256'],'number':v['revision_number'],'saved_at':v.get('saved_at')} for v in (saved or {}).get('revision_history',[])+([saved] if saved else []) if v.get('cutting_view')]
    from lib.trailer_contract import require_contract
    return {'revision':(saved or {}).get('edit_revision_sha256'),'number':(saved or {}).get('revision_number'),
            'timeline':cut.editable(timeline,display=True) if ready else {'fps':24,'picture':[],'audio':[]},'pictures':pictures,'sounds':sounds,
            'clips':clips,'versions':versions,'target_frames':round(require_contract(root)['target_duration_seconds']*24)}

def mount(app,require_writer,safe_root,invalidate):
    import asyncio,re
    from fastapi import Request,HTTPException
    from fastapi.responses import FileResponse
    async def run(fn,*args):
        try:return await asyncio.to_thread(fn,*args)
        except (ValueError,OSError,RuntimeError,KeyError) as e:raise HTTPException(400,str(e) if isinstance(e,ValueError) else 'The cut could not be read or played safely. Check the kept files, then reopen the saved cut.') from None
    @app.get('/api/project/{project_id}/cut')
    async def state(project_id:str):return await run(snapshot,safe_root(project_id))
    @app.post('/api/project/{project_id}/cut')
    async def save(project_id:str,request:Request):
        require_writer(request)
        data=await request.json()
        if not isinstance(data,dict) or set(data)!={'timeline','revision'}:raise HTTPException(400,'Save the picture and sound controls together.')
        await run(cut.save_cut,safe_root(project_id),data['timeline'],data['revision']);invalidate(project_id)
        return await run(snapshot,safe_root(project_id))
    @app.post('/api/project/{project_id}/cut/preview')
    async def preview(project_id:str,request:Request):
        require_writer(request);data=await request.json()
        if not isinstance(data,dict) or set(data)!={'revision'}:raise HTTPException(400,'Save the cut before preparing playback.')
        path=await run(cut.render_preview,safe_root(project_id),data['revision'])
        return {'url':f'/api/project/{project_id}/cut/play/{path.name}'}
    @app.post('/api/project/{project_id}/cut/restore')
    async def restore(project_id:str,request:Request):
        require_writer(request);data=await request.json()
        if not isinstance(data,dict) or set(data)!={'revision','current'}:raise HTTPException(400,'Choose a saved cut and reopen the current version before restoring.')
        await run(trailer_edit.restore_revision,safe_root(project_id),data['revision'],data['current']);invalidate(project_id)
        return await run(snapshot,safe_root(project_id))
    @app.get('/api/project/{project_id}/cut/audio/{identity}/waveform')
    async def waveform(project_id:str,identity:str):
        from fastapi.responses import Response
        import subprocess
        def draw():
            root=safe_root(project_id);take=cut.require_source(root,{'take_id':identity},'audio',1)
            return subprocess.check_output(['ffmpeg','-v','error','-i',str(resolve_input(take['path'],root)),'-filter_complex','showwavespic=s=160x48:colors=white','-frames:v','1','-f','image2pipe','-vcodec','png','pipe:1'],timeout=15)
        return Response(await run(draw),media_type='image/png')
    @app.get('/api/project/{project_id}/cut/play/{name}')
    async def play(project_id:str,name:str):
        if not re.fullmatch('[a-f0-9]{64}-[a-f0-9]{32}\\.mp4',name):raise HTTPException(404)
        root=safe_root(project_id);saved=await run(trailer_edit.read_revision,root)
        if not saved or not name.startswith(saved['edit_revision_sha256']):raise HTTPException(400,'This preview is from an earlier cut. Prepare playback of the saved cut.')
        await run(lambda:cut.validate_cut(root,cut.editable(saved['timeline']),verify_files=True))
        path=await run(resolve_input,'artifacts/cut-previews/'+name,root)
        return FileResponse(path,media_type='video/mp4',headers={'Cache-Control':'no-store'})
