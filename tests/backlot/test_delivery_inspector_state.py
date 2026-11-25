"""A local inspector failure cannot turn a real trailer into a missing project."""
import pytest
from playwright.sync_api import sync_playwright, expect

from tests.backlot.test_delivery_browser import delivery_cut
from tests.backlot.test_voice_panel_browser import board, short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film, provider, looks, workspace, sign


def test_footage_read_proves_unchanged_receipt_streams_once(signed_cut,monkeypatch):
    from backlot.footage_controls import inspect
    from lib import gates
    root,calls,posts,*_=signed_cut
    checks=[];original=gates.verify_local_projection
    def counted(*args):
        checks.append(args[1]);return original(*args)
    monkeypatch.setattr(gates,'verify_local_projection',counted)
    value=inspect(root,{})
    assert len(value['shots'][0]['takes'])==3
    assert len(checks)<=2,checks
    assert len(calls['posts'])==3 and len(posts)==1


@pytest.mark.parametrize('failure', ['missing', 'broken'])
def test_kept_footage_board_opens_and_names_the_inspector(delivery_cut, monkeypatch, tmp_path, failure):
    root, calls, posts = delivery_cut
    probe = tmp_path / 'invented-ffprobe'
    if failure == 'broken':
        probe.write_text('#!/bin/sh\nprintf "Invented inspector failure.\\n" >&2\nexit 41\n')
        probe.chmod(0o700)
    monkeypatch.setenv('OPENMONTAGE_FFPROBE', str(probe))
    with board(root, monkeypatch) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            state = page.request.get(url + '/api/project/' + root.name + '/state')
            assert state.ok, state.text()
            assert state.json()['is_trailer']
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            expect(page.get_by_role('banner')).to_contain_text('invented gallery', ignore_case=True)
            expect(page.locator('body')).not_to_contain_text('Project not found')
            expect(page.get_by_role('region', name='Footage workspace', exact=True)).to_contain_text('ffprobe', timeout=15000)
            expect(page.get_by_role('region', name='Trailer delivery', exact=True)).to_contain_text('ffprobe', timeout=15000)
            assert not errors
            assert len(calls['posts']) == 3 and len(posts) == 1
        finally:
            browser.close()
