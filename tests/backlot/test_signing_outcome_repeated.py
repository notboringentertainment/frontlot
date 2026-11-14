"""Forty real footage account-signing journeys, with no intercepted frames."""
import json,time
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board
from tests.backlot.test_followups_browser import open_page
from tests.backlot.test_signing_outcome_browser import wait_for
from tests.backlot.signing_outcome_helpers import (
    project,priced,http,voice_world,film,provider,signing_folder,bounded_signers,held,figures)


@pytest.mark.parametrize('attempt',range(40))
def test_account_review_repeated_without_a_second_signature(request,attempt,monkeypatch,bounded_signers):
    from lib import receipts
    root,reservation,count,ceiling=held(request,'footage',monkeypatch);before=count();events=[];started=time.monotonic()
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            page.on('request',lambda r:events.append({'seconds':round(time.monotonic()-started,3),'method':r.method,'path':r.url.split('/api/project/'+root.name)[-1]}) if '/api/project/'+root.name in r.url else None)
            panel=page.get_by_role('article',name='Shot 1 footage',exact=True)
            panel.get_by_label('What you checked in the service account',exact=True).fill('I checked the invented account. Nothing was charged.')
            panel.get_by_role('button',name='Review account outcome',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=10000)
            wait_for(page,lambda:figures(root,ceiling)[1]==0)
            expect(page.get_by_role('region',name='Footage workspace')).to_be_visible(timeout=7000)
            assert len(receipts.verified_approvals(root,'audio_resolution',entity_id=reservation['reservation_id']))==1
            assert figures(root,ceiling,fresh=True)==pytest.approx([0,0,50])
            assert count()==before and not page.errors and not (root/'checkpoint_assets.json').exists()
            with (root/'signing-journey.json').open('x') as out:json.dump({'attempt':attempt,'events':events,'figures':[0,0,50]},out,indent=2)
        finally:browser.close()
