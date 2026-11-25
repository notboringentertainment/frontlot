"""Clip questions and the laptop signing column stay readable and truthful."""
import os
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_footage_controls_browser import film,provider,browser_board
from tests.tools.test_signed_footage import prepare,workspace

@pytest.mark.parametrize('width,height',[(1440,800),(735,900)])
@pytest.mark.parametrize('check',['question','layout'])
def test_clip_question_and_history_do_not_obscure_the_writer(film,provider,monkeypatch,width,height,check):
    root,shot=film;record=prepare(film)
    gate=workspace().request_approval(root,record['request_id'])
    if check=='question':
        other=workspace().prepare_request(root,shot,duration_seconds=6,model=workspace().MODEL)
        other_gate=workspace().request_approval(root,other['request_id'])
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':width,'height':height});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url.split('/p/')[0]+'/') else r.abort())
            page.goto(url)
            page.locator(f'button.need[data-request-id="{gate["request_id"]}"]').click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            question=page.locator('#gate-sign-panel .sign-question')
            expect(page.get_by_role('button',name='Sign and make this clip',exact=True)).to_be_visible(timeout=30000)
            if check=='question':
                expect(question).to_contain_text('Highest charge $0.5000')
                expect(question).to_contain_text(record['prompt'])
                expect(question).to_contain_text('5 seconds')
                page.locator(f'button.need[data-request-id="{other_gate["request_id"]}"]').click()
                expect(page.get_by_label('Footage request details',exact=True)).to_contain_text('$0.6000')
                expect(question).to_contain_text('Highest charge $0.5000')
                expect(question).to_contain_text('5 seconds')
            page.get_by_role('button',name="Show the signing program's text",exact=True).click()
            if check=='question':expect(question).to_contain_text(record['prompt'])
            # Compare actual boxes, including the composer buttons, rather
            # than accepting a screenshot that merely looks uncrowded.
            composer=page.locator('#composer').bounding_box()
            history=page.locator('#history > summary').bounding_box()
            assert composer and history
            assert history['y']>=composer['y']+composer['height']-1,'History covers the writer’s message box or buttons.'
            folder=os.environ.get('COMBINED_NOTES_PICTURES')
            if folder:page.screenshot(path=str(Path(folder)/f'journey-clip-{check}-{width}.png'))
            page.get_by_role('button',name='Not now',exact=True).click()
            assert all(not value for value in provider.values()) and not errors
        finally:browser.close()
