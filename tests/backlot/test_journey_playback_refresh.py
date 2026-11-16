import re
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign


@pytest.mark.parametrize('intent',['playing','playing-live','paused','new-cut'])
def test_board_rebuild_keeps_only_the_same_intentionally_playing_file(signed_cut,monkeypatch,intent):
    root,calls,posts,*_=signed_cut
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Cutting workspace',exact=True).click()
            cut=page.get_by_role('region',name='Cutting view',exact=True)
            expect(cut.get_by_label('Kept picture take',exact=True).locator('option')).to_have_count(3,timeout=30000)
            cut.get_by_role('button',name='Prepare saved playback',exact=True).click(timeout=30000)
            expect(cut.get_by_role('status').first).to_contain_text('Saved preview ready.',timeout=120000)
            video=cut.locator('video');expect(video).to_have_js_property('readyState',4,timeout=20000)
            source=video.get_attribute('src')
            video.evaluate('''v=>{window.playbackEvents=[];for(const kind of ['play','pause','ended','emptied','loadedmetadata','seeking','seeked','error'])v.addEventListener(kind,()=>window.playbackEvents.push({kind,time:v.currentTime,source:v.currentSrc,connected:v.isConnected}));}''')
            video.evaluate('(v)=>{v.play().catch(e=>window.playbackFailure=String(e));}')
            page.wait_for_function('(v)=>v.currentTime>0.1',arg=video.element_handle(),timeout=10000)
            if intent=='paused':video.evaluate('(v)=>v.pause()')
            if intent=='new-cut':
                with page.expect_response(lambda r:r.url.endswith('/cut') and r.request.method=='POST',timeout=30000) as saved:
                    cut.get_by_role('button',name='Save cut',exact=True).click()
                assert saved.value.ok
                expect(cut.get_by_role('status').first).to_contain_text(f'Version {saved.value.json()["number"]} saved.')
            # This visible control uses the same rebuild as a live update,
            # without altering a take, cut or signature for the playback case.
            if intent=='playing-live':
                for _ in range(4):
                    with page.expect_response(lambda r:r.url.endswith('/state')):
                        page.evaluate('()=>window.dispatchEvent(new Event("focus"))')
            else:
                # The trailer rail is a progress record, not navigation.
                # Returning to the window exercises the actual live rebuild.
                with page.expect_response(lambda r:r.url.endswith('/state')):
                    page.evaluate('()=>window.dispatchEvent(new Event("focus"))')
            if intent.startswith('playing'):
                assert video.get_attribute('src')==source
                try:page.wait_for_function('(v)=>v.ended',arg=video.element_handle(),timeout=15000)
                except Exception:
                    print('Playback events:',page.evaluate('()=>window.playbackEvents'))
                    print('Playback state:',video.evaluate('(v)=>({paused:v.paused,time:v.currentTime,duration:v.duration,source:v.currentSrc,ready:v.readyState,error:v.error?.message})'))
                    raise
            else:
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                expect(video).to_have_js_property('paused',True)
                if intent=='new-cut':assert video.get_attribute('src') is None
                else:assert video.get_attribute('src')==source
            assert not errors and len(calls['posts'])==3 and len(posts)==1
        finally:browser.close()
