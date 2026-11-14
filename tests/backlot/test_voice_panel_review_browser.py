"""The writer can find signed prices and see that a disconnected board is stopped."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder,sign_and_return,open_prices,capture
from tests.backlot.test_voice_panel import voice_world,options
from tests.tools.test_audio_governance import project,sign
from tests.tools.test_audio_half_transport import priced,http

def test_signed_price_values_survive_reload_and_restore_has_a_true_answer(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof');panel=page.locator('.voice-panel');open_prices(panel)
            field=panel.get_by_label('Dollar replacement value per credit',exact=True)
            expect(field).to_have_value('0.0002')
            help_id=field.get_attribute('aria-describedby');assert help_id
            expect(page.locator('#'+help_id)).to_contain_text('Subscription')
            speech=panel.get_by_label('Highest speech credits per character',exact=True)
            expect(page.locator('#'+speech.get_attribute('aria-describedby'))).to_contain_text('Text to Speech')
            music=panel.get_by_label('Highest music credits per minute',exact=True)
            expect(page.locator('#'+music.get_attribute('aria-describedby'))).to_contain_text('hover')
            multiplier=panel.get_by_label('Highest voice rate multiplier',exact=True)
            expect(page.locator('#'+multiplier.get_attribute('aria-describedby'))).to_contain_text('Voice Library')
            capture(page,'price-guidance',field)
            capture(page,'voice-multiplier-guidance',multiplier)
            field.fill('.0003');panel.get_by_label('Prices expire',exact=True).fill('2099-12-31T23:00')
            panel.get_by_role('button',name='Prepare price settings for signing',exact=True).click();sign_and_return(page)
            page.reload();expect(panel).to_contain_text('One production ceiling',timeout=15000);open_prices(panel)
            expect(field).to_have_value('0.0003');expect(panel.get_by_label('Prices expire',exact=True)).to_have_value('2099-12-31T23:00')
            capture(page,'signed-figures-after-reload',field)
            panel.get_by_role('button',name='Discard draft and restore signed settings',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('already signed')
            capture(page,'already-signed-restore',panel.get_by_role('status'))
            assert posts==[] and gets==[] and not errors
            page.close()
        finally:browser.close()

def test_disconnected_board_shows_the_stop_and_offers_no_paid_action(voice_world,monkeypatch):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world;panel.catalogue(root)
    record=panel.prepare_voice(root,options())['request'];sign(root,'audio_spend',record,record['request_id'])
    monkeypatch.delenv('ELEVENLABS_API_KEY')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof');view=page.locator('.voice-panel')
            expect(view.get_by_role('alert').filter(has_text='not connected').first).to_be_visible(timeout=15000)
            expect(view).to_contain_text('restart the board')
            expect(view.get_by_role('button',name='Load account voices',exact=True)).to_be_disabled()
            expect(view.get_by_role('button',name='Prepare a spending card',exact=True)).to_be_disabled()
            expect(view.get_by_role('button',name='Make this approved take',exact=True)).to_have_count(0)
            capture(page,'account-not-connected',view.locator('.audio-account-state'))
            assert posts==[] and len(gets)==1 and not errors
            page.close()
        finally:browser.close()
