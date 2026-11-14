"""Invented footage review cards use real local board routes and counting fakes."""
import copy
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright
from tests.tools.test_signed_footage import film,provider,prepare,approve,workspace  # noqa: F401
from tests.lib import look_lock_helpers as looks
from tests.backlot.test_trailer_identity_display import display_server
from lib import supervised_production as sp
from lib.trailer_edit import save_revision
import pytest


def test_signed_request_take_and_timing_states_in_browser(film,provider,tmp_path,monkeypatch):
    from tests import conftest as isolation
    assert not isolation._unsafe_locations(os.environ)
    root,shot=film;record=prepare(film)
    workspace().request_approval(root,record['request_id'])
    pictures=tmp_path/'pictures';pictures.mkdir()
    with display_server(root,Path(os.environ['OPENMONTAGE_GATES_DIR'])) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'{url}/p/{root.name}?static=1')
            page.get_by_role('button',name='Review: Make this clip',exact=True).wait_for()
            page.locator('#gates .need').filter(has_text='Make this clip').first.click()
            details=page.locator('[aria-label="Footage request details"]')
            assert 'Highest charge: $0.5000' in details.inner_text()
            assert record['prompt'] in details.inner_text()
            assert '[object Object]' not in details.inner_text()
            assert 'dress blues' in details.inner_text()
            assert 'Fal' in details.inner_text() and 'Kling o3 reference video' in details.inner_text()
            assert page.locator('#gate-detail .screen img').count()==1
            assert page.locator('.footage-review .look-row p').first.evaluate('(n)=>getComputedStyle(n).color')=='rgb(37, 42, 36)'
            page.screenshot(path=str(pictures/'request.png'),full_page=True)
            page.locator('#gate-detail .screen img').scroll_into_view_if_needed()
            page.screenshot(path=str(pictures/'references-and-signing.png'),full_page=True)
            approve(root,record);take=workspace().execute(root,record['request_id'])
            decision=workspace().request_decision(root,shot,take['take_id'],'accept')
            page.reload();page.locator('#gates .need').filter(has_text='Review this take').first.click()
            video=page.locator('video[aria-label="Full footage take"]')
            assert video.get_attribute('src').endswith(take['path'])
            assert video.get_attribute('controls') is not None
            video.evaluate('(v)=>{v.muted=true;return v.play()}')
            video.evaluate('(v)=>{v.currentTime=v.duration-0.2}')
            page.wait_for_function("()=>[...document.querySelectorAll('video')].some(v=>v.ended)",timeout=12000)
            assert 'Declining is final' in page.locator('#gate-detail').inner_text()
            page.screenshot(path=str(pictures/'take.png'),full_page=True)
            looks.approve_request(decision,root);sp.select(root,shot,take['take_id'],user_note='Use the signed take.')
            page.reload()
            preview=page.locator('.paper-preview')
            assert 'Accepted footage available' in preview.inner_text()
            page.screenshot(path=str(pictures/'accepted.png'),full_page=True)
            current=json.loads((root/'artifacts/edit_decisions.json').read_text())
            changed=copy.deepcopy(current['paper_cut']);changed['shots'][0]['purpose']='The keeper looks towards another invented doorway.'
            save_revision(root,changed,current['edit_revision_sha256'])
            page.reload()
            assert 'Needs a fresh look' in preview.inner_text()
            page.screenshot(path=str(pictures/'stale.png'),full_page=True)
            decline=workspace().request_decision(root,shot,take['take_id'],'decline')
            looks.approve_request(decline,root);sp.decline(root,shot,take['take_id'],user_note='A final signed decline.')
            page.reload()
            assert 'UNFINISHED PLACEHOLDER' in preview.inner_text()
            page.screenshot(path=str(pictures/'declined.png'),full_page=True)
            assert workspace().take_state(root,shot,take['take_id'])=='Declined'
            assert not errors
            assert not (root/'checkpoint_assets.json').exists()
            assert len(provider['posts'])==1
            # The test runner can retain these invented pictures for a build report.
            destination=os.environ.get('FRONTLOT_FOOTAGE_PICTURES')
            if destination:
                target=Path(destination);target.mkdir(parents=True,exist_ok=True)
                for source in pictures.glob('*.png'):
                    with (target/source.name).open('xb') as stream:stream.write(source.read_bytes())
            page.close()
        finally:browser.close()


@pytest.mark.parametrize('state',['unknown','missing-story'])
def test_footage_refusal_states_in_browser(film,provider,tmp_path,monkeypatch,state):
    from tools.video import _shared
    from lib import audio_workspace
    root,shot=film;record=prepare(film)
    if state=='missing-story':
        workspace().request_approval(root,record['request_id'])
        (root/'checkpoint_canon_ingest.json').write_text('{}')
        title='Make this clip';message='current signed story'
    else:
        approve(root,record)
        monkeypatch.setattr(_shared,'fal_queue_wait',lambda *a,**k:(_ for _ in ()).throw(TimeoutError('Invented uncertain result.')))
        with pytest.raises(ValueError):workspace().execute(root,record['request_id'])
        from tools.cost_tracker import nonterminal_reservations
        reservation=nonterminal_reservations(root)[0]
        audio_workspace.request_resolution(root,reservation['reservation_id'],False,0,0,'Checked the invented service account and confirmed no charge.')
        title='Review uncertain charge';message='Spending is blocked'
    with display_server(root,Path(os.environ['OPENMONTAGE_GATES_DIR'])) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100})
            page.goto(f'{url}/p/{root.name}?static=1')
            page.locator('#gates .need').filter(has_text=title).first.click()
            page.get_by_text(message,exact=False).first.wait_for()
            if state=='missing-story':assert page.locator('#gate-detail .gate-primary').is_disabled()
            destination=os.environ.get('FRONTLOT_FOOTAGE_PICTURES')
            if destination:
                target=Path(destination);target.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(target/(state+'.png')),full_page=True)
            assert len(provider['posts'])==(1 if state=='unknown' else 0)
            page.close()
        finally:browser.close()
