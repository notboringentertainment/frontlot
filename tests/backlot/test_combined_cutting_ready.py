"""A first-time cut read can recover after its prerequisites become ready."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_voice_panel import voice_world,project,priced,http

def test_cut_retries_a_first_read_that_was_not_ready(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            def route(r):
                if r.request.url==url+'/cut-ready-proof':r.fulfill(body='<html><body></body></html>',content_type='text/html')
                elif r.request.url.startswith(url+'/'):r.continue_()
                else:r.abort()
            page.route('**/*',route);page.goto(url+'/cut-ready-proof')
            page.evaluate('''async()=>{
              const m=await import('/ui/cutting.js');window.cutModule=m;window.ready=false;window.reads=0;
              window.fetch=async()=>{window.reads++;return window.ready?new Response(JSON.stringify({number:1,revision:'invented',target_frames:72,pictures:[],sounds:[],timeline:{picture:[],audio:[],cards:[],mix:{}},card_states:{},fonts:[],versions:[]})):new Response(JSON.stringify({detail:'Sign the story before opening this cut.'}),{status:400});};
              document.body.append(m.renderCutting({project_id:'invented-fresh'},()=>{}));
            }''')
            cut=page.get_by_label('Cutting view',exact=True)
            expect(cut.get_by_role('status')).to_contain_text('Sign the story')
            page.evaluate("()=>{window.ready=true;window.cutModule.renderCutting({project_id:'invented-fresh'},()=>{});}")
            expect(cut.get_by_label('Kept picture take',exact=True)).to_be_visible()
            assert page.evaluate('()=>window.reads')==2
            assert posts==[] and gets==[] and not errors
        finally:browser.close()
