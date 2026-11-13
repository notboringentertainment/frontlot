import json,os,socket,threading,time
from contextlib import contextmanager
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http

def capture(page,name,target=None):
    folder=os.environ.get('VOICE_PANEL_SCREENSHOT_DIR')
    if not folder:return
    directory=Path(folder);directory.mkdir(parents=True,exist_ok=True)
    path=directory/(os.environ.get('VOICE_PANEL_SCREENSHOT_PREFIX','voice')+'-'+name+'.png')
    assert not path.exists()
    if target:
        target.evaluate('(e)=>e.scrollIntoView({block:"center",behavior:"instant"})')
        expect(target).to_be_in_viewport()
    page.screenshot(path=str(path),full_page=False)

@pytest.fixture(autouse=True)
def short_signing_folder(monkeypatch,_isolated_gates_dir):
    import tempfile
    folder=tempfile.mkdtemp(prefix='vp-sign-',dir='/tmp')
    monkeypatch.setenv('OPENMONTAGE_GATES_DIR',folder)
    monkeypatch.setenv('FRONTLOT_CLAUDE','/nonexistent/frontlot-no-assistant')

@contextmanager
def board(root,monkeypatch):
    import uvicorn
    import backlot.server as server
    from lib.gates import gates_dir
    assert str(Path.home()).startswith('/tmp/') or Path.home().is_relative_to(Path(os.environ['FRONTLOT_TEST_RUN_ROOT']))
    assert gates_dir().is_relative_to(Path('/tmp')) or gates_dir().is_relative_to(Path('/private/var'))
    assert server.backlot_metadata_dir().is_relative_to(Path(os.environ['FRONTLOT_TEST_RUN_ROOT']))
    with (root/'browser-isolation.json').open('x') as f:json.dump({'home':str(Path.home()),'signing':str(gates_dir()),'metadata':str(server.backlot_metadata_dir())},f)
    monkeypatch.setenv('OPENMONTAGE_PROJECTS_DIR',str(root.parent))
    monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    monkeypatch.setattr(server,'_PROJECTS_ROOT_STR',os.path.normcase(str(root.parent.resolve())))
    monkeypatch.setattr('backlot.state.PROJECTS_DIR',root.parent)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    instance=uvicorn.Server(uvicorn.Config(server.create_app(port=port,capability_token='invented-browser-proof'),host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=instance.run);thread.start()
    for _ in range(100):
        if instance.started:break
        time.sleep(.1)
    assert instance.started
    try:yield f'http://127.0.0.1:{port}'
    finally:
        instance.should_exit=True;thread.join(15);assert not thread.is_alive()
        from backlot.tty import session_paths
        path=session_paths(root.name)['sidecar']
        if path.exists():
            value=json.loads(path.read_text());pid=value.get('pid')
            if pid:
                import signal
                try:os.kill(pid,signal.SIGTERM)
                except ProcessLookupError:pass

def test_writer_voice_controls_sign_play_keep_decline_and_retake(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Paper cut workspace',exact=True).click()
            page.get_by_role('button',name='Voice workspace',exact=True).click()
            panel=page.get_by_role('region',name='Trailer audio')
            expect(panel).to_be_visible(timeout=15000)
            expect(panel).to_contain_text('The gallery is closed.')
            panel.get_by_role('button',name='Load account voices',exact=True).click()
            line=panel.locator('[data-voice-line="0"]')
            line.get_by_label('Voice for line 1',exact=True).select_option('invented_voice')
            line.get_by_label('Pronunciation and delivery notes for line 1',exact=True).fill('Quiet, with a pause.')
            line.get_by_label('I have permission to use this voice.').check()
            capture(page,'line-and-protected-wording',line)
            line.get_by_role('button',name='Prepare a spending card',exact=True).click()
            card=panel.locator('.voice-spend').filter(has_text='Gallery narrator')
            expect(card).to_contain_text('Quiet, with a pause.')
            capture(page,'spending-card',card)
            assert posts==[]
            card.get_by_role('button',name='Review and sign this request',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel').get_by_text('Your approval is recorded. You can close this.',exact=True)).to_be_visible(timeout=20000)
            page.get_by_role('button',name='Return to audio',exact=True).click()
            panel=page.get_by_role('region',name='Trailer audio')
            panel.get_by_role('button',name='Make this approved take',exact=True).click(timeout=15000)
            tile=panel.locator('.voice-take')
            expect(tile).to_be_visible(timeout=15000)
            expect(panel.locator('.voice-budget')).to_contain_text('$0.0002 replacement value of credits')
            capture(page,'recorded-audio-total',panel.locator('.voice-budget'))
            capture(page,'take-waiting',tile)
            player=tile.locator('audio');expect(player).to_have_js_property('readyState',4,timeout=15000)
            player.evaluate('(a)=>a.play()');player.evaluate('(a)=>a.pause()')
            tile.get_by_role('button',name='Keep this take',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel').get_by_text('Your approval is recorded. You can close this.',exact=True)).to_be_visible(timeout=20000)
            page.get_by_role('button',name='Return to audio',exact=True).click()
            panel=page.get_by_role('region',name='Trailer audio');expect(panel.locator('.voice-take')).to_contain_text('Kept',timeout=15000)
            capture(page,'kept-take',panel.locator('.voice-take'))
            from lib import trailer_edit
            saved=trailer_edit.read_revision(root);draft=saved['paper_cut'];draft['lines'][0]['text']='A lantern glows.'
            trailer_edit.save_revision(root,draft,saved['edit_revision_sha256'])
            expect(panel.locator('.voice-take')).to_contain_text('Needs a fresh look',timeout=15000)
            capture(page,'changed-line-needs-review',panel.locator('.voice-take'))
            panel.get_by_role('button',name='Decline this take permanently',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel').get_by_text('Your approval is recorded. You can close this.',exact=True)).to_be_visible(timeout=20000)
            page.get_by_role('button',name='Return to audio',exact=True).click()
            panel=page.get_by_role('region',name='Trailer audio');expect(panel.locator('.voice-take')).to_contain_text('Declined',timeout=15000)
            capture(page,'declined-take',panel.locator('.voice-take'))
            expect(panel.get_by_role('button',name='Keep this take',exact=True)).to_have_count(0)
            panel.get_by_role('button',name='Ask for another take',exact=True).click()
            panel.locator('[data-voice-line="0"]').get_by_role('button',name='Prepare a spending card',exact=True).click()
            expect(panel.locator('.voice-spend').filter(has_text='A lantern glows.')).to_have_count(1)
            assert len(posts)==1 and len(gets)==1 and not errors
            page.close()
        except Exception:
            with (root/'browser-failure.txt').open('x') as f:f.write(page.locator('body').inner_text())
            page.screenshot(path=str(root/'browser-failure.png'),full_page=True)
            print('Browser diagnostic:',root/'browser-failure.txt')
            raise
        finally:browser.close()

def sign_and_return(page):
    page.get_by_role('button',name='Start signing',exact=True).click()
    page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
    expect(page.locator('#gate-sign-panel').get_by_text('Your approval is recorded. You can close this.',exact=True)).to_be_visible(timeout=20000)
    page.get_by_role('button',name='Return to audio',exact=True).click()

def open_prices(panel):
    settings=panel.locator('details').filter(has_text='Audio account consent and price ceilings')
    if not settings.evaluate('(e)=>e.open'):panel.get_by_text('Audio account consent and price ceilings',exact=True).click()

def test_price_controls_restore_and_sign_without_commands(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer audio');expect(panel).to_contain_text('One production ceiling',timeout=15000)
            open_prices(panel)
            for label,value in [('Dollar replacement value per credit','.0002'),('Highest speech credits per character','1'),('Highest music credits per minute','1200'),('Highest voice rate multiplier','2'),('Prices expire','2099-12-31T23:00')]:panel.get_by_label(label,exact=True).fill(value)
            panel.get_by_role('button',name='Prepare price settings for signing',exact=True).click()
            expect(page.get_by_text('Account consent and maximum audio prices',exact=True)).to_be_visible()
            capture(page,'price-settings-signature',page.get_by_text('Account consent and maximum audio prices',exact=True))
            page.get_by_role('button',name='Return to audio',exact=True).click()
            open_prices(panel)
            panel.get_by_role('button',name='Discard draft and restore signed settings',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('unsigned draft was discarded')
            open_prices(panel)
            panel.get_by_role('button',name='Prepare price settings for signing',exact=True).click()
            sign_and_return(page)
            from lib.project_config import load_current_verified_project_config
            assert load_current_verified_project_config(root).data['audio_price_basis']['dollars_per_credit']['value']==.0002
            assert posts==[] and gets==[] and not errors
            page.close()
        finally:browser.close()

def test_unknown_charge_controls_sign_and_apply_account_review(voice_world,monkeypatch):
    from backlot import voice_panel as vp
    from tests.tools.test_audio_governance import sign
    root,posts,gets=voice_world;vp.catalogue(root)
    record=vp.prepare_voice(root,{'line_index':0,'voice':'invented_voice','rights_confirmed':True,'notes':''})['request'];sign(root,'audio_spend',record,record['request_id'])
    def timeout(*a,**kw):posts.append(1);raise TimeoutError()
    monkeypatch.setattr('requests.post',timeout);assert not vp.send(root,record['request_id'])['success']
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer audio');expect(panel).to_contain_text('Review an unresolved audio charge',timeout=15000)
            cash=panel.get_by_label('Actual new cash dollars',exact=True);credits=panel.get_by_label('Actual credit replacement dollars',exact=True)
            expect(cash).to_have_value('0');expect(credits).to_have_value('0');expect(cash).to_be_disabled();expect(credits).to_be_disabled()
            panel.get_by_label('The account was charged.',exact=True).check();expect(cash).to_be_enabled();cash.fill('.1');credits.fill('.2')
            panel.get_by_label('The account was charged.',exact=True).uncheck();expect(cash).to_have_value('0');expect(credits).to_have_value('0');expect(cash).to_be_disabled()
            capture(page,'unresolved-charge',panel.get_by_role('heading',name='Review an unresolved audio charge',exact=True))
            panel.get_by_role('button',name='Apply the signed account outcome',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Sign one account outcome')
            panel.get_by_label('What I checked in the account',exact=True).fill('I checked my account. Nothing was charged.')
            panel.get_by_role('button',name='Review and sign this account outcome',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel').get_by_text('Your approval is recorded. You can close this.',exact=True)).to_be_visible(timeout=20000)
            old=vp.snapshot(root);held=[]
            def hold_first(route):
                if not held:held.append(route)
                else:route.continue_()
            page.route(f'{url}/api/project/{root.name}/voice',hold_first)
            page.get_by_role('button',name='Return to audio',exact=True).click()
            panel.get_by_role('button',name='Apply the signed account outcome',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('account outcome has been recorded',timeout=15000)
            page.wait_for_timeout(500)
            assert held
            held[0].fulfill(status=200,content_type='application/json',body=json.dumps(old))
            expect(panel.get_by_role('heading',name='Review an unresolved audio charge',exact=True)).to_have_count(0)
            assert len(posts)==1 and len(gets)==1 and not errors
            page.close()
        finally:browser.close()
