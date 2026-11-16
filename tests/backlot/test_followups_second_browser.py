"""Live signing-screen regressions for the second follow-up batch."""
import json,os,threading,time
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_followups_browser import open_page,capture
from tests.backlot.test_voice_panel_browser import board,short_signing_folder,open_prices
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http
from tests.tools.test_signed_footage import film,provider,prepare,approve,workspace


def test_1_footage_keep_signing_keeps_playing_video(film,provider,monkeypatch):
    root,shot=film;record=prepare(film);approve(root,record);take=workspace().execute(root,record['request_id'])
    gate=workspace().request_decision(root,shot,take['take_id'],'accept')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            page.get_by_role('button',name='Review: Review this take',exact=True).click()
            player=page.get_by_label('Full footage take',exact=True)
            expect(player).to_have_js_property('readyState',4)
            player.evaluate('(v)=>{window.signingTake=v;v.muted=true;v.playbackRate=.5;return v.play()}')
            page.wait_for_function('()=>window.signingTake.currentTime>=.5')
            before=player.evaluate('(v)=>v.currentTime')
            with page.expect_request(f'{url}/api/project/{root.name}/state'):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(1000)
            value=player.evaluate('(v)=>({same:v===window.signingTake,paused:v.paused,time:v.currentTime})')
            assert value['same'] and not value['paused'] and value['time']>=before,value
            capture(page,'footage-keep-signing-after-refresh')
            player.evaluate('(v)=>v.pause()');paused=player.evaluate('(v)=>v.currentTime')
            with page.expect_request(f'{url}/api/project/{root.name}/state'):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(500)
            assert player.evaluate('(v)=>v.paused')
            assert player.evaluate('(v)=>v.currentTime')==pytest.approx(paused,abs=.1)
            assert all(len(rows)==1 for rows in provider.values()) and not page.errors
            preview=os.environ.get('FRONTLOT_FOLLOWUP_SECOND_PREVIEW')
            if preview:
                with Path(preview).open('x') as f:json.dump({'url':f'{url}/p/{root.name}#k=invented-browser-proof'},f)
                deadline=time.monotonic()+120
                while not Path(preview+'.done').exists():
                    assert time.monotonic()<deadline,'The shared preview was not closed in time.'
                    threading.Event().wait(.1)
        finally:browser.close()


def test_3_changed_audio_file_has_no_recovery_control(voice_world,monkeypatch):
    from tests.backlot.test_followups_second_batch import paid_without_record
    root,posts,gets=voice_world;record=paid_without_record(root,monkeypatch)
    path=root/'assets/audio'/(record['request_id']+'.audio');path.write_bytes(path.read_bytes()+b'changed')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);panel=page.get_by_role('region',name='Trailer audio')
            expect(panel.locator('.voice-spend')).to_contain_text('no longer matches its receipt')
            expect(panel.get_by_role('button',name='Recover recorded take',exact=True)).to_have_count(0)
            capture(page,'changed-file-recovery-warning')
            assert len(posts)==1 and not gets and not page.errors
        finally:browser.close()


def test_4_price_settings_wait_for_signing_handoff(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.route(url+'/handoff',lambda r:r.fulfill(content_type='text/html',body='<html><body></body></html>'))
            page.goto(url+'/handoff#k=invented-browser-proof')
            page.evaluate('''async project=>{
                const {renderVoice}=await import('/ui/voice.js');const fetch=window.fetch;
                window.fetch=(url,...args)=>{if(String(url).endsWith('/voice')&&window.handoff&&!window.selected)window.overlap=true;return fetch(url,...args)};
                document.body.append(renderVoice({project_id:project},async id=>{window.handoff=id;await new Promise(r=>window.finish=r);window.selected=id}));
            }''',root.name)
            panel=page.get_by_role('region',name='Trailer audio');open_prices(panel)
            panel.get_by_role('button',name='Prepare price settings for signing',exact=True).click()
            page.wait_for_function('()=>!!window.handoff');page.wait_for_timeout(300)
            assert not page.evaluate('()=>!!window.overlap'),'Price settings refreshed before signing handoff finished.'
            page.evaluate('()=>window.finish()');page.wait_for_function('()=>window.selected===window.handoff')
            capture(page,'price-signing-handoff');assert not posts and not gets and not errors
        finally:browser.close()
