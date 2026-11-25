"""A signed account still permits correcting and discarding an unsigned draft."""
from playwright.sync_api import sync_playwright,expect
from tests.lib.test_story_ingest import root
from tests.lib.look_lock_helpers import approve_request
from tests.backlot.test_footage_price_settings import audio
from tests.backlot.test_early_paid_readiness import isolated_standins,start_browser,shot_picture
from tests.backlot.test_footage_controls_browser import browser_board

def test_signed_audio_survives_a_corrected_then_discarded_footage_draft(root,monkeypatch,isolated_standins):
    from lib import media_settings
    approve_request(media_settings.prepare_audio(root,audio()),root);before=(root/'project.yaml').read_bytes()
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url);setup=page.get_by_role('region',name='Footage prerequisites',exact=True)
            price=setup.get_by_role('group',name='Fal footage prices and permissions',exact=True)
            for label,value in [('Highest cash dollars per second','.112'),('Highest credit replacement dollars per second','0'),('Footage prices expire','2099-01-01T12:00')]:price.get_by_label(label,exact=True).fill(value)
            price.get_by_label('Allow separately approved prompts to be sent to Fal.').check();price.get_by_label('Allow separately approved reference pictures to be sent to Fal.').check()
            for value in ('.112','.12'):
                price.get_by_label('Highest cash dollars per second',exact=True).fill(value)
                price.get_by_role('button',name='Prepare footage price settings for signing',exact=True).click()
                review=page.get_by_role('region',name='Price settings approval',exact=True)
                expect(review).to_contain_text('$'+float(value).__format__('.4f'))
                expect(review).to_contain_text('ElevenLabs audio')
                assert (root/'project.yaml').read_bytes()==before
                page.get_by_role('button',name='Return to footage setup',exact=True).click()
                expect(price).to_contain_text('All purchases are paused')
                expect(price.get_by_role('button',name='Discard unsigned footage price draft',exact=True)).to_be_enabled()
            shot_picture(page,'round2-unsigned-prices-paused-with-discard',price)
            price.get_by_role('button',name='Discard unsigned footage price draft',exact=True).click()
            expect(setup.get_by_role('status')).to_contain_text('discarded')
            expect(price.get_by_role('button',name='Discard unsigned footage price draft',exact=True)).to_be_disabled()
            expect(price.get_by_role('button',name='Prepare footage price settings for signing',exact=True)).to_be_enabled()
            expect(page.locator('#gates')).not_to_contain_text('Review footage prices and permissions')
            assert (root/'project.yaml').read_bytes()==before
            assert not media_settings.pending(root)
            media_settings.require_no_draft(root)
            shot_picture(page,'round2-unsigned-prices-discarded',price)
            assert all(not v for v in isolated_standins.values()) and not page.errors
        finally:browser.close()
