"""Local board adapters for the authoritative saved cut."""
from pathlib import Path
from lib import cutting_view as cut,trailer_edit,footage_requests,audio_workspace,supervised_production
from lib.pathsafe import resolve_input

def snapshot(root):
    footage_requests.require_trailer(root)
    saved=trailer_edit.read_revision(root);pictures=[];sounds=[]
    for n,shot in enumerate((saved or {}).get('paper_cut',{}).get('shots',[]),1):
        for row in supervised_production.history(root,shot['id']):
            if row.get('kind')=='take' and footage_requests.take_state(root,shot['id'],row['take_id'])=='Accepted':
                pictures.append({'shot_id':shot['id'],'take_id':row['take_id'],'frames':int(row['duration_seconds']*24),'label':f'Shot {n}: {shot["purpose"]}'})
    for take in audio_workspace.state(root)['takes']:
        if take['status']=='Accepted':sounds.append({'take_id':take['id'],'frames':int(take['duration_seconds']*24),'label':{'speech':'Voice','music':'Music','effect':'Sound effect'}[take['kind']]+(' · test trailer only' if take.get('release_note') else '')})
    timeline=(saved or {}).get('timeline',{})
    ready=timeline.get('picture') and all(x.get('kind')=='footage' for x in timeline['picture'])
    from lib import trailer_titles as titles
    return {'revision':(saved or {}).get('edit_revision_sha256'),'number':(saved or {}).get('revision_number'),
            'timeline':cut.editable(timeline) if ready else {'fps':24,'picture':[],'audio':[]},'pictures':pictures,'sounds':sounds,
            'fonts':titles.fonts(),'card_states':{c['id']:titles.status(root,c) for c in timeline.get('cards',[])}}

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
    @app.post('/api/project/{project_id}/cut/cards/{identity}/approval')
    async def approve_card(project_id:str,identity:str,request:Request):
        require_writer(request);data=await request.json();root=safe_root(project_id)
        if not isinstance(data,dict) or set(data)!={'revision'}:raise HTTPException(400,'Save the cut before preparing this card’s review.')
        saved=await run(trailer_edit.read_revision,root)
        if not saved or saved['edit_revision_sha256']!=data['revision']:raise HTTPException(400,'The cut changed. Reopen the saved cut before reviewing this card.')
        from lib.trailer_titles import request_approval
        gate=await run(request_approval,root,identity);invalidate(project_id)
        return {'gate_request_id':gate['request_id']}
    @app.get('/api/project/{project_id}/cut/play/{name}')
    async def play(project_id:str,name:str):
        if not re.fullmatch('[a-f0-9]{64}-[a-f0-9]{32}\\.mp4',name):raise HTTPException(404)
        root=safe_root(project_id);saved=await run(trailer_edit.read_revision,root)
        if not saved or not name.startswith(saved['edit_revision_sha256']):raise HTTPException(400,'This preview is from an earlier cut. Prepare playback of the saved cut.')
        await run(lambda:cut.validate_cut(root,cut.editable(saved['timeline']),verify_files=True))
        path=await run(resolve_input,'artifacts/cut-previews/'+name,root)
        return FileResponse(path,media_type='video/mp4',headers={'Cache-Control':'no-store'})
