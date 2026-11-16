"""Invented first-use faults, exercised with real signing and counted services."""
import time
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel import voice_world,project,priced,http,options,sign
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_followups_browser import open_page
from tests.backlot.signing_outcome_helpers import bounded_signers,figures

def failed_filing(root,monkeypatch):
    from backlot import voice_panel as voice
    from lib import audio_workspace
    from tools.cost_tracker import nonterminal_reservations
    voice.catalogue(root);record=voice.prepare_voice(root,options())['request']
    sign(root,'audio_spend',record,record['request_id'])
    original=audio_workspace.probe_audio
    def unavailable(*a,**k):raise FileNotFoundError('Invented local filing failure.')
    monkeypatch.setattr(audio_workspace,'probe_audio',unavailable)
    assert not voice.send(root,record['request_id'])['success']
    monkeypatch.setattr(audio_workspace,'probe_audio',original)
    reservation,=nonterminal_reservations(root)
    assert reservation['state']=='pending_billing'
    return reservation

def test_account_review_click_survives_focus_refresh(voice_world,monkeypatch,bounded_signers):
    root,posts,gets=voice_world;reservation=failed_filing(root,monkeypatch)
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            form=page.get_by_role('region',name='Trailer audio')
            form.get_by_label('The account was charged.',exact=True).check()
            form.get_by_label('Actual credit replacement dollars',exact=True).fill('0.0038')
            form.get_by_label('What I checked in the account',exact=True).fill('I checked the invented account; nineteen credits were charged.')
            button=form.get_by_role('button',name='Review and sign this account outcome',exact=True)
            def changed(route):
                data=route.fetch().json();data['record_warnings']=['Invented changed information during account review.'];route.fulfill(json=data)
            page.route('**/voice',changed)
            button.scroll_into_view_if_needed();box=button.bounding_box()
            page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down()
            with page.expect_response(url+'/api/project/'+root.name+'/state'):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(400);page.mouse.up()
            expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_visible(timeout=7000)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=15000)
            deadline=time.monotonic()+10
            while figures(root,1)[1] and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert figures(root,1,fresh=True)==pytest.approx([.0038,0,.9962])
            assert len(posts)==1 and not page.errors
            return_button=page.get_by_role('button',name='Return to audio',exact=True)
            if return_button.count() and return_button.is_visible():return_button.click()
            form.get_by_role('button',name='Load account voices',exact=True).click()
            form.get_by_label('Voice for line 1',exact=True).select_option('invented_voice')
            form.get_by_label('I have permission to use this voice.').check()
            form.get_by_role('button',name='Prepare a spending card',exact=True).click()
            waiting=form.locator('.voice-spend').filter(has=page.get_by_role('heading',name='Waiting for your signature',exact=True))
            waiting.get_by_role('button',name='Review and sign this request',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=15000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded.',timeout=15000)
            page.get_by_role('button',name='Return to audio',exact=True).click()
            form.get_by_role('button',name='Make this approved take',exact=True).click()
            expect(form.locator('.voice-take')).to_be_visible(timeout=15000)
            assert figures(root,1,fresh=True)==pytest.approx([.004,0,.996])
            assert len(posts)==2 and not page.errors
        finally:browser.close()

def test_refresh_keeps_viewer_focus_and_unsaved_words(voice_world,monkeypatch):
    root,_,_=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            notes=page.get_by_label('Pronunciation and delivery notes for line 1',exact=True)
            notes.fill('Keep these unfinished words.');notes.focus()
            page.evaluate('()=>{window.keptViewer=document.getElementById("viewer");window.keptField=document.activeElement;}')
            with page.expect_response(url+'/api/project/'+root.name+'/state'):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(300)
            assert page.evaluate('()=>document.getElementById("viewer")===window.keptViewer')
            assert page.evaluate('()=>document.activeElement===window.keptField')
            expect(notes).to_have_value('Keep these unfinished words.')
        finally:browser.close()

def test_refused_writer_token_is_visible_across_the_page(voice_world,monkeypatch):
    from backlot import server
    root,posts,_=voice_world;original=server.create_app;apps=[]
    def create(*a,**k):app=original(*a,**k);apps.append(app);return app
    monkeypatch.setattr(server,'create_app',create)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            notes=page.get_by_label('Pronunciation and delivery notes for line 1',exact=True)
            notes.fill('These unfinished words must stay visible.');notes.focus()
            apps[0].state.capability_token='invented-rotated-token'
            page.get_by_role('button',name='Load account voices',exact=True).click()
            banner=page.get_by_role('alert').filter(has_text='Open Front Lot from its app icon')
            expect(banner).to_be_visible(timeout=7000)
            expect(notes).to_have_value('These unfinished words must stay visible.')
            notes.scroll_into_view_if_needed();expect(notes).to_be_in_viewport()
            assert page.locator('#viewer').bounding_box()['height']>100
            page.set_viewport_size({'width':390,'height':844})
            page.wait_for_function("()=>document.getElementById('table').getBoundingClientRect().top>=document.getElementById('writer-warning').getBoundingClientRect().bottom")
            notes.scroll_into_view_if_needed();expect(notes).to_be_in_viewport()
            held=[];page.route('**/state',lambda route:held.append(route))
            page.reload(wait_until='domcontentloaded');expect(banner).to_be_visible(timeout=7000)
            expect(banner).not_to_contain_text('Your unfinished words remain in this tab')
            expect(page.locator('#viewer')).to_contain_text('Loading this film')
            assert not posts
        finally:browser.close()

def test_paper_seconds_required_words_and_open_unsigned_prices(voice_world,monkeypatch):
    root,_,_=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            page.get_by_role('button',name='Paper cut workspace',exact=True).click()
            paper=page.get_by_role('region',name='Trailer paper cut')
            paper.get_by_role('button',name='Add shot',exact=True).click()
            shot=paper.get_by_role('group',name='Shot 2',exact=True)
            expect(shot.get_by_label('Shot purpose',exact=True)).to_have_attribute('required','')
            shot.get_by_label('Screen time in seconds',exact=True).fill('5.25')
            expect(shot).to_contain_text('126 frames')
            expect(shot.get_by_label('Difficulty (optional planning note)',exact=True)).to_have_value('Not assessed')
            expect(page.get_by_role('region',name='Next step')).to_be_visible()
        finally:browser.close()


def test_saved_paper_refreshes_its_audio_without_a_live_feed(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}?static=1#k=invented-browser-proof')
            paper=page.get_by_role('region',name='Trailer paper cut',exact=True)
            audio=page.get_by_role('region',name='Trailer audio',exact=True)
            expect(audio.locator('[data-voice-line="0"]')).to_be_visible()
            paper.get_by_role('button',name='Add spoken line',exact=True).click()
            paper.get_by_role('group',name='Line 2',exact=True).get_by_label('Exact wording',exact=True).fill('A lantern glows.')
            paper.get_by_role('button',name='Save a new version',exact=True).click()
            expect(paper.get_by_role('status').first).to_contain_text('Saved.')
            expect(audio.locator('[data-voice-line="1"]')).to_contain_text('A lantern glows.',timeout=7000)
            assert not posts and not gets and not errors
        finally:browser.close()


def test_audio_snapshot_from_before_paper_save_is_never_displayed(voice_world,monkeypatch):
    from tests.backlot.test_signing_outcome_browser import wait_for
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);held=[]
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.goto(f'{url}/p/{root.name}?static=1#k=invented-browser-proof')
            audio=page.get_by_role('region',name='Trailer audio',exact=True)
            expect(audio.locator('[data-voice-line="0"]')).to_be_visible()
            def delay(route):
                value=route.fetch().json()
                if not held:value['record_warnings']=['An invented stale snapshot must not be displayed.']
                held.append((route,value))
            page.route('**/voice',delay)
            page.evaluate("id=>import('/ui/voice.js').then(m=>m.renderVoice({project_id:id},()=>{}))",root.name)
            wait_for(page,lambda:len(held)==1)
            paper=page.get_by_role('region',name='Trailer paper cut',exact=True)
            paper.get_by_role('button',name='Add spoken line',exact=True).click()
            paper.get_by_role('group',name='Line 2',exact=True).get_by_label('Exact wording',exact=True).fill('A lantern glows.')
            paper.get_by_role('button',name='Save a new version',exact=True).click()
            expect(paper.get_by_role('button',name='Save a new version',exact=True)).to_be_enabled(timeout=15000)
            held[0][0].fulfill(json=held[0][1]);wait_for(page,lambda:len(held)>=2)
            expect(audio).not_to_contain_text('An invented stale snapshot must not be displayed.')
            for route,value in held[1:]:route.fulfill(json=value)
            page.unroute('**/voice',delay)
            expect(audio.locator('[data-voice-line="1"]')).to_contain_text('A lantern glows.')
            assert not posts and not gets
        finally:browser.close()


def test_results_stay_beside_the_control_that_was_used(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            paper=page.get_by_role('region',name='Trailer paper cut',exact=True)
            paper.get_by_role('button',name='Add shot',exact=True).click()
            save=paper.get_by_role('button',name='Save a new version',exact=True);save.click()
            expect(save.locator('xpath=following-sibling::*[1]')).to_contain_text('purpose')
            paper.get_by_role('group',name='Shot 2',exact=True).get_by_label('Shot purpose',exact=True).fill('A quiet closing invitation.')
            paper.get_by_role('group',name='Shot 1',exact=True).get_by_label('Screen time in seconds',exact=True).fill('1')
            paper.get_by_role('group',name='Shot 2',exact=True).get_by_label('Screen time in seconds',exact=True).fill('2')
            save.click();expect(save.locator('xpath=following-sibling::*[1]')).to_contain_text('Saved.')
            check=paper.get_by_role('button',name='Check paper cut against approved story',exact=True);check.click()
            expect(check.locator('xpath=following-sibling::*[1]')).to_contain_text('compared with the approved story')
            expect(save.locator('xpath=following-sibling::*[1]')).to_contain_text('Saved.')
            audio=page.get_by_role('region',name='Trailer audio',exact=True)
            load=audio.get_by_role('button',name='Load account voices',exact=True);load.click()
            expect(load.locator('xpath=following-sibling::*[1]')).to_contain_text('Account voices loaded')
            prepare=audio.get_by_role('button',name='Prepare a spending card',exact=True);prepare.click()
            expect(prepare.locator('xpath=following-sibling::*[1]')).to_contain_text('Choose a voice')
            assert not posts and gets==[1] and not page.errors
        finally:browser.close()


def test_unsigned_audio_prices_start_open_and_are_the_named_next_step(voice_world,monkeypatch):
    import yaml,hashlib
    root,posts,gets=voice_world;path=root/'project.yaml'
    data=yaml.safe_load(path.read_text());data.pop('audio_price_basis');raw=yaml.safe_dump(data).encode();path.write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            summary=page.get_by_text('Audio account consent and price ceilings',exact=True)
            expect(summary.locator('..')).to_have_js_property('open',True)
            expect(page.get_by_role('region',name='Trailer audio',exact=True).get_by_label('Dollar replacement value per credit',exact=True)).to_have_attribute('required','')
            expect(page.get_by_role('region',name='Next step',exact=True)).to_contain_text('Audio account consent and price ceilings')
            summary.click();expect(summary.locator('..')).to_have_js_property('open',False)
            page.get_by_role('region',name='Next step',exact=True).get_by_role('button',name='Go to this step',exact=True).click()
            expect(summary.locator('..')).to_have_js_property('open',True)
            assert page.get_by_label('Dollar replacement value per credit',exact=True).evaluate('(e)=>e===document.activeElement')
            assert not posts and not gets
        finally:browser.close()


def test_chosen_voice_survives_a_reload_without_preserving_permission(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);audio=page.get_by_role('region',name='Trailer audio',exact=True)
            audio.get_by_role('button',name='Load account voices',exact=True).click()
            choice=audio.get_by_label('Voice for line 1',exact=True);choice.select_option('invented_voice')
            audio.get_by_label('I have permission to use this voice.').check();page.reload()
            expect(choice).to_have_value('invented_voice')
            expect(audio.get_by_label('I have permission to use this voice.')).not_to_be_checked()
            assert not posts and gets==[1]
        finally:browser.close()


def test_changed_audio_snapshot_preserves_focus_selection_scroll_and_open_panels(voice_world,monkeypatch):
    from tests.backlot.test_voice_panel_browser import open_prices
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);audio=page.get_by_role('region',name='Trailer audio',exact=True)
            open_prices(audio)
            notes=audio.get_by_label('Pronunciation and delivery notes for line 1',exact=True)
            notes.fill('Keep these unfinished delivery notes.');notes.focus();notes.evaluate('(e)=>e.setSelectionRange(5,10)')
            scroll=page.locator('#viewer').evaluate('(e)=>e.scrollTop')
            def changed(route):
                value=route.fetch().json();value['record_warnings']=['Invented updated account information.'];route.fulfill(json=value)
            page.route('**/voice',changed)
            page.evaluate("id=>import('/ui/voice.js').then(m=>m.renderVoice({project_id:id},()=>{}))",root.name)
            expect(audio).to_contain_text('Invented updated account information.')
            expect(notes).to_have_value('Keep these unfinished delivery notes.')
            assert notes.evaluate('(e)=>e===document.activeElement'),'The refresh stole focus from the unfinished note.'
            assert notes.evaluate('(e)=>[e.selectionStart,e.selectionEnd]')==[5,10]
            assert abs(page.locator('#viewer').evaluate('(e)=>e.scrollTop')-scroll)<=3
            expect(audio.get_by_text('Audio account consent and price ceilings',exact=True).locator('..')).to_have_js_property('open',True)
            assert not posts and not gets and not page.errors
        finally:
            page.unroute_all(behavior='ignoreErrors');browser.close()


def test_workspace_navigation_survives_focus_refresh_and_stages_are_a_record(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            workspace=page.get_by_role('button',name='Paper cut workspace',exact=True);workspace.focus()
            page.evaluate('()=>window.originalWorkspace=document.activeElement')
            with page.expect_response(url+'/api/project/'+root.name+'/state'):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(250)
            assert workspace.evaluate('(e)=>e===window.originalWorkspace&&e===document.activeElement')
            stages=page.get_by_role('navigation',name='Stage sign-offs',exact=True)
            expect(stages.get_by_role('button')).to_have_count(0)
            expect(stages).not_to_contain_text('Not available yet')
            assert not posts and not gets and not page.errors
        finally:browser.close()
