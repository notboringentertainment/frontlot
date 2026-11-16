"""Ben can sign an exact real export while its timed speech notes stay visible."""
import os
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_delivery_browser import delivery_cut
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_delivery_export import approve_rough
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign

@pytest.mark.parametrize('signed_cut',['speech-padding'],indirect=True)
def test_timed_speech_notes_leave_final_approval_to_the_writer(delivery_cut,monkeypatch):
    from lib import trailer_delivery as d
    root,provider,posts=delivery_cut;saved=approve_rough(root)
    item=d.export(root,saved['edit_revision_sha256']);review=item['final_review']
    assert review['status']=='pass',review['issues_found']
    assert any('Voice in sound 1' in s and '0:00.000' in s and '0:03.000' in s for s in review['issues_found'])
    assert review['metadata']['speech_warnings_advisory'] is True
    note='These timed speech notes are listening warnings. Listen to those passages; you can approve this exact file if the pauses and balance are right to your ear.'
    assert note in review['issues_found']
    destination=os.environ.get('DELIVERY_CAPTURE')
    prefix=os.environ.get('DELIVERY_CAPTURE_PREFIX','')+'advisory-'
    def capture(page,name,target):
        if destination:
            target.evaluate('(e)=>e.scrollIntoView({block:"center",behavior:"instant"})')
            path=Path(destination)/(prefix+name+'.png');assert not path.exists();page.screenshot(path=str(path))
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer delivery')
            expect(panel).to_contain_text(note,timeout=60000)
            expect(panel.get_by_role('button',name='Review and sign final approval',exact=True)).to_be_enabled()
            capture(page,'waiting-with-notes',panel)
            panel.get_by_role('button',name='Review and sign final approval',exact=True).click()
            signing=page.get_by_role('region',name='Delivery review');expect(signing).to_contain_text(note,timeout=120000)
            player=signing.locator('video');expect(player).to_have_js_property('readyState',4,timeout=30000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=15000)
            capture(page,'exact-file-review',signing)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=120000)
            page.get_by_role('button',name='Return to delivery',exact=True).click()
            expect(panel).to_contain_text('Approved by you',timeout=60000)
            expect(panel).to_contain_text(note);capture(page,'approved-with-notes',panel)
            assert d.final_state(root,item['id'])=='Approved by you'
            assert len(provider['posts'])==3 and len(posts)==1 and not errors
        finally:browser.close()
