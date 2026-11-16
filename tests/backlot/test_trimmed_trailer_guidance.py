"""The real board never calls the five working review controls unavailable."""
import json
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_trimmed_trailer_contract import trimmed,human_terminal,no_service_contact,checked_paper


@pytest.mark.parametrize('case',['empty','unsigned-stage'])
def test_working_trailer_reviews_have_true_guidance_in_the_board(trimmed,monkeypatch,case):
    if case=='unsigned-stage':
        checked_paper(trimmed)
        path=trimmed/'checkpoint_script.json';row=json.loads(path.read_text())
        row.update(status='completed',human_approved=True)
        path.write_text(json.dumps(row))
    with board(trimmed,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(url+'/') else route.abort())
            page.goto(f'{url}/p/{trimmed.name}#k=invented-browser-proof')
            if case=='empty':expect(page.locator('body')).to_contain_text('Start by supplying and signing your story in Story workspace.')
            else:
                expect(page.locator('body')).to_contain_text('Paper cut is waiting for your review.')
                expect(page.locator('body')).to_contain_text('Review and sign it in Paper cut workspace.')
            expect(page.locator('body')).not_to_contain_text('Signed stage approval controls are not available yet.')
            expect(page.locator('body')).not_to_contain_text('The agent is paused at this gate.')
            assert not errors
        finally:browser.close()
