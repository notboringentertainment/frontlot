"""Review starts from the saved cut, even before its background read arrives."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign


def test_preparing_one_review_keeps_one_exact_receipt_proof_scope(signed_cut,monkeypatch):
    import json
    from urllib.request import Request,urlopen
    from lib import gates,trailer_edit
    root,calls,posts,*_=signed_cut
    with board(root,monkeypatch) as url:
        checks=[];original=gates.verify_local_projection
        def checked(*args):checks.append(args[1]);return original(*args)
        monkeypatch.setattr(gates,'verify_local_projection',checked)
        request=Request(url+f'/api/project/{root.name}/delivery/rough',
                        data=json.dumps({'revision':trailer_edit.read_revision(root)['edit_revision_sha256']}).encode(),
                        headers={'Content-Type':'application/json','Origin':url,'X-Frontlot-Capability':'invented-browser-proof'})
        checks.clear()
        with urlopen(request,timeout=180) as reply:result=json.load(reply)
        assert result['gate_request_id']
        assert len(checks)<=2,checks
        assert len(calls['posts'])==3 and len(posts)==1


def test_rough_review_rereads_the_current_cut_before_binding_its_request(signed_cut,monkeypatch):
    from lib import cutting_view,trailer_edit,receipts
    root,calls,posts,*_=signed_cut
    # Rendering and signature playback are proved by the real journey tests.
    # This test isolates which saved revision the real request operation binds.
    monkeypatch.setattr(cutting_view,'render_preview',lambda *args:root/'assets/invented-preview.mp4')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            page.route(url+'/current-cut-proof',lambda route:route.fulfill(body='<html><body></body></html>',content_type='text/html'))
            page.goto(url+'/current-cut-proof')
            page.evaluate('''async id=>{
              const {CAPABILITY_TOKEN_KEY}=await import('/ui/lib.js');
              sessionStorage.setItem(CAPABILITY_TOKEN_KEY,'invented-browser-proof');
              const module=await import('/ui/delivery.js');window.openedReview=null;
              document.body.append(module.renderDelivery({project_id:id},request=>{window.openedReview=request;}));
            }''',root.name)
            control=page.get_by_role('button',name='Review and sign the saved rough cut',exact=True)
            expect(control).to_be_enabled()
            before=trailer_edit.read_revision(root)
            changed=cutting_view.editable(before['timeline']);changed['picture'][0]['colour']['brightness']=15
            newer=cutting_view.save_cut(root,changed,before['edit_revision_sha256'])
            assert newer['edit_revision_sha256']!=before['edit_revision_sha256']
            control.click()
            page.wait_for_function('()=>!!window.openedReview',timeout=5000)
            import json
            request=json.loads((root/'.gate-requests'/(page.evaluate('window.openedReview')+'.json')).read_text())
            current=trailer_edit.read_revision(root)
            assert request['approval_record']['edit_revision_sha256']==current['edit_revision_sha256']
            assert current['timeline']['picture'][0]['colour']['brightness']==15
            assert not receipts.verified_approvals(root,'artifact_review',entity_id='edit')
            assert len(calls['posts'])==3 and len(posts)==1
        finally:browser.close()
