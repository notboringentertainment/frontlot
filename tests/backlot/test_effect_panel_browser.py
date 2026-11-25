"""The actual board, signer and chooser with local counted stand-ins."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder,sign_and_return
from tests.backlot.test_effect_panel import effect_world,effect_bytes
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http,wav_bytes
import os
from pathlib import Path

def picture(page,name,target):
    folder=os.environ.get('FRONTLOT_EFFECT_PICTURES')
    if folder:
        path=Path(folder)/(name+'.png');assert not path.exists()
        target.evaluate('(e)=>{e.scrollIntoView({block:"center",behavior:"instant"});}');page.screenshot(path=str(path))

def test_effect_board_generate_loop_sign_play_keep_change_decline_and_retake(effect_world,monkeypatch):
    root,posts,gets=effect_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Sound effects workspace',exact=True).click()
            panel=page.get_by_role('region',name='Trailer audio');form=panel.locator('#effect-compose')
            form.get_by_label('Effect description',exact=True).fill('A quiet metal latch clicks once.')
            form.get_by_label('Effect length in seconds',exact=True).fill('2')
            form.get_by_label('Start in the trailer, in seconds',exact=True).fill('1')
            form.get_by_label('Make this a seamless loop',exact=True).check()
            form.get_by_role('button',name='Save effect brief',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Effect brief saved')
            picture(page,'effect-brief',form)
            form.get_by_role('button',name='Prepare effect spending card',exact=True).click()
            card=panel.locator('.voice-spend[data-kind="effect"]');expect(card).to_contain_text('$0.0160')
            expect(card).to_contain_text('Loop: yes');expect(card).to_contain_text('starts at 1 seconds')
            picture(page,'effect-spending-card',card);assert posts==[]
            card.get_by_role('button',name='Review and sign this request',exact=True).click()
            expect(page.locator('.viewer-inner')).to_contain_text('Loop: yes');sign_and_return(page)
            card.get_by_role('button',name='Make this approved take',exact=True).click()
            tile=panel.locator('.voice-take[data-kind="effect"]');expect(tile).to_be_visible(timeout=15000)
            expect(tile).to_contain_text('starts at 1 seconds');picture(page,'effect-waiting',tile)
            player=tile.locator('audio');expect(player).to_have_js_property('readyState',4,timeout=15000)
            player.evaluate('(a)=>a.play()');player.evaluate('(a)=>a.pause()')
            tile.get_by_role('button',name='Keep this take',exact=True).click();sign_and_return(page)
            expect(tile).to_contain_text('Kept',timeout=15000);picture(page,'effect-kept',tile)
            form.get_by_label('Effect description',exact=True).fill('A second latch closes sharply.')
            form.get_by_role('button',name='Save effect brief',exact=True).click()
            expect(tile).to_contain_text('Needs a fresh look',timeout=15000);picture(page,'effect-fresh-look',tile)
            tile.get_by_role('button',name='Decline this take permanently',exact=True).click();sign_and_return(page)
            expect(tile).to_contain_text('Declined',timeout=15000);picture(page,'effect-declined',tile)
            expect(tile.get_by_role('button',name='Keep this take',exact=True)).to_have_count(0)
            tile.get_by_role('button',name='Ask for another take',exact=True).click()
            form.get_by_role('button',name='Prepare effect spending card',exact=True).click()
            expect(panel.locator('.voice-spend[data-kind="effect"]')).to_have_count(2)
            form.get_by_role('button',name='Start a separate sound effect',exact=True).click()
            expect(form.get_by_label('Effect description',exact=True)).to_have_value('')
            form.get_by_label('Effect description',exact=True).fill('A faint distant bell.')
            form.get_by_label('Effect length in seconds',exact=True).fill('2')
            form.get_by_role('button',name='Save effect brief',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Effect brief saved')
            expect(tile).to_contain_text('Declined')
            expect(panel.locator('.voice-budget')).to_contain_text('$0.0002 replacement value of credits')
            assert len(posts)==1 and posts[0][1]['json']['loop'] is True and gets==[] and not errors
            page.close()
        finally:browser.close()

def test_effect_import_chooser_licence_sign_and_final_decline(effect_world,monkeypatch,tmp_path):
    from backlot import music_panel as cues
    from tests.backlot.test_effect_panel import brief
    root,posts,gets=effect_world;cues.save_effect(root,brief())
    source=tmp_path/'own-latch.wav';source.write_bytes(effect_bytes());dialogs=[]
    def choose(**kw):dialogs.append(kw);return str(source) if len(dialogs)!=2 else None
    monkeypatch.setattr(cues,'choose_file',choose)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer audio');form=panel.locator('#effect-import')
            form.get_by_role('button',name='Choose an effect file',exact=True).click();expect(form).to_contain_text('own-latch.wav')
            form.get_by_label('Where this effect came from',exact=True).fill('My own recording')
            form.get_by_role('button',name='Bring in this effect',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('licence note');picture(page,'effect-import-licence-needed',form)
            form.get_by_role('button',name='Choose an effect file',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('No file was chosen')
            form.get_by_label('Licence note',exact=True).fill('I own this recording.')
            form.get_by_role('button',name='Bring in this effect',exact=True).click()
            tile=panel.locator('.voice-take[data-kind="effect"]');expect(tile).to_contain_text('I own this recording.',timeout=15000)
            picture(page,'effect-import-waiting',tile)
            tile.get_by_role('button',name='Keep this take',exact=True).click();sign_and_return(page)
            expect(tile).to_contain_text('Kept',timeout=15000);picture(page,'effect-import-kept',tile)
            tile.get_by_role('button',name='Decline this take permanently',exact=True).click();sign_and_return(page)
            expect(tile).to_contain_text('Declined',timeout=15000)
            expect(tile.get_by_role('button',name='Keep this take',exact=True)).to_have_count(0)
            tile.get_by_role('button',name='Choose another effect file',exact=True).click()
            assert len(dialogs)==2 and posts==[] and gets==[] and not errors;page.close()
        finally:browser.close()

def test_effect_price_and_account_stops_are_plain_and_local_import_remains(effect_world,monkeypatch):
    import hashlib,yaml
    from tests.tools.test_audio_governance import sign
    root,posts,gets=effect_world
    settings=yaml.safe_load((root/'project.yaml').read_text());settings['audio_price_basis'].pop('effect_credits_per_second')
    raw=yaml.safe_dump(settings).encode();(root/'project.yaml').write_bytes(raw);sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    monkeypatch.delenv('ELEVENLABS_API_KEY')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof');panel=page.get_by_role('region',name='Trailer audio')
            expect(panel).to_contain_text('not connected',timeout=15000)
            expect(panel.get_by_role('button',name='Prepare effect spending card',exact=True)).to_be_disabled()
            expect(panel.locator('#effect-compose')).to_contain_text('Sign a sound-effect credits-per-second ceiling')
            expect(panel.get_by_role('button',name='Choose an effect file',exact=True)).to_be_enabled()
            picture(page,'effect-price-and-account-needed',panel.locator('#effect-compose'))
            assert posts==[] and gets==[] and not errors;page.close()
        finally:browser.close()
