"""Real browser regressions on invented boards and existing UI modules."""
import io,json,os,threading,time,wave
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project,sign
from tests.tools.test_audio_half_transport import priced,http
from tests.backlot.test_music_panel import prepare as music_request
from tests.tools.test_signed_footage import film,provider,prepare,approve,workspace


def own_take(root):
    from lib import audio_workspace as audio
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000);f.writeframes(b'\0\0'*8000*12)
    path=root/'invented-recording.wav';path.write_bytes(buffer.getvalue())
    return audio.import_music(root,path,'My invented recording','My own work.')


def capture(page,name):
    directory=os.environ.get('FRONTLOT_FOLLOWUP_PICTURES')
    if directory:
        target=Path(directory);target.mkdir(parents=True,exist_ok=True)
        path=target/(name+'.png');assert not path.exists();page.screenshot(path=str(path))


def open_page(browser,url,root):
    page=browser.new_page(viewport={'width':1440,'height':1000});page.errors=[]
    page.on('pageerror',lambda e:page.errors.append(str(e)))
    page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
    page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
    return page


@pytest.mark.parametrize('kind',['audio','video'])
def test_4_playing_take_survives_live_board_update(request,kind,monkeypatch):
    if kind=='audio':
        root,posts,gets=request.getfixturevalue('voice_world');take=own_take(root)
        from lib import audio_workspace as audio
        gate=audio.request_decision(root,take['id'],'accept')
    else:
        root,shot=request.getfixturevalue('film');calls=request.getfixturevalue('provider')
        record=prepare((root,shot));approve(root,record);workspace().execute(root,record['request_id'])
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            if kind=='audio':
                page.get_by_role('button',name=f"Inspect audio_take gate {gate['request_id']}",exact=True).click()
                player=page.locator('.viewer-inner audio')
            else:player=page.get_by_role('article',name='Shot 1 footage',exact=True).locator('video').first
            expect(player).to_have_js_property('readyState',4)
            player.evaluate('(m)=>{window.playingTake=m;m.muted=true;return m.play()}')
            page.wait_for_function('()=>window.playingTake.currentTime>=.5')
            started_at=player.evaluate('(m)=>m.currentTime')
            with page.expect_request(f'{url}/api/project/{root.name}/state',timeout=15000):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(1200)
            playback=player.evaluate('(m)=>({same:m===window.playingTake,paused:m.paused,time:m.currentTime})')
            assert playback['same'] and not playback['paused'] and playback['time']>=started_at-.1,playback
            capture(page,f'playing-{kind}-after-refresh')
            player.evaluate('(m)=>m.pause()')
            paused_at=player.evaluate('(m)=>m.currentTime')
            with page.expect_request(f'{url}/api/project/{root.name}/state',timeout=15000):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(500)
            assert player.evaluate('(m)=>m.paused')
            assert player.evaluate('(m)=>m.currentTime')==pytest.approx(paused_at,abs=.1)
            assert not page.errors
            if kind=='audio':assert not posts and not gets
            else:assert all(len(rows)==1 for rows in calls.values())
        finally:browser.close()


def test_5_keep_waits_for_signing_handoff_before_refresh(voice_world,monkeypatch):
    root,posts,gets=voice_world;own_take(root)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.errors=[];page.on('pageerror',lambda e:page.errors.append(str(e)))
            page.route(url+'/followup-proof',lambda r:r.fulfill(content_type='text/html',body='<html><body></body></html>'))
            page.goto(url+'/followup-proof#k=invented-browser-proof')
            # Mount the actual workspace with a deliberately slow signing
            # handoff. This makes the overlapping-refresh race deterministic.
            page.evaluate('''async project=>{
                const {renderVoice}=await import('/ui/voice.js');
                const original=window.fetch;
                window.fetch=(url,...args)=>{
                    if(String(url).endsWith('/voice')&&window.handoff&&!window.selected)window.refreshDuringHandoff=true;
                    return original(url,...args);
                };
                document.body.replaceChildren(renderVoice({project_id:project},async id=>{
                    window.handoff=id;await new Promise(r=>window.finishHandoff=r);window.selected=id;
                }));
            }''',root.name)
            page.get_by_role('button',name='Keep this take',exact=True).click()
            page.wait_for_function('()=>!!window.handoff')
            page.wait_for_timeout(400)
            assert not page.evaluate('()=>!!window.refreshDuringHandoff'),'The workspace refreshed before the signing handoff finished.'
            page.evaluate('()=>window.finishHandoff()');page.wait_for_function('()=>window.selected===window.handoff')
            capture(page,'keep-signing-handoff');assert not page.errors and not posts
        finally:browser.close()


def test_6_generated_take_banner_describes_the_result(voice_world,monkeypatch):
    root,posts,gets=voice_world;record=music_request(root);sign(root,'audio_spend',record,record['request_id'])
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);panel=page.get_by_role('region',name='Trailer audio')
            panel.get_by_role('button',name='Make this approved take',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Take ready')
            assert len(posts)==1 and not page.errors
        finally:browser.close()


def test_7_writer_recovers_receipted_take_from_the_board(voice_world,monkeypatch):
    from backlot import music_panel as music
    from lib import audio_workspace as audio
    root,posts,gets=voice_world;record=music_request(root);sign(root,'audio_spend',record,record['request_id'])
    original=audio.generated_take
    def stopped(*a,**k):raise OSError('Invented stop after the paid receipt.')
    monkeypatch.setattr(audio,'generated_take',stopped)
    assert not music.send(root,record['request_id'])['success']
    monkeypatch.setattr(audio,'generated_take',original)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);panel=page.get_by_role('region',name='Trailer audio')
            panel.get_by_role('button',name='Recover recorded take',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('without another call or charge')
            expect(panel.locator('.voice-take')).to_contain_text('Waiting for your decision')
            expect(panel.get_by_role('button',name='Recover recorded take',exact=True)).to_have_count(0)
            panel.get_by_role('button',name='Keep this take',exact=True).click()
            expect(page.locator('.viewer-inner h3')).to_have_text('Keep this take')
            expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_enabled()
            capture(page,'recovered-take-ready-for-signature')
            assert len(posts)==1 and not gets and not page.errors
        finally:browser.close()


@pytest.mark.parametrize('case',['licence','notes','prices'])
def test_8_audio_labels_are_true_and_plain(voice_world,monkeypatch,case):
    root,posts,gets=voice_world;take=own_take(root)
    from lib import audio_workspace as audio
    gate=audio.request_decision(root,take['id'],'accept')
    if case=='prices':
        import hashlib
        gate=audio.gate_request(root,'config','project_config',{'config_sha256':hashlib.sha256((root/'project.yaml').read_bytes()).hexdigest()},'Review audio prices.',scope='audio:project_config')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            if case=='licence':
                expect(page.get_by_role('region',name='Trailer audio')).to_contain_text('My own work. No generation charge.')
            elif case=='notes':
                page.get_by_role('button',name=f"Inspect audio_take gate {gate['request_id']}",exact=True).click()
                expect(page.locator('.viewer-inner h3')).to_have_text('Keep this take')
                expect(page.locator('.viewer-inner')).not_to_contain_text('No direction notes.')
            else:
                expect(page.locator('#gates')).to_contain_text('Review audio prices and permissions')
            assert not page.errors and not posts
        finally:browser.close()


def test_9_two_signed_cards_show_changed_ceiling_in_signing_view(voice_world,monkeypatch):
    import hashlib,yaml
    from backlot import music_panel as music
    root,posts,gets=voice_world
    settings=yaml.safe_load((root/'project.yaml').read_text());settings['budget_usd_cap']=.0481
    raw=yaml.safe_dump(settings).encode();(root/'project.yaml').write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    a=music_request(root);b=music_request(root)
    for record in (a,b):sign(root,'audio_spend',record,record['request_id'])
    from lib import audio_workspace as audio
    gate=audio.gate_request(root,'audio_spend',b['request_id'],b,'Review this audio request.')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            page.get_by_role('button',name=f"Inspect audio_spend gate {gate['request_id']}",exact=True).click()
            expect(page.locator('.viewer-inner')).to_contain_text('$0.0480')
            assert music.send(root,a['request_id'])['success']
            page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            expect(page.locator('.viewer-inner')).to_contain_text('remaining production ceiling no longer covers',timeout=15000)
            expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_disabled()
            expect(page.locator('.viewer-inner')).not_to_contain_text('until every picture')
            capture(page,'ceiling-warning-before-send')
            assert len(posts)==1 and not page.errors
            preview=os.environ.get('FRONTLOT_FOLLOWUP_PREVIEW')
            if preview:
                with Path(preview).open('x') as f:json.dump({'url':f'{url}/p/{root.name}#k=invented-browser-proof','gate':gate['request_id']},f)
                deadline=time.monotonic()+120
                while not Path(preview+'.done').exists():
                    assert time.monotonic()<deadline,'The shared preview was not closed in time.'
                    threading.Event().wait(.1)
        finally:browser.close()
