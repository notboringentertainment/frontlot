"""The real music board and signer, with service and native-dialog stand-ins."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder,sign_and_return,capture
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http,wav_bytes

def test_music_controls_sign_play_keep_change_decline_and_retake(voice_world,monkeypatch):
    root,posts,gets=voice_world
    import requests
    original=requests.post
    def post(url,**kw):
        result=original(url,**kw)
        if '/text-to-speech/' in url:
            data=bytearray(wav_bytes());data[-1]=1;result.content=bytes(data)
        return result
    monkeypatch.setattr(requests,'post',post)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Music workspace',exact=True).click()
            panel=page.get_by_role('region',name='Trailer audio');form=panel.locator('#music-compose')
            expect(form).to_be_visible(timeout=15000)
            form.get_by_label('Music brief',exact=True).fill('A quiet instrumental pulse with room for speech.')
            form.get_by_label('Music length in seconds',exact=True).fill('12')
            form.get_by_role('button',name='Save music brief',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Music brief saved')
            capture(page,'music-brief',form)
            form.get_by_role('button',name='Prepare music spending card',exact=True).click()
            card=panel.locator('.voice-spend[data-kind="music"]')
            expect(card).to_contain_text('$0.0480')
            expect(card).to_contain_text('test trailer only')
            capture(page,'music-spending-card',card)
            assert posts==[]
            card.get_by_role('button',name='Review and sign this request',exact=True).click()
            expect(page.locator('.viewer-inner')).to_contain_text('test trailer only')
            sign_and_return(page)
            card.get_by_role('button',name='Make this approved take',exact=True).click()
            tile=panel.locator('.voice-take[data-kind="music"]');expect(tile).to_be_visible(timeout=15000)
            expect(tile).to_contain_text('test trailer only')
            capture(page,'music-take-waiting',tile)
            player=tile.locator('audio');expect(player).to_have_js_property('readyState',4,timeout=15000)
            player.evaluate('(a)=>a.play()');player.evaluate('(a)=>a.pause()')
            tile.get_by_role('button',name='Keep this take',exact=True).click();sign_and_return(page)
            expect(tile).to_contain_text('Kept',timeout=15000);capture(page,'music-kept',tile)
            form.get_by_label('Music brief',exact=True).fill('A different instrumental ending.')
            form.get_by_role('button',name='Save music brief',exact=True).click()
            expect(tile).to_contain_text('Needs a fresh look',timeout=15000)
            capture(page,'music-needs-review',tile)
            expect(tile.get_by_role('button',name='Keep this take',exact=True)).to_have_count(0)
            tile.get_by_role('button',name='Decline this take permanently',exact=True).click();sign_and_return(page)
            expect(tile).to_contain_text('Declined',timeout=15000);capture(page,'music-declined',tile)
            expect(tile).to_contain_text('test trailer only')
            expect(tile.get_by_role('button',name='Keep this take',exact=True)).to_have_count(0)
            tile.get_by_role('button',name='Ask for another take',exact=True).click()
            form.get_by_role('button',name='Prepare music spending card',exact=True).click()
            expect(panel.locator('.voice-spend').filter(has_text='A different instrumental ending.')).to_have_count(1)
            expect(panel.locator('.voice-budget')).to_contain_text('$0.0002 spent')
            expect(panel.locator('.voice-budget')).to_contain_text('$0.0002 replacement value of credits')
            page.get_by_role('button',name='Voice workspace',exact=True).click()
            panel.get_by_role('button',name='Load account voices',exact=True).click()
            line=panel.locator('[data-voice-line="0"]')
            line.get_by_label('Voice for line 1',exact=True).select_option('invented_voice')
            line.get_by_label('I have permission to use this voice.').check()
            line.get_by_role('button',name='Prepare a spending card',exact=True).click()
            speech=panel.locator('.voice-spend[data-kind="speech"]')
            speech.get_by_role('button',name='Review and sign this request',exact=True).click();sign_and_return(page)
            speech.get_by_role('button',name='Make this approved take',exact=True).click()
            expect(panel.locator('.voice-take[data-kind="speech"]')).to_be_visible(timeout=15000)
            expect(panel.locator('.voice-budget')).to_contain_text('$0.0004 spent')
            expect(panel.locator('.voice-budget')).to_contain_text('$0.9996 remaining')
            expect(panel.locator('.voice-budget')).to_contain_text('$0.0004 replacement value of credits')
            capture(page,'voice-and-music-total',panel.locator('.voice-budget'))
            assert len(posts)==2 and len(gets)==1 and not errors
            page.close()
        finally:browser.close()

def test_import_controls_choose_require_licence_play_sign_and_decline(voice_world,monkeypatch,tmp_path):
    from backlot import music_panel as music
    root,posts,gets=voice_world;source=tmp_path/'own-recording.wav';source.write_bytes(wav_bytes());dialogs=[]
    def choose():dialogs.append(1);return str(source) if len(dialogs)!=2 else None
    monkeypatch.setattr(music,'choose_file',choose)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Music workspace',exact=True).click()
            panel=page.get_by_role('region',name='Trailer audio');form=panel.locator('#music-import')
            form.get_by_role('button',name='Choose a music file',exact=True).click()
            expect(form).to_contain_text('own-recording.wav')
            form.get_by_label('Where this music came from',exact=True).fill('My own recording')
            form.get_by_role('button',name='Bring in this music',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('licence note')
            capture(page,'import-licence-needed',form)
            form.get_by_role('button',name='Choose a music file',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('No file was chosen')
            form.get_by_label('Licence note',exact=True).fill('I own the composition and recording.')
            capture(page,'import-selected',form)
            form.get_by_role('button',name='Bring in this music',exact=True).click()
            tile=panel.locator('.voice-take[data-kind="music"]');expect(tile).to_be_visible(timeout=15000)
            expect(tile).to_contain_text('I own the composition and recording.')
            capture(page,'import-waiting',tile)
            player=tile.locator('audio');expect(player).to_have_js_property('readyState',4,timeout=15000)
            player.evaluate('(a)=>a.play()');player.evaluate('(a)=>a.pause()')
            tile.get_by_role('button',name='Keep this take',exact=True).click()
            expect(page.locator('.viewer-inner')).to_contain_text('My own recording')
            sign_and_return(page)
            expect(tile).to_contain_text('Kept',timeout=15000);capture(page,'import-kept',tile)
            tile.get_by_role('button',name='Decline this take permanently',exact=True).click();sign_and_return(page)
            expect(tile).to_contain_text('Declined',timeout=15000)
            capture(page,'import-declined',tile)
            expect(tile.get_by_role('button',name='Keep this take',exact=True)).to_have_count(0)
            tile.get_by_role('button',name='Choose another music file',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Choose another file')
            expect(panel.locator('.voice-budget')).to_contain_text('$0.0000 spent')
            assert len(dialogs)==2 and posts==[] and gets==[] and not errors
            page.close()
        finally:browser.close()

def test_price_review_leads_with_dollars_and_keeps_raw_settings_below(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer audio')
            panel.get_by_text('Audio account consent and price ceilings',exact=True).click()
            for label,value in [('Dollar replacement value per credit','.0002'),('Highest speech credits per character','1'),('Highest music credits per minute','1200'),('Highest voice rate multiplier','2'),('Prices expire','2099-12-31T23:00')]:panel.get_by_label(label,exact=True).fill(value)
            panel.get_by_role('button',name='Prepare price settings for signing',exact=True).click()
            review=page.locator('.viewer-inner');expect(review).to_contain_text('For the longest saved spoken line: at most $0.0088')
            expect(review).to_contain_text('For one minute of instrumental music: at most $0.2400')
            expect(review).to_contain_text('Whole trailer ceiling: $1.0000')
            expect(review.get_by_text('Highest voice rate multiplier: 2.',exact=False)).not_to_be_visible()
            capture(page,'price-review-dollars',review.get_by_role('heading',name='Account consent and maximum audio prices',exact=True))
            review.get_by_text('Details of these settings',exact=True).click()
            expect(review.get_by_text('Highest voice rate multiplier: 2.',exact=False)).to_be_visible()
            review.get_by_text('Details of these settings',exact=True).click()
            sign_and_return(page)
            assert posts==[] and gets==[] and not errors
            page.close()
        finally:browser.close()
