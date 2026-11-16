import os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_stuck_requests_browser import board,short_signing_folder,signed_cut,film,provider

def test_obsolete_needs_you_and_signing_words(film,provider,monkeypatch):
    from backlot.footage_controls import create
    from tests.backlot.test_stuck_requests import change_config
    from lib.footage_requests import MODEL
    root,shot=film;made=create(root,{'shot_id':shot,'model':MODEL,'duration_seconds':5})
    change_config(root,lambda c:c['footage_price_basis'].update(cash_usd_per_second=.2))
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(url+'/p/'+root.name+'#k=invented-browser-proof')
            row=page.locator(f'[data-request-id="{made["gate"]["request_id"]}"]').first
            expect(row).to_contain_text('Shot 1',timeout=30000)
            expect(row).to_contain_text('Needs a fresh spending card')
            row.click()
            expect(page.locator('#gate-detail')).to_contain_text('This request cannot be signed now',timeout=30000)
            expect(page.locator('#gate-detail')).not_to_contain_text('Some evidence is missing or changed')
            expect(page.locator('#gate-detail')).not_to_contain_text('No pictures in this request')
            assert all(not v for v in provider.values())
        finally:browser.close()

def test_waiting_request_survives_aborted_refresh_and_price_draft(signed_cut,monkeypatch,tmp_path):
    from backlot import voice_panel as p
    root,calls,posts,timeline,take=signed_cut
    gets=[]
    class Voices:
        def raise_for_status(self):pass
        def json(self):return {'voices':[{'voice_id':'invented','name':'Invented narrator'}]}
    monkeypatch.setattr('requests.get',lambda *a,**k:(gets.append(1) or Voices()))
    p.catalogue(root)
    made=p.prepare_voice(root,{'line_index':0,'voice':'invented','rights_confirmed':True,'notes':''})
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    folder=Path(os.environ.get('STUCK_R2_PICTURES',tmp_path));folder.mkdir(parents=True,exist_ok=True)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});page.set_default_timeout(60000);errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(url+'/p/'+root.name+'#k=invented-browser-proof')
            shot=page.get_by_role('article',name='Shot 1 footage',exact=True)
            expect(shot.get_by_role('button',name='Create a retake request',exact=True)).to_be_enabled(timeout=30000)
            # Reproduce the interrupted global state read that lost the card.
            page.route('**/state',lambda r:r.abort(),times=1)
            shot.get_by_role('button',name='Create a retake request',exact=True).click()
            page.get_by_role('button',name='Return to footage',exact=True).click()
            expect(shot).to_contain_text('Waiting on your signature',timeout=60000)
            expect(shot.get_by_role('button',name='Review and sign request',exact=True)).to_be_visible(timeout=60000)
            expect(shot.get_by_role('button',name='Create a retake request',exact=True)).to_be_disabled()
            shot.get_by_role('button',name='Review and sign request',exact=True).evaluate('(e)=>e.scrollIntoView({block:"center"})')
            page.screenshot(path=str(folder/'r2-waiting-request-after-return.png'))
            audio=page.get_by_label('Trailer audio',exact=True)
            audio.get_by_text('Audio account consent and price ceilings',exact=True).click()
            audio.get_by_label('Highest speech credits per character',exact=True).fill('2')
            audio.get_by_role('button',name='Prepare price settings for signing',exact=True).click()
            page.get_by_role('button',name='Return to audio',exact=True).click()
            card=audio.locator(f'[data-audio-request="{made["request"]["request_id"]}"]')
            expect(card).to_contain_text('Waiting before review',timeout=60000)
            expect(card).to_contain_text('price-settings draft',timeout=60000)
            expect(card.get_by_role('button',name='Prepare a fresh spending card',exact=True)).to_have_count(0)
            expect(shot.get_by_role('button',name='Set aside unsigned request',exact=True)).to_have_count(0)
            card.evaluate('(e)=>e.scrollIntoView({block:"center"})')
            page.screenshot(path=str(folder/'r2-price-draft-waits-without-retiring-card.png'))
            assert len(posts)==1 and len(calls['posts'])==3 and len(gets)==1 and not errors
        finally:browser.close()
