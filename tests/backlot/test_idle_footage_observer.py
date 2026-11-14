from playwright.sync_api import sync_playwright,expect
from tests.tools.test_signed_footage import film
from tests.backlot.test_voice_panel_browser import board,short_signing_folder

def test_read_only_idle_page_does_not_poll_or_post_quotes(film,monkeypatch):
    root,_=film
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();calls=[];errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:calls.append((r.method,r.url)) if '/footage' in r.url and '/ui/' not in r.url else None)
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(url+'/p/'+root.name)
            expect(page.get_by_role('region',name='Footage workspace')).to_be_visible(timeout=20000)
            page.wait_for_timeout(5000)
            assert not [c for c in calls if c[0]=='POST']
            assert len([c for c in calls if c[0]=='GET'])<=2
            assert not errors
        finally:browser.close()
