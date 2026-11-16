"""Bad settings may stop purchases, but must not hide recorded work or holds."""
import pytest
import yaml
from playwright.sync_api import sync_playwright, expect

from tests.backlot.test_voice_panel_browser import board, short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced, http
from tests.backlot.test_first_hands import failed_filing


def test_audio_read_proves_each_unchanged_receipt_stream_once(voice_world,monkeypatch):
    from backlot.voice_panel import snapshot
    from lib import gates
    root,posts,gets=voice_world
    checks=[];original=gates.verify_local_projection
    def checked(*args):
        checks.append(args[1]);return original(*args)
    monkeypatch.setattr(gates,'verify_local_projection',checked)
    before={p:p.read_bytes() for p in (root/'approvals.jsonl',root/'cost_log.json') if p.exists()}
    value=snapshot(root)
    assert value['budget']['ceiling']==1
    assert len(checks)<=2,checks
    assert all(p.read_bytes()==raw for p,raw in before.items())
    assert not posts and not gets


@pytest.mark.parametrize('damage', ['duplicate-provider', 'unreadable-yaml'])
def test_reopened_audio_keeps_forms_and_held_money_when_settings_are_unreadable(voice_world, monkeypatch, damage):
    from backlot.voice_panel import snapshot
    root, posts, gets = voice_world
    failed_filing(root, monkeypatch)
    before_contacts=(len(posts),len(gets))
    original = (root / 'project.yaml').read_bytes()
    booked = (root / 'cost_log.json').read_bytes()
    if damage == 'duplicate-provider':
        config = yaml.safe_load(original)
        config['provider_egress'].append(config['provider_egress'][0])
        (root / 'project.yaml').write_text(yaml.safe_dump(config))
    else:
        (root / 'project.yaml').write_text('version: [an unreadable settings document')
    value = snapshot(root)
    assert value['budget']['held'] == .0088 and value['budget']['spent'] == 0
    assert value['budget']['ceiling'] is None and value['budget']['remaining'] is None
    assert len(value['unresolved_requests']) == 1 and not value['price_settings_signed']
    with board(root, monkeypatch) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':735,'height':900})
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            audio = page.get_by_role('region',name='Trailer audio',exact=True)
            expect(audio.get_by_label('Effect description',exact=True)).to_be_visible()
            expect(audio).to_contain_text('settings cannot be read')
            expect(audio).to_contain_text('$0.0088 held')
            expect(audio).to_contain_text('remaining ceiling is unavailable')
            expect(audio.get_by_role('button',name='Prepare effect spending card',exact=True)).to_be_disabled()
            expect(audio.get_by_role('button',name='Review and sign this account outcome',exact=True)).to_be_visible()
            page.reload()
            expect(audio.get_by_label('Effect description',exact=True)).to_be_visible()
            assert (len(posts),len(gets)) == before_contacts
        finally:
            browser.close()
    assert (root / 'cost_log.json').read_bytes() == booked
    (root / 'project.yaml').write_bytes(original)
    restored = snapshot(root)
    assert restored['budget']['held'] == .0088 and restored['budget']['remaining'] == .9912
    assert (root / 'cost_log.json').read_bytes() == booked and len(posts) == 1
