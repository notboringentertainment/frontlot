"""A kept clip has finished its decision; the card must say so."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_footage_controls_browser import film,provider,browser_board
from tests.tools.test_signed_footage import prepare,approve,workspace,looks,sp

def test_kept_clip_offers_no_second_decision(film,provider,monkeypatch):
    root,shot=film
    record=prepare(film);approve(root,record)
    take=workspace().execute(root,record['request_id'])
    looks.approve_request(workspace().request_decision(root,shot,take['take_id'],'accept'),root)
    sp.select(root,shot,take['take_id'],user_note='Apply this signed keep.')
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url.split('/p/')[0]+'/') else r.abort())
            page.goto(url)
            card=page.get_by_role('article',name='Shot 1 footage',exact=True)
            expect(card.get_by_role('status').filter(has_text='Kept')).to_be_visible(timeout=30000)
            expect(card.get_by_role('button',name='Keep this take',exact=True)).to_have_count(0)
            expect(card.get_by_role('button',name='Decline this take',exact=True)).to_have_count(0)
            expect(page.locator('header.bar .status')).not_to_have_text('Working')
            expect(page.locator('body')).not_to_contain_text('Tabs read look, headshot')
            assert len(provider['posts'])==1 and not errors
        finally:browser.close()
