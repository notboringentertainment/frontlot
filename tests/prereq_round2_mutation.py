"""Explicit regression probes; change only this test process or served test code."""
import importlib,os,subprocess
from pathlib import Path
import pytest

def pytest_sessionstart(session):
    mutation=os.environ['FRONTLOT_PREREQ_MUTATION']
    if mutation=='prices':
        from lib import media_settings as m
        original=m._prepare
        def in_place(root,*a,**kw):
            request=original(root,*a,**kw)
            raw=m.draft_material(root,request)[2]
            m.atomic_write_bytes(Path(root)/'project.yaml',raw)
            return request
        m._prepare=in_place
    if mutation=='price-pause':
        from lib import media_settings
        media_settings.require_no_draft=lambda root:None
    if mutation in {'location','story-reader'}:
        name={'location':'lib.frontlot_location_source','story-reader':'lib.trailer_edit'}[mutation]
        module=importlib.import_module(name)
        source=subprocess.check_output(['git','show','0e22f39:'+name.replace('.','/')+'.py'],text=True)
        exec(compile(source,module.__file__,'exec'),module.__dict__)

@pytest.fixture(autouse=True)
def changed_browser_code(monkeypatch):
    mutation=os.environ['FRONTLOT_PREREQ_MUTATION']
    if mutation not in {'writing-locks','stale-paper','preserved-paper','own-paper'}:return
    from playwright.sync_api import Page
    original=Page.goto
    directory=Path(__file__).resolve().parents[1]/'backlot/ui'
    def goto(page,*a,**kw):
        for name in ('story.js','trailer.js','footage_setup.js'):
            source=(directory/name).read_text()
            if mutation=='writing-locks':source=source.replace('controls.disabled=true;','controls.disabled=false;')
            if mutation=='stale-paper' and name=='trailer.js':source=source.replace('if(epoch===editEpoch)update(next);','update(next);')
            if mutation=='preserved-paper' and name=='trailer.js':source=source.replace('draft=preserved.paper;revision=preserved.revision;','draft=preserved.paper;')
            if mutation=='own-paper' and name=='trailer.js':source=source.replace('editor.own(result.paper_cut);','update(result.paper_cut);')
            def handler(body):
                def fulfill(route):route.fulfill(body=body,content_type='text/javascript')
                return fulfill
            page.route('**/ui/'+name,handler(source))
        return original(page,*a,**kw)
    monkeypatch.setattr(Page,'goto',goto)
