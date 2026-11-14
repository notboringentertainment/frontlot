"""Story, cards and Delivery own distinct reviews in either start order."""
from types import SimpleNamespace
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.lib.test_cutting_signed import signed_cut
from tests.backlot.test_delivery_browser import delivery_cut


@pytest.mark.parametrize('order',['story-first','delivery-first'])
def test_story_rough_and_final_reviews_sign_in_either_start_order(film,provider,monkeypatch,order):
    pytest.importorskip('lib.story_ingest',reason='This integration requires the separate Story approval parent.')
    root,_=film
    def make_cut():
        value=signed_cut.__wrapped__(film,provider,monkeypatch,SimpleNamespace(param=None))
        return delivery_cut.__wrapped__(value)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            def sign_story():
                page.get_by_role('button',name='Story workspace',exact=True).click()
                panel=page.get_by_role('region',name='Trailer story',exact=True)
                panel.get_by_label('Exact story text',exact=True).fill('The lamp is still lit. A keeper looks towards a doorway.')
                panel.get_by_role('button',name='Save story for review',exact=True).click()
                expect(panel.get_by_role('status')).to_contain_text('Saved',timeout=30000)
                panel.get_by_role('button',name='Review and sign this story',exact=True).click()
                expect(page.get_by_role('region',name='Story approval',exact=True)).to_contain_text('The lamp is still lit.',timeout=30000)
                page.get_by_role('button',name='Start signing',exact=True).click()
                page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
                expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=60000)
                page.get_by_role('button',name='Return to story',exact=True).click()
                expect(panel.get_by_role('status')).to_contain_text('signed',timeout=30000)
            if order=='story-first':
                sign_story()
                # The fixture had an unpaid brief for its previous story.
                # Keep that history; prepare a new shot for the new source,
                # rather than bypassing its correctly stale source snapshot.
                from lib import trailer_edit
                saved=trailer_edit.read_revision(root);paper=saved['paper_cut']
                paper['shots'][0]['id']='shot-after-signed-story'
                trailer_edit.save_revision(root,paper,saved['edit_revision_sha256'])
                film=(root,'shot-after-signed-story')
            root,video_calls,audio_calls=make_cut()
            page.reload()
            # Literal card review has its own kind and must still dispatch there.
            cut=page.get_by_role('region',name='Cutting view',exact=True)
            cut.get_by_role('button',name='Review and sign card 1',exact=True).click(timeout=30000)
            expect(page.get_by_role('region',name='Exact card review',exact=True)).to_contain_text('One lamp.')
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=60000)
            page.get_by_role('button',name='Return to cutting view',exact=True).click()
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True).click(timeout=30000)
            def sign_delivery(heading):
                review=page.get_by_role('region',name='Delivery review',exact=True)
                expect(review.get_by_role('heading',name=heading,exact=True)).to_be_visible(timeout=240000)
                if heading=='Review this exact final file':
                    gallery=review.get_by_role('region',name='Pictures from the final file')
                    expect(gallery.locator('img')).to_have_count(16)
                    expect(gallery.locator('img').last).to_have_js_property('naturalWidth',1920,timeout=30000)
                    if __import__('os').environ.get('DELIVERY_CAPTURE'):
                        from pathlib import Path
                        gallery.evaluate('(e)=>e.scrollIntoView({block:"start",behavior:"instant"})')
                        page.screenshot(path=str(Path(__import__('os').environ['DELIVERY_CAPTURE'])/('round2-'+order+'-signing-pictures.png')))
                player=review.locator('video');expect(player).to_have_js_property('readyState',4,timeout=30000)
                player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=15000)
                page.get_by_role('button',name='Start signing',exact=True).click()
                page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
                expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=120000)
                page.get_by_role('button',name='Return to delivery',exact=True).click()
            sign_delivery('Review the saved rough cut')
            panel.get_by_role('button',name='Make the final file',exact=True).click()
            expect(panel).to_contain_text('Loudness is on target',timeout=300000)
            gallery=panel.get_by_role('region',name='Pictures from the final file')
            expect(gallery.locator('img')).to_have_count(16)
            expect(gallery.locator('img').first).to_have_js_property('naturalWidth',1920,timeout=30000)
            page.wait_for_function('(e)=>Array.from(e.querySelectorAll("img")).every(i=>i.naturalWidth===1920)',arg=gallery.element_handle(),timeout=30000)
            expect(gallery).to_contain_text('0.250 seconds (frame 6)')
            if __import__('os').environ.get('DELIVERY_CAPTURE'):
                from pathlib import Path
                gallery.evaluate('(e)=>e.scrollIntoView({block:"start",behavior:"instant"})')
                page.screenshot(path=str(Path(__import__('os').environ['DELIVERY_CAPTURE'])/('round2-'+order+'-delivery-pictures.png')))
            panel.get_by_role('button',name='Review and sign final approval',exact=True).click()
            sign_delivery('Review this exact final file')
            expect(panel).to_contain_text('Approved by you',timeout=60000)
            if order=='delivery-first':
                sign_story()
                # Changing the source after final signing never silently keeps
                # the file current. This order tests dispatch, not a bypass.
                from lib.trailer_delivery import snapshot
                with pytest.raises(ValueError,match='paper cut|fresh|story'):
                    snapshot(root)
            from lib.receipts import verified_approvals
            rows=verified_approvals(root,'artifact_review')
            assert {'canon_ingest','edit','compose'} <= {r['stage'] for r in rows}
            assert len(video_calls['posts'])==3 and len(audio_calls)==1 and not errors
        finally:browser.close()
