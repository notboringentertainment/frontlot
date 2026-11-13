"""A local placeholder clip cannot acquire a signed trailer keep decision."""
from playwright.sync_api import sync_playwright
from tests.tools.test_trailer_footage_notebook import linked, shot  # noqa: F401
from tests.backlot.test_footage_controls_browser import browser_board
from tests.backlot.test_ui_shots import _clip
from lib import supervised_production as sp


def test_trailer_local_clip_cannot_be_kept_as_paid_footage(linked, tmp_path, monkeypatch):
    root, spec, _ = linked
    sp.prepare(root, spec, user_note='An invented brief.')
    clip = tmp_path/'invented.webm'
    clip.write_bytes(_clip())
    sp.attach(root, spec['shot_id'], clip, user_note='A local pattern, not generated footage.')
    with browser_board(root,monkeypatch) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url)
            response=page.request.get(url.split('/p/')[0]+f'/api/project/{root.name}/footage')
            assert response.ok,response.text()
            panel=page.get_by_role('article',name='Shot 1 footage',exact=True)
            panel.get_by_role('button',name='Keep this take',exact=True).click(timeout=15000)
            panel.get_by_role('alert').get_by_text('no signed footage request',exact=False).wait_for()
            assert not list((root/'.gate-requests').glob('media-*.json'))
            assert not (root/'checkpoint_assets.json').exists()
            page.close()
        finally:
            browser.close()
