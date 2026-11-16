"""Visible results survive unrelated reads; recorded state changes together."""
import copy

import pytest
from playwright.sync_api import sync_playwright, expect

from tests.backlot.test_voice_panel_browser import board, short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced, http
from tests.backlot.test_delivery_actions import setup_delivery, snapshot


@pytest.mark.parametrize('kind', ['music', 'effect'])
def test_new_brief_result_wins_over_a_held_signing_return_read(voice_world, monkeypatch, kind):
    root, posts, gets = voice_world
    held = []
    stalled = {'value': True}
    with board(root, monkeypatch) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            audio = page.get_by_role('region', name='Trailer audio', exact=True)
            form = audio.locator('#' + kind + '-compose')
            field = form.get_by_label('Music brief' if kind == 'music' else 'Effect description', exact=True)
            expect(field).to_be_visible()
            field.fill('An invented quiet pulse.')
            form.get_by_label('Music length in seconds' if kind == 'music' else 'Effect length in seconds', exact=True).fill('3' if kind == 'music' else '1')
            # Return from signing starts a read, but the writer saves the
            # next brief before that older read has answered.
            def signing_read(route):
                if stalled['value']:
                    held.append(route)
                else:
                    data = route.fetch().json()
                    data['record_warnings'] = ['The signing return finished.']
                    route.fulfill(json=data)
            page.route('**/voice', signing_read)
            page.evaluate('()=>import("/ui/voice.js").then(m=>m.refreshAudioAfterSigning())')
            expect(audio.get_by_role('status')).to_contain_text('Checking the saved audio decisions')
            page.wait_for_function('()=>true')
            assert held
            save = form.get_by_role('button', name='Save ' + kind + ' brief', exact=True)
            save.click()
            expect(audio.get_by_role('status')).to_contain_text('brief saved')
            stalled['value'] = False
            with page.expect_response('**/voice'):
                held.pop().continue_()
            # Wait for the follow-up read which is required because saving
            # invalidated the first response. Neither read may erase the
            # newer action's result.
            expect(form.get_by_role('button', name='Prepare ' + kind + ' spending card', exact=True)).to_be_enabled()
            expect(audio).to_contain_text('The signing return finished.')
            expect(audio.get_by_role('status')).to_contain_text('brief saved')
            expect(save.locator('xpath=following-sibling::*[1]')).to_contain_text('brief saved')
            assert not posts and not gets
        finally:
            page.unroute_all(behavior='ignoreErrors')
            browser.close()


@pytest.mark.parametrize('kind', ['music', 'effect'])
def test_brief_saved_result_survives_unrelated_read_and_clears_for_changed_brief(voice_world, monkeypatch, kind):
    from backlot import music_panel
    root, posts, gets = voice_world
    with board(root, monkeypatch) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            audio = page.get_by_role('region', name='Trailer audio', exact=True)
            form = audio.locator('#' + kind + '-compose')
            form.get_by_label('Music brief' if kind == 'music' else 'Effect description', exact=True).fill('An invented quiet pulse.')
            form.get_by_label('Music length in seconds' if kind == 'music' else 'Effect length in seconds', exact=True).fill('3' if kind == 'music' else '1')
            save = form.get_by_role('button', name='Save ' + kind + ' brief', exact=True)
            save.click()
            expect(audio.get_by_role('status')).to_contain_text('brief saved')
            marker = {'value': 'An unrelated new observation.'}
            def changed(route):
                data = route.fetch().json()
                data['record_warnings'] = [marker['value']]
                route.fulfill(json=data)
            page.route('**/voice', changed)
            def refresh():
                page.evaluate('id=>import("/ui/voice.js").then(m=>m.renderVoice({project_id:id},()=>{}))', root.name)
                expect(audio).to_contain_text(marker['value'])
            refresh()
            expect(audio.get_by_role('status')).to_contain_text('brief saved')
            expect(save.locator('xpath=following-sibling::*[1]')).to_contain_text('brief saved')
            body = {'text': 'A different invented pulse.', 'seconds': 3 if kind == 'music' else 1, 'brief_id': None}
            if kind == 'effect':
                body.update(loop=False, start_frame=0, shot_index=0)
            (music_panel.save_brief if kind == 'music' else music_panel.save_effect)(root, body)
            marker['value'] = 'The saved brief really changed.'
            refresh()
            expect(audio.get_by_role('status')).not_to_contain_text('brief saved')
            assert not posts and not gets
        finally:
            browser.close()


def test_recording_has_immediate_card_feedback_and_commits_the_highlight_with_its_result(voice_world, monkeypatch):
    root, posts, gets, served = setup_delivery(voice_world, monkeypatch)
    served['value'] = snapshot('Signed by you', colour='Needs your signature')
    held = []
    with board(root, monkeypatch) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            delivery = page.get_by_role('region', name='Trailer delivery', exact=True)
            control = delivery.get_by_role('button', name='Record the signed rough-cut approval', exact=True)
            expect(control).to_be_enabled()
            control.evaluate('(b)=>b.dataset.testRecording="rough"')
            control = delivery.locator('[data-test-recording="rough"]')
            page.route('**/delivery/rough/apply', lambda route: held.append(route))
            control.click()
            page.wait_for_function('()=>true')
            expect(control).to_have_text('Recording your signed rough-cut approval…', timeout=1000)
            expect(delivery.locator('[data-delivery-stage="edit"]').get_by_role('note')).to_contain_text('Recording your signed rough-cut approval')
            assert delivery.locator('fieldset').evaluate('(e)=>e.disabled')
            page.evaluate('''()=>{window.badDeliveryFrames=[];new MutationObserver(()=>{
              const p=document.querySelector('[aria-label="Trailer delivery"]');
              if(p.querySelector('[role="status"]')?.textContent.includes('is recorded')&&
                p.querySelector('[aria-label="Next delivery step"]')?.textContent.includes('Record the signed rough-cut approval'))window.badDeliveryFrames.push(p.innerText);
            }).observe(document.body,{subtree:true,childList:true,characterData:true});}''')
            newer = copy.deepcopy(served['value'])
            newer['recorded_stages'] = ['canon_ingest', 'script', 'edit']
            served['completed'].add('edit')
            while not held:
                page.wait_for_timeout(10)
            held[0].fulfill(json={'message': 'Your signed rough-cut approval is recorded.', 'delivery': newer})
            expect(delivery.get_by_label('Next delivery step')).to_have_text('Next: Review and sign colour and sound.', timeout=5000)
            expect(delivery.get_by_role('status')).to_contain_text('is recorded')
            assert page.evaluate('window.badDeliveryFrames') == []
            assert not posts and not gets
        finally:
            page.unroute_all(behavior='ignoreErrors')
            browser.close()


def test_export_work_and_refusal_are_beside_its_button(voice_world,monkeypatch):
    root,posts,gets,served=setup_delivery(voice_world,monkeypatch)
    served['value']=snapshot('Signed by you',colour='Signed by you')
    served['completed']={'edit','colour_mix'}
    held=[]
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':735,'height':900})
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            card=panel.locator('[data-delivery-stage="export"]')
            page.route('**/delivery/export',lambda route:held.append(route))
            card.get_by_role('button',name='Make the final file',exact=True).click()
            expect(card.get_by_role('note')).to_contain_text('Making and measuring',timeout=1000)
            expect(card.get_by_role('button')).to_have_attribute('aria-busy','true')
            while not held:page.wait_for_timeout(10)
            held[0].fulfill(status=400,json={'detail':'This saved cut changed. Review and sign the current cut before exporting.'})
            expect(card.get_by_role('note')).to_contain_text('This saved cut changed.')
            assert not posts and not gets
        finally:
            page.unroute_all(behavior='ignoreErrors');browser.close()
