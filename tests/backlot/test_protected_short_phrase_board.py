import os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_trailer_editor_round3 import editor_server
from tests.lib.test_trailer_edit import trailer,paper,signed_story,human_terminal
from tests.lib.test_protected_short_phrases import canon,row
from lib import trailer_edit as edit

def test_short_warning_is_beside_line_and_clears_on_edit(tmp_path):
    root=trailer(tmp_path);signed_story(root,canon('I know what you did.'))
    p=paper();p['lines']=[row('I know.')];edit.save_revision(root,p,None)
    with editor_server(root.parent) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            line=page.locator('fieldset').filter(has=page.locator('legend',has_text='Line 1'))
            warning=line.locator('.short-phrase-warning')
            expect(warning).to_contain_text('I know what you did.')
            expect(warning).to_contain_text('not clearance')
            screenshot=os.environ.get('SHORT_PHRASE_SCREENSHOT')
            if screenshot:line.scroll_into_view_if_needed();page.screenshot(path=screenshot,full_page=True)
            page.get_by_role('button',name='Play placeholders',exact=True).click()
            page.get_by_role('button',name='Pause',exact=True).click()
            line.get_by_label('Exact wording').fill('The lantern glows.')
            expect(warning).to_be_empty()
            page.get_by_role('button',name='Save a new version',exact=True).click()
            expect(page.get_by_role('button',name='Open version 2',exact=False)).to_be_visible()
            expect(warning).to_be_empty()
            assert not edit.paper_state(root)['story_check']['warnings']
            assert not errors
        finally:browser.close()
