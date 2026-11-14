"""Board-only adapters to the deterministic source and existing story signer."""
import base64,binascii
from fastapi import HTTPException,Request
from lib import story_ingest as story

def mount(app,require_writer,safe_root,invalidate):
    import asyncio
    prefix='/api/project/{project_id}/story'
    async def run(project_id,fn,*args,**kwargs):
        try:return await asyncio.to_thread(fn,safe_root(project_id),*args,**kwargs)
        except (ValueError,OSError,RuntimeError,KeyError,TypeError) as exc:
            message=str(exc) if isinstance(exc,ValueError) else 'The story could not be read safely. Your earlier source and signatures are kept. Supply it again for review.'
            raise HTTPException(400,message) from None
    @app.get(prefix)
    async def status(project_id:str):return await run(project_id,story.status)
    @app.post(prefix)
    async def supply(project_id:str,request:Request):
        require_writer(request)
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>story.MAX_STORY_BYTES*8:
                raise HTTPException(413,'Choose a plain UTF-8 text story no larger than 128 kilobytes. Nothing was saved.')
        import json
        try:
            data=json.loads(body)
            if not isinstance(data,dict) or set(data)-{'text','file','protected','spoilers'} or ('text' in data)==('file' in data):
                raise ValueError('Paste the story or choose a text file with the story controls. The board does not read a typed file path.')
            name='Pasted story'
            if 'text' in data:
                if not isinstance(data['text'],str):raise ValueError('Paste the exact story text before saving it.')
                raw=data['text'].encode('utf-8')
            else:
                file=data['file']
                if not isinstance(file,dict) or set(file)!={'name','base64'}:raise ValueError('Choose a plain text or Markdown file using the story file chooser.')
                name=file['name']
                if not isinstance(name,str) or not name.lower().endswith(('.txt','.md')):raise ValueError('Choose a plain text or Markdown file, rather than a document, picture or credential file.')
                raw=base64.b64decode(file['base64'],validate=True)
            result=await run(project_id,story.supply,raw,protected=data.get('protected',[]),spoilers=data.get('spoilers',[]),name=name)
        except (ValueError,TypeError,UnicodeError,binascii.Error) as exc:
            raise HTTPException(400,str(exc) if isinstance(exc,ValueError) else 'This text file could not be read. Choose it again.') from None
        invalidate(project_id);return result
    @app.post(prefix+'/apply')
    async def apply(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,story.complete);invalidate(project_id);return result
    @app.post(prefix+'/check')
    async def compare(project_id:str,request:Request):
        require_writer(request);result=await run(project_id,story.check_paper);invalidate(project_id);return result
