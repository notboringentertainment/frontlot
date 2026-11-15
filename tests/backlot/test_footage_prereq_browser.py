"""Both price-signing orders and a recoverable background refresh in the board."""
from playwright.sync_api import sync_playwright,expect
from tests.lib.test_story_ingest import root
from tests.backlot.test_early_paid_readiness import isolated_standins,start_browser,set_prices
from tests.backlot.test_footage_controls_browser import browser_board
from tests.backlot.footage_prereq_helpers import simple_sign

def test_footage_then_audio_signing_shows_and_keeps_both_sets(root,monkeypatch,isolated_standins):
    from lib.project_config import load_current_verified_project_config
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url);setup=page.get_by_role('region',name='Footage prerequisites',exact=True)
            price=setup.get_by_role('group',name='Fal footage prices and permissions',exact=True)
            for label,value in [('Highest cash dollars per second','.112'),('Highest credit replacement dollars per second','0'),('Footage prices expire','2099-01-01T12:00')]:price.get_by_label(label,exact=True).fill(value)
            price.get_by_label('Allow separately approved prompts to be sent to Fal.').check();price.get_by_label('Allow separately approved reference pictures to be sent to Fal.').check()
            price.get_by_role('button',name='Prepare footage price settings for signing',exact=True).click()
            review=page.get_by_role('region',name='Price settings approval',exact=True)
            expect(review).to_contain_text('No ElevenLabs audio figures are set.')
            simple_sign(page,'Return to footage setup')
            audio=page.get_by_role('region',name='Trailer audio',exact=True)
            # Hold the ordinary helper at the signing card so both sets can
            # be read before this second signature is given.
            audio.get_by_text('Audio account consent and price ceilings',exact=True).click()
            for label,value in [('Dollar replacement value per credit','.0002'),('Highest speech credits per character','1'),('Highest music credits per minute','1200'),('Highest voice rate multiplier','2'),('Highest sound-effect credits per second','40'),('Prices expire','2099-01-01T12:00')]:audio.get_by_label(label,exact=True).fill(value)
            audio.get_by_role('button',name='Prepare price settings for signing',exact=True).click()
            review=page.get_by_role('heading',name='Review and sign audio',exact=True).locator('..')
            expect(review).to_contain_text('ElevenLabs audio');expect(review).to_contain_text('Fal footage');expect(review).to_contain_text('$0.1120');expect(review).to_contain_text('$10.0000')
            simple_sign(page,'Return to audio')
            settings=load_current_verified_project_config(root).data
            assert settings['budget_usd_cap']==10 and settings['footage_price_basis']['cash_usd_per_second']==.112
            assert settings['audio_price_basis']['dollars_per_credit']['value']==.0002
            assert all(not v for v in isolated_standins.values()) and not page.errors
        finally:browser.close()

def test_successful_refresh_clears_the_previous_background_failure(root,monkeypatch,isolated_standins):
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url);failure={'active':False}
        try:
            page.route('**/api/project/'+root.name+'/voice',lambda r:r.fulfill(status=500,json={'detail':'Invented temporary voice read failure.'}) if failure['active'] else r.continue_())
            page.goto(url);audio=page.get_by_role('region',name='Trailer audio',exact=True)
            expect(audio.locator('.audio-account-state')).to_be_visible(timeout=15000)
            trigger="async project=>{const module=await import('/ui/voice.js');module.renderVoice({project_id:project},()=>{});}"
            failure['active']=True;page.evaluate(trigger,root.name)
            expect(audio.get_by_role('status')).to_contain_text('Invented temporary voice read failure.')
            failure['active']=False;page.evaluate(trigger,root.name)
            expect(audio.get_by_role('status')).to_be_empty(timeout=15000)
            assert all(not v for v in isolated_standins.values()) and not page.errors
        finally:browser.close()
