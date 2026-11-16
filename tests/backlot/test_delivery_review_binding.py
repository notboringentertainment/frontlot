"""Rough-cut signing must show the saved finish, never a later comparison."""
import json
import pytest
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from playwright.sync_api import sync_playwright,expect

@pytest.mark.parametrize('case',['later-before','only-before','changed-settings'])
def test_rough_review_requires_the_saved_finished_settings(signed_cut,case):
    from lib import trailer_delivery as d,trailer_edit,cutting_view as cut
    from backlot.delivery import signing_packet
    root,provider,posts,*_=signed_cut
    saved=trailer_edit.read_revision(root);timeline=cut.editable(saved['timeline'])
    timeline['picture'][0]['colour']['brightness']=25
    cut.save_cut(root,timeline,saved['edit_revision_sha256'])
    request=d.request_rough(root,trailer_edit.read_revision(root)['edit_revision_sha256'])
    revision=trailer_edit.read_revision(root)['edit_revision_sha256']
    if case!='only-before':finished=cut.render_preview(root,revision,'after')
    if case!='changed-settings':comparison=cut.render_preview(root,revision,'before')
    else:
        path=finished.with_suffix('.review.json');review=json.loads(path.read_text())
        review['settings_sha256']='f'*64;path.write_text(json.dumps(review))
    if case=='later-before':assert comparison.stat().st_mtime_ns>finished.stat().st_mtime_ns
    packet=signing_packet(root,request)
    if case=='later-before':assert packet['play_url'].endswith('/'+finished.name),packet
    else:
        assert packet['packet_error'] is True,packet
        assert 'saved colour and sound settings' in packet['error'],packet
    assert len(provider['posts'])==3 and len(posts)==1

def test_retained_review_player_recovers_without_restarting_the_same_movie(signed_cut,monkeypatch):
    from lib import trailer_edit
    root,provider,posts,*_=signed_cut
    path=root/trailer_edit.read_revision(root)['timeline']['picture'][0]['path']
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            def route(r):
                if r.request.url==url+'/recovery-preview.mp4':r.fulfill(body=path.read_bytes(),content_type='video/mp4')
                elif r.request.url==url+'/review-harness':r.fulfill(body='<html><body></body></html>',content_type='text/html')
                elif r.request.url.startswith(url+'/'):r.continue_()
                else:r.abort()
            page.route('**/*',route);page.goto(url+'/review-harness')
            page.evaluate('''async()=>{
                const {renderDeliverySigning}=await import('/ui/delivery.js');
                window.updateReview=(packet)=>{
                    const action=document.createElement('button');action.className='gate-primary';action.textContent='Start signing';
                    return renderDeliverySigning({request_id:'invented-recovery-review',request:{stage:'edit'},packet},action,()=>{});
                };
                window.reviewNode=window.updateReview({packet_error:true,error:'Prepare saved playback again.'});
                document.body.append(window.reviewNode);
            }''')
            player=page.locator('video');assert player.get_attribute('src') is None
            expect(page.get_by_role('heading',name='Review the saved rough cut',exact=True)).to_be_visible()
            assert page.evaluate("()=>window.updateReview({delivery_stage:'edit',play_url:'/recovery-preview.mp4',messages:[]})===window.reviewNode")
            expect(player).to_have_attribute('src','/recovery-preview.mp4')
            expect(player).to_have_js_property('readyState',4,timeout=30000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=15000)
            page.evaluate("()=>window.updateReview({})")
            expect(player).to_have_js_property('ended',True)
            expect(player).to_have_attribute('src','/recovery-preview.mp4')
            expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_disabled()
            page.evaluate("()=>window.updateReview({delivery_stage:'compose',play_url:'/recovery-preview.mp4',messages:[]})")
            expect(player).to_have_js_property('ended',True)
            expect(page.get_by_role('heading',name='Review the saved rough cut',exact=True)).to_be_visible()
            expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_enabled()
            page.evaluate("()=>window.updateReview({packet_error:true,error:'The saved playback changed.'})")
            assert player.get_attribute('src') is None
            expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_disabled()
            assert len(provider['posts'])==3 and len(posts)==1 and not errors
        finally:browser.close()
