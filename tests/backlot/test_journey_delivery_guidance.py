import json
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign


def test_delivery_names_the_actual_missing_reviews_in_the_real_board(signed_cut,monkeypatch):
    root,calls,posts,*_=signed_cut
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Delivery workspace',exact=True).click()
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            expect(panel).to_contain_text('Saved version',timeout=30000)
            expect(panel).to_contain_text('What still prevents a finished stage')
            for label in ('Treatment','Looks','Cast portraits','Visual references','Lines and title cards','Shot list','Footage and sound'):
                expect(panel).to_contain_text(label+':')
            expect(panel).to_contain_text('Paper cut workspace')
            expect(panel).to_contain_text('The final story comparison has no control yet.')
            expect(panel.get_by_role('button',name='Record the signed rough-cut approval',exact=True)).to_be_disabled()
            rail=page.get_by_role('navigation',name='Stage sign-offs')
            expect(rail).to_contain_text('Final assembly')
            expect(rail.get_by_role('button')).to_have_count(0)
            # The current workspace supplies the actual missing reviews;
            # the progress rail must not invite a second approval route.
            expect(panel.get_by_role('button',name='Record the signed rough-cut approval',exact=True)).to_be_disabled()
            assert len(calls['posts'])==3 and len(posts)==1 and not errors
        finally:browser.close()


def test_making_a_file_never_finishes_a_stage_as_a_side_effect(signed_cut,monkeypatch):
    import backlot.server as server
    from lib import trailer_delivery as delivery
    root,calls,posts,*_=signed_cut;made=[];completed=[]
    monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    def export(root,revision):made.append(revision);return {'id':'invented-local-export'}
    def complete(*args,**kwargs):completed.append(1);raise AssertionError('Making a file is not a stage-approval action.')
    monkeypatch.setattr(delivery,'export',export);monkeypatch.setattr(delivery,'complete_stage',complete)
    with TestClient(server.create_app(port=4936,capability_token='invented-guidance')) as client:
            response=client.post(f'/api/project/{root.name}/delivery/export',json={'revision':'invented-saved-revision'},headers={'x-frontlot-capability':'invented-guidance','origin':'http://127.0.0.1:4936'})
    assert response.status_code==200 and made==['invented-saved-revision'] and completed==[]
    assert len(calls['posts'])==3 and len(posts)==1


def test_delivery_controls_wait_for_the_running_operation(signed_cut,monkeypatch):
    root,calls,posts,*_=signed_cut
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];held=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Delivery workspace',exact=True).click()
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            expect(panel).to_contain_text('Saved version',timeout=30000)
            endpoint=url+f'/api/project/{root.name}/delivery/rough'
            page.route(endpoint,lambda route:held.append(route))
            review=panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True)
            review.evaluate('(node)=>node.dataset.testBusy="review"')
            with page.expect_request(endpoint,timeout=30000):review.click()
            expect(panel.locator('[data-test-busy="review"]')).to_be_disabled()
            expect(panel.locator('[data-test-busy="review"]')).to_have_attribute('aria-busy','true')
            assert held
            held[0].fulfill(status=400,content_type='application/json',body=json.dumps({'detail':'An invented refused preparation. No review was changed.'}))
            expect(panel.get_by_role('status')).to_contain_text('An invented refused preparation.')
            expect(panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True)).to_be_enabled(timeout=30000)
            assert not errors and len(calls['posts'])==3 and len(posts)==1
        finally:browser.close()
