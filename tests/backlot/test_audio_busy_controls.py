from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http


def test_audio_controls_wait_for_the_running_operation(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];held=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Voice workspace',exact=True).click()
            panel=page.get_by_role('region',name='Trailer audio',exact=True)
            expect(panel).to_contain_text('One production ceiling',timeout=15000)
            endpoint=url+f'/api/project/{root.name}/voice/catalogue'
            page.route(endpoint,lambda route:held.append(route))
            load=panel.get_by_role('button',name='Load account voices',exact=True)
            with page.expect_request(endpoint):load.click()
            expect(load).to_be_disabled()
            expect(panel.get_by_label('Voice for line 1',exact=True)).to_be_disabled()
            assert held
            held[0].fulfill(status=200,content_type='application/json',body='[]')
            expect(load).to_be_enabled(timeout=15000)
            assert posts==[] and gets==[] and not errors
        finally:browser.close()
