import json
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http


def test_cut_controls_wait_for_the_running_operation_instead_of_dropping_a_click(voice_world,monkeypatch):
    from backlot.cutting import snapshot
    root,posts,gets=voice_world;before=(root/'artifacts/edit_decisions.json').read_bytes()
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];held=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Cutting workspace',exact=True).click()
            cut=page.get_by_role('region',name='Cutting view',exact=True)
            expect(cut.get_by_label('Kept sound take',exact=True)).to_be_visible(timeout=15000)
            page.route(url+f'/api/project/{root.name}/cut',lambda route:held.append(route))
            cut.get_by_role('button',name='Reopen saved cut',exact=True).click()
            expect(cut.get_by_role('button',name='Save cut',exact=True)).to_be_disabled()
            expect(cut.get_by_role('button',name='Prepare saved playback',exact=True)).to_be_disabled()
            expect(cut.get_by_label('Kept sound take',exact=True)).to_be_disabled()
            assert held
            while held:held.pop(0).fulfill(status=200,content_type='application/json',body=json.dumps(snapshot(root)))
            page.unroute(url+f'/api/project/{root.name}/cut')
            expect(cut.get_by_role('button',name='Save cut',exact=True)).to_be_enabled()
            assert (root/'artifacts/edit_decisions.json').read_bytes()==before
            assert posts==[] and gets==[] and not errors
        finally:browser.close()
