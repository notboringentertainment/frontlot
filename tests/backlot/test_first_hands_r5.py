"""A paid filing notice arriving after the action, and one-shot navigation."""
import json,os,shutil
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel import voice_world,project,priced,http,options
from tests.backlot.test_voice_panel_browser import short_signing_folder
from tests.backlot.signing_outcome_helpers import bounded_signers,figures
from tests.backlot.test_first_hands_icon import icon_board

def capture(page,folder,name):
    path=Path(folder)/name;assert not path.exists();page.screenshot(path=str(path))

@pytest.mark.parametrize('width',[735,1440])
@pytest.mark.parametrize('type_before_notice',[False,True],ids=['reveal-once','typing-cancels'])
def test_late_filing_notice(voice_world,monkeypatch,bounded_signers,tmp_path,width,type_before_notice):
    from backlot import voice_panel as voice
    root,posts,_=voice_world;voice.catalogue(root);voice.prepare_voice(root,options())
    with icon_board(tmp_path,failed_filing=True) as (projects,counter,start,stop),sync_playwright() as pw:
        live=projects/root.name;shutil.copytree(root,live);ready=start(1)
        browser=pw.chromium.launch(headless=True);held=[];stall={'enabled':True}
        try:
            page=browser.new_page(viewport={'width':width,'height':900});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{ready["url"]}p/{root.name}#k='+ready['token'])
            audio=page.get_by_role('region',name='Trailer audio')
            audio.scroll_into_view_if_needed()
            audio.get_by_role('button',name='Review and sign this request',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            approve=page.get_by_role('button',name='Approve and sign',exact=True)
            expect(approve).to_be_visible(timeout=20000)
            question=page.locator('#gate-sign-panel .sign-question')
            question_wording=question.inner_text()
            page.wait_for_timeout(800)
            for item in (question,approve):
                box=item.bounding_box();assert box['y']>=page.locator('.bar').bounding_box()['height'] and box['y']+box['height']<=900
            folder=os.environ.get('R5_PICTURES')
            if folder and not type_before_notice:
                capture(page,folder,f'nov15-polish-r5-{width}-signing.png')
            approve.click();expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=20000)
            page.get_by_role('button',name='Return to audio',exact=True).click()
            page.wait_for_timeout(1500)
            def hold(route):
                if stall['enabled'] and not held:held.append(route)
                else:route.continue_()
            page.route('**/voice',hold)
            page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            for _ in range(100):
                if held:break
                page.wait_for_timeout(50)
            assert held
            audio.get_by_role('button',name='Make this approved take',exact=True).click()
            expect(audio.locator(':scope > [role=status]')).to_contain_text('did not finish',timeout=20000)
            expect(page.locator('[data-held-audio-notice]')).to_have_count(0)
            field=audio.get_by_label('Pronunciation and delivery notes for line 1',exact=True)
            if type_before_notice:field.fill('Keep my unfinished delivery note.')
            page.evaluate("()=>{window.reveals=0;const original=Element.prototype.scrollIntoView;Element.prototype.scrollIntoView=function(...args){if(this.matches('[data-held-audio-notice]'))window.reveals++;return original.apply(this,args)}}")
            stall['enabled']=False;held.pop().continue_();page.unroute('**/voice')
            notice=page.locator('[data-held-audio-notice]');expect(notice).to_have_count(1,timeout=20000)
            page.wait_for_timeout(800)
            if not type_before_notice:
                for item in (notice.get_by_role('alert'),notice.get_by_role('button',name='Review this held charge')):
                    box=item.bounding_box();print('R5 POSITIONS',width,box)
                    assert box['y']>=page.locator('.bar').bounding_box()['height'] and box['y']+box['height']<=900
                assert page.evaluate('window.reveals')==1
                if folder:capture(page,folder,f'nov15-polish-r5-{width}-late-filing.png')
            else:assert page.evaluate('window.reveals')==0
            field.fill('Keep my unfinished delivery note.')
            before=page.evaluate("()=>innerWidth<=760?scrollY:document.querySelector('#viewer').scrollTop")
            changed_reads=[]
            def changed(route):
                value=route.fetch().json();changed_reads.append(1)
                value['record_warnings']=[f'Invented changed reading {len(changed_reads)}']
                route.fulfill(json=value)
            page.route('**/voice',changed)
            for _ in range(3):
                with page.expect_response('**/voice'):
                    page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
                page.wait_for_timeout(500)
            assert len(changed_reads)>=3
            assert page.evaluate('window.reveals')==(0 if type_before_notice else 1)
            assert abs(page.evaluate("()=>innerWidth<=760?scrollY:document.querySelector('#viewer').scrollTop")-before)<2
            expect(field).to_have_value('Keep my unfinished delivery note.')
            assert figures(live,1,fresh=True)==pytest.approx([0,.0088,.9912])
            assert [json.loads(x)['kind'] for x in counter.read_text().splitlines()]==['speech']
            assert 'The gallery is closed.' in question_wording and '$0.0088' in question_wording
            assert not errors
        finally:
            page.unroute_all(behavior='ignoreErrors');browser.close()
