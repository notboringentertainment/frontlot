from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http


def test_writer_reviews_the_whole_paper_and_changed_words_need_a_new_signature(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Paper cut workspace',exact=True).click()
            paper=page.get_by_role('region',name='Trailer paper cut',exact=True)
            paper.get_by_role('button',name='Review and sign the whole paper cut',exact=True).click()
            review=page.get_by_role('region',name='Whole paper-cut review',exact=True)
            expect(review).to_contain_text('The gallery is closed.')
            expect(review).to_contain_text('Keep the light on.')
            for name in ['Hook','Escalation','Reversal','Final invitation','Spoken lines','Title cards','Shot list','Story comparison']:
                expect(review.get_by_role('heading',name=name,exact=True)).to_be_visible()
            expect(review).to_contain_text('Starts at frame 0 (0.000 seconds); lasts 72 frames (3.000 seconds).')
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=20000)
            page.get_by_role('button',name='Return to paper cut',exact=True).click()
            expect(paper.get_by_role('status',name='Whole paper-cut approval',exact=True)).to_have_text('Signed by you',timeout=15000)
            page.reload();page.get_by_role('button',name='Paper cut workspace',exact=True).click()
            expect(paper.get_by_role('status',name='Whole paper-cut approval',exact=True)).to_have_text('Signed by you',timeout=15000)
            paper.get_by_role('group',name='Line 1',exact=True).get_by_label('Exact wording',exact=True).fill('A lantern glows.')
            paper.get_by_role('button',name='Review and sign the whole paper cut',exact=True).click()
            expect(paper.get_by_role('status').first).to_contain_text('Save your changed draft')
            paper.get_by_role('button',name='Save a new version',exact=True).click()
            expect(paper.get_by_role('status').first).to_contain_text('Saved.')
            expect(paper.get_by_role('status',name='Whole paper-cut approval',exact=True)).to_have_text('Needs a fresh look',timeout=15000)
            assert posts==[] and gets==[] and not errors
        finally:browser.close()
