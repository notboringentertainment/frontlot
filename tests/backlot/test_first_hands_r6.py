"""A signing question follows its own verified session, never the selected card."""
import copy,os
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel import voice_world,project,priced,http,options
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.signing_outcome_helpers import bounded_signers


def requests(root):
    from backlot import voice_panel,music_panel
    voice_panel.catalogue(root)
    speech=voice_panel.prepare_voice(root,options())
    brief=music_panel.save_brief(root,{'text':'An invented slow piano figure for the empty gallery.','seconds':12,'brief_id':None})
    music=music_panel.prepare_music(root,{'brief_id':brief['brief_id']})
    return {'speech':speech,'music':music}


def expected(record):
    return (f'Approve one {record["kind"]} take for “{record["prompt"]}”? Highest charge '
            f'${record["max_cash_usd"]+record["max_credits_usd"]:.4f} '
            f'(${record["max_cash_usd"]:.4f} cash and ${record["max_credits_usd"]:.4f} replacement value of credits).')


def select(page,identity):
    page.get_by_role('button',name='Inspect audio_spend gate '+identity,exact=True).click()


@pytest.mark.parametrize('width',[735,1440])
@pytest.mark.parametrize('kind',['speech','music'])
def test_question_stays_with_signed_request_after_switch_and_text_toggle(voice_world,monkeypatch,bounded_signers,width,kind):
    from lib import receipts
    root,posts,gets=voice_world;cards=requests(root)
    own=cards[kind];other=cards['music' if kind=='speech' else 'speech']
    assert own['request']['max_credits_usd']!=other['request']['max_credits_usd']
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':width,'height':900});errors=[];outside=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else (outside.append(r.request.url),r.abort()))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            select(page,own['gate_request_id'])
            page.get_by_role('button',name='Start signing',exact=True).click()
            question=page.locator('#gate-sign-panel .sign-question')
            expect(question).to_have_text(expected(own['request']),timeout=30000)
            select(page,other['gate_request_id'])
            expect(page.locator('#viewer')).to_contain_text(other['request']['prompt'])
            expect(question).to_have_text(expected(own['request']))
            page.get_by_role('button',name="Show the signing program's text",exact=True).click()
            expect(question).to_have_text(expected(own['request']))
            select(page,own['gate_request_id'])
            expect(page.locator('#viewer')).to_contain_text(own['request']['prompt'])
            select(page,other['gate_request_id'])
            expect(page.locator('#viewer')).to_contain_text(other['request']['prompt'])
            page.get_by_role('button',name="Hide the signing program's text",exact=True).click()
            expect(question).to_have_text(expected(own['request']))
            folder=os.environ.get('R6_PICTURES')
            if folder:
                path=Path(folder)/f'r6-{width}-{kind}-question-other-card-settled.png'
                assert not path.exists();page.screenshot(path=str(path))
            page.get_by_role('button',name='Approve and sign',exact=True).click()
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=30000)
            approval=receipts.find_approval(root,'audio_spend',entity_id=own['request']['request_id'])
            assert approval and approval['record']==own['request']
            assert receipts.find_approval(root,'audio_spend',entity_id=other['request']['request_id']) is None
            assert posts==[] and len(gets)==1 and outside==[] and errors==[]
        finally:browser.close()


@pytest.mark.parametrize('damage',['missing-record','different-request'])
def test_unreadable_or_foreign_session_packet_uses_plain_question(voice_world,monkeypatch,bounded_signers,damage):
    import backlot.server as server
    root,posts,gets=voice_world;cards=requests(root);own=cards['speech'];other=cards['music']
    original=server.load_gate_detail;reads=[]
    def detail(root,identity):
        result=original(root,identity)
        if identity==own['gate_request_id']:
            reads.append(1)
            if len(reads)>=2:  # The signing session's own fresh read.
                result=copy.deepcopy(result)
                if damage=='missing-record':result['packet'].pop('record',None)
                else:result['request_id']=other['gate_request_id']
        return result
    monkeypatch.setattr(server,'load_gate_detail',detail)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':735,'height':900})
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof');select(page,own['gate_request_id'])
            page.get_by_role('button',name='Start signing',exact=True).click()
            question=page.locator('#gate-sign-panel .sign-question')
            expect(question).to_have_text('Approve this?',timeout=30000)
            select(page,other['gate_request_id'])
            expect(page.locator('#viewer')).to_contain_text(other['request']['prompt'])
            page.get_by_role('button',name="Show the signing program's text",exact=True).click()
            expect(question).to_have_text('Approve this?')
            page.get_by_role('button',name='Not now',exact=True).click()
            assert posts==[] and len(gets)==1
        finally:browser.close()
