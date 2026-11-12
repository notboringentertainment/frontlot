"""Existing take controls must tell the truth during the nonspending first half."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
from tests.tools.test_trailer_footage_notebook import linked, shot  # noqa: F401
from tests.backlot.test_trailer_identity_display import display_server
from tests.backlot.test_ui_shots import _clip
from lib import supervised_production as sp


def test_trailer_acceptance_is_visibly_unavailable(linked, tmp_path):
    root, spec, _ = linked
    sp.prepare(root, spec, user_note='An invented brief.')
    clip = tmp_path/'invented.webm'
    clip.write_bytes(_clip())
    sp.attach(root, spec['shot_id'], clip, user_note='A local pattern, not generated footage.')
    with display_server(root, Path(os.environ['OPENMONTAGE_GATES_DIR'])) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(f'{url}/p/{root.name}')
            page.locator('#gates .need.take-need').click(timeout=15000)
            assert page.locator('#viewer .take-approve').is_disabled()
            assert 'Trailer take acceptance is not available yet' in page.locator('#viewer').inner_text()
            assert page.locator('#viewer .take-decline').is_enabled()
            page.close()
        finally:
            browser.close()
