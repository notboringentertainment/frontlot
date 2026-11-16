"""A new verified request opens independently of a slow board summary."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign


def test_delivery_request_opens_while_the_board_read_is_held(signed_cut,monkeypatch):
    root,calls,posts,*_=signed_cut
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':735,'height':900})
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(url+'/') else route.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            control=panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True)
            expect(control).to_be_enabled(timeout=30000)
            held=[]
            page.route(url+f'/api/project/{root.name}/state',lambda route:held.append(route))
            with page.expect_response(lambda response:response.url.endswith('/delivery/rough') and response.request.method=='POST',timeout=180000) as prepared:
                control.click()
            assert prepared.value.ok,prepared.value.text()
            identity=prepared.value.json()['gate_request_id']
            review=page.get_by_role('region',name='Delivery review',exact=True)
            expect(review.get_by_role('heading',name='Review the saved rough cut',exact=True)).to_be_visible(timeout=5000)
            expect(review.get_by_role('button',name='Start signing',exact=True)).to_be_disabled()
            video=review.locator('video')
            expect(video).to_have_js_property('readyState',4,timeout=10000)
            video.evaluate('(v)=>{v.muted=true;return v.play();}')
            page.wait_for_function('(v)=>v.ended',arg=video.element_handle(),timeout=15000)
            expect(review.get_by_role('button',name='Start signing',exact=True)).to_be_enabled()
            import json
            record=json.loads((root/'.gate-requests'/(identity+'.json')).read_text())
            assert record['kind']=='artifact_review' and record['stage']=='edit'
            assert len(calls['posts'])==3 and len(posts)==1
        finally:
            page.unroute_all(behavior='ignoreErrors');browser.close()
