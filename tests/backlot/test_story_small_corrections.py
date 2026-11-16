"""Three readiness corrections change messages and free quote reads only."""
import json
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.signing_outcome_helpers import (project,priced,http,voice_world,film,provider,signing_folder)
from tests.backlot.test_voice_panel_browser import board
from tests.backlot.test_followups_browser import open_page

def test_retyping_unchanged_clip_length_refreshes_its_quote(film,provider,monkeypatch):
    root,shot=film
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);panel=page.get_by_role('article',name='Shot 1 footage',exact=True)
            expect(panel).to_contain_text('Highest charge: $0.4000',timeout=15000)
            field=panel.get_by_label('Clip length in seconds',exact=True)
            field.fill('4');field.press('Tab')
            expect(panel).to_contain_text('Highest charge: $0.4000',timeout=5000)
            assert not provider['posts'] and not page.errors
        finally:browser.close()

def test_audio_explains_pending_cut_review_instead_of_changed_words(voice_world,monkeypatch):
    from backlot import voice_panel as vp
    from tests.tools.test_audio_governance import sign
    from lib import trailer_edit as edit
    root,posts,gets=voice_world;vp.catalogue(root)
    record=vp.prepare_voice(root,{'line_index':0,'voice':'invented_voice','rights_confirmed':True,'notes':''})['request']
    sign(root,'audio_spend',record,record['request_id']);take=vp.send(root,record['request_id'])['take']
    gate=vp.decide(root,take['id'],'accept');sign(root,'audio_take',gate['approval_record'],take['id'])
    old=edit.read_revision(root);value=json.loads(json.dumps(old));value['paper_cut_pending']=True
    with edit._edit_lock(root):edit._commit(root,value,old)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);tile=page.locator('.voice-take[data-kind="speech"]')
            expect(tile).to_contain_text('The saved paper cut is marked for review.',timeout=5000)
            expect(tile).to_contain_text('Cutting workspace')
            expect(tile).not_to_contain_text('The line or file changed.')
            assert len(posts)==1 and not page.errors
        finally:browser.close()

def test_missing_fal_connection_is_named_before_any_contact(film,provider,monkeypatch):
    from tests.tools.test_signed_footage import prepare,approve,workspace
    root,shot=film;record=prepare(film);approve(root,record)
    monkeypatch.delenv('FAL_KEY',raising=False);monkeypatch.delenv('FAL_AI_API_KEY',raising=False)
    with pytest.raises(ValueError,match='Fal is not connected'):
        workspace().execute(root,record['request_id'])
    assert all(not v for v in provider.values())
    from tools.cost_tracker import load_reservations
    assert not load_reservations(root)
