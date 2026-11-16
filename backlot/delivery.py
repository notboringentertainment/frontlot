"""Capability-authenticated board controls for local trailer delivery."""
from pathlib import Path
from lib import trailer_delivery as delivery

def mount(app,require_writer,safe_root,invalidate):
    import asyncio
    from fastapi import Request,HTTPException
    from fastapi.responses import FileResponse
    async def run(fn,*args):
        try:return await asyncio.to_thread(fn,*args)
        except (ValueError,OSError,KeyError,RuntimeError) as e:
            raise HTTPException(400,str(e) if isinstance(e,ValueError) else 'The saved delivery could not be verified. Reopen the cut and make a fresh file; previous files remain.') from None
    async def body(request,keys):
        require_writer(request);data=await request.json()
        if not isinstance(data,dict) or set(data)!=set(keys):raise HTTPException(400,'Reopen the saved delivery controls and try again.')
        return data
    base='/api/project/{project_id}/delivery'
    @app.get(base)
    async def state(project_id:str):return await run(delivery.snapshot,safe_root(project_id))
    @app.post(base+'/rough')
    async def rough(project_id:str,request:Request):
        data=await body(request,['revision']);root=safe_root(project_id)
        gate=await run(delivery.request_rough,root,data['revision'])
        from lib.cutting_view import render_preview
        from lib.trailer_edit import read_revision
        saved=await run(read_revision,root);await run(render_preview,root,saved['edit_revision_sha256'])
        invalidate(project_id);return {'gate_request_id':gate['request_id']}
    @app.post(base+'/export')
    async def export(project_id:str,request:Request):
        data=await body(request,['revision']);root=safe_root(project_id)
        item=await run(delivery.export,root,data['revision']);invalidate(project_id)
        return {'id':item['id'],'stage_message':'Making a file completes no stage. Its signature and any outstanding requirements are shown below.'}
    @app.post(base+'/colour')
    async def colour(project_id:str,request:Request):
        data=await body(request,['revision']);root=safe_root(project_id)
        gate=await run(delivery.request_colour,root,data['revision'])
        from lib.cutting_view import render_preview
        await run(render_preview,root,data['revision'])
        invalidate(project_id);return {'gate_request_id':gate['request_id']}
    @app.post(base+'/colour/apply')
    async def apply_colour(project_id:str,request:Request):
        await body(request,[]);root=safe_root(project_id)
        message=await run(delivery.complete_stage,root,'colour_mix');invalidate(project_id)
        return {'message':message}
    @app.post(base+'/rough/apply')
    async def apply_rough(project_id:str,request:Request):
        await body(request,[]);root=safe_root(project_id)
        message=await run(delivery.complete_stage,root,'edit');invalidate(project_id)
        return {'message':message}
    @app.post(base+'/{identity}/approval')
    async def approval(project_id:str,identity:str,request:Request):
        root=safe_root(project_id)
        from lib.trailer_policy import trimmed
        if trimmed(delivery._pin(root)):
            data=await body(request,['story_review']);gate=await run(delivery.request_final,root,identity,data['story_review'])
        else:
            await body(request,[]);gate=await run(delivery.request_final,root,identity)
        invalidate(project_id);return {'gate_request_id':gate['request_id']}
    @app.post(base+'/{identity}/apply')
    async def apply(project_id:str,identity:str,request:Request):
        await body(request,[]);root=safe_root(project_id)
        message=await run(delivery.complete_stage,root,'compose',identity);invalidate(project_id)
        return {'message':message}
    @app.post(base+'/{identity}/reveal')
    async def reveal(project_id:str,identity:str,request:Request):
        await body(request,[]);return await run(delivery.reveal,safe_root(project_id),identity)
    @app.get(base+'/{identity}/play')
    async def play(project_id:str,identity:str):
        path=await run(delivery.playback,safe_root(project_id),identity)
        return FileResponse(path,media_type='video/mp4',headers={'Cache-Control':'no-store'})
    @app.get(base+'/{identity}/frame/{frame}')
    async def frame(project_id:str,identity:str,frame:int):
        path=await run(delivery.frame_picture,safe_root(project_id),identity,frame)
        return FileResponse(path,media_type='image/png',headers={'Cache-Control':'no-store'})

def signing_packet(root,request):
    from lib import trailer_edit
    from lib.pathsafe import sha256_file,resolve_input
    import json
    try:
        if request['stage'] in ('edit','colour_mix'):
            stage=request['stage']
            record=(delivery.rough_record if stage=='edit' else delivery.colour_record)(root)
            revision=record['edit_revision_sha256']
            saved=trailer_edit.read_revision(root)
            from lib.canonical_json import record_sha256
            reason='Prepare rough-cut playback with the saved colour and sound settings before signing.'
            if not saved or saved['edit_revision_sha256']!=revision:raise ValueError(reason)
            paths=sorted((root/'artifacts/cut-previews').glob(revision+'-*.mp4'),key=lambda p:p.stat().st_mtime,reverse=True)
            for candidate in paths:
                path=resolve_input(str(candidate.relative_to(root)),root)
                try:review=json.loads(resolve_input(str(path.with_suffix('.review.json').relative_to(root)),root).read_text())
                except (ValueError,OSError):raise ValueError(reason) from None
                if not isinstance(review,dict):raise ValueError(reason)
                if review.get('comparison')!='after':continue
                if review.get('edit_revision_sha256')!=revision or review.get('settings_sha256')!=record_sha256(saved['timeline']):raise ValueError(reason)
                if sha256_file(path)!=review.get('output_sha256'):raise ValueError('The rough preview changed. Prepare playback again.')
                return {'delivery_stage':stage,'number':saved['revision_number'],'play_url':f'/api/project/{root.name}/cut/play/{path.name}',
                        'messages':['Your signature binds this saved cut. Any changed picture, sound, card or setting needs a fresh review.']}
            raise ValueError(reason)
        item=delivery.read_export(root,request['entity_id'])
        delivery._artifacts(root,request['entity_id'])
        story_review=(request.get('approval_record') or {}).get('final_story_review')
        from lib.trailer_policy import trimmed
        if trimmed(delivery._pin(root)):
            from lib.trailer_final_story import validate
            story_review=validate(root,story_review)
        return {'delivery_stage':'compose','final_story_review':story_review,
                'final_story_questions':delivery.story_questions(root) if story_review else None,
                'samples':delivery.frame_samples(root,item),'play_url':f'/api/project/{root.name}/delivery/{item["id"]}/play','review':item['final_review'],
                'messages':item['final_review']['issues_found']}
    except (ValueError,OSError,KeyError) as e:return {'packet_error':True,'error':str(e)}
