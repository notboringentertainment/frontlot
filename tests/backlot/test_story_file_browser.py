"""The native browser chooser copies exact source bytes, never its original."""
import hashlib,json
from playwright.sync_api import sync_playwright,expect
from tests.lib.test_story_ingest import root
from tests.backlot.test_early_paid_readiness import isolated_standins,shot_picture,start_browser
from tests.backlot.test_footage_controls_browser import browser_board

def test_chosen_story_bytes_replacement_and_real_signing(root,tmp_path,monkeypatch,isolated_standins):
    raw=b'\xef\xbb\xbf  A gallery waits.\r\nThe gallery is closed.\r\n\r\n'
    original=tmp_path/'Invented source.txt';original.write_bytes(raw)
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url)
            panel=page.get_by_role('region',name='Trailer story',exact=True)
            panel.get_by_label('Choose a text file',exact=True).set_input_files(str(original))
            assert panel.get_by_label('Exact story text',exact=True).bounding_box()['width']>500
            shot_picture(page,'source-file-panel',panel.get_by_role('heading').first)
            panel.get_by_role('button',name='Save story for review',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Saved')
            path=root/'sources/story'/(hashlib.sha256(raw).hexdigest()+'.txt')
            assert path.read_bytes()==original.read_bytes()==raw
            panel.get_by_role('button',name='Review and sign this story',exact=True).click()
            review=page.get_by_role('region',name='Story approval',exact=True)
            expect(review).to_be_visible()
            assert review.locator('pre').text_content()==raw.decode('utf-8')
            expect(review).to_contain_text(f'{len(raw)} bytes')
            shot_picture(page,'chosen-file-signing',review.get_by_role('heading').first)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded.',timeout=20000)
            page.get_by_role('button',name='Return to story',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('signed')
            panel.get_by_label('Exact story text',exact=True).fill('A changed invented story.\n')
            panel.get_by_role('button',name='Save story for review',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Nothing is approved until you sign')
            assert path.read_bytes()==original.read_bytes()==raw
            cp=json.loads((root/'checkpoint_canon_ingest.json').read_text())
            assert cp['status']=='awaiting_human' and not cp['human_approved']
            assert all(not v for v in isolated_standins.values()) and not page.errors
        finally:browser.close()
