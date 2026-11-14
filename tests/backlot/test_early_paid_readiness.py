"""Readiness investigation: distinguish the empty-screen stop from prepared proofs.

Every production is invented. Prices below are repository test assumptions,
never a claim about a real account. Only visible browser controls buy takes.
"""
import io
import json
import os
import threading
import time
import uuid
import wave
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect

PARENT = Path(os.environ['TMPDIR']).resolve().parent
RUN = os.environ.get('EARLY_PICTURE_PREFIX',uuid.uuid4().hex)
LINE = 'The gallery is closed.'


@pytest.fixture(autouse=True)
def isolated_standins(monkeypatch):
    # No credential file is opened; the checkout has no dotenv. Stop rather
    # than accidentally loading a future one when a tool module is imported.
    assert not (Path(__file__).resolve().parents[2]/'.env').exists()
    for name in ('ELEVENLABS_API_KEY', 'FAL_KEY', 'FAL_AI_API_KEY'):
        monkeypatch.setenv(name, 'invented-readiness-only')
    folder=PARENT/('g'+uuid.uuid4().hex[:6]);folder.mkdir()
    monkeypatch.setenv('OPENMONTAGE_GATES_DIR',str(folder))
    monkeypatch.setenv('FRONTLOT_CLAUDE','/nonexistent/readiness-no-assistant')
    import requests
    calls={'catalogues':[], 'audio_posts':[], 'uploads':[], 'video_posts':[], 'waits':[], 'downloads':[]}
    def get(url, **kw):
        assert url=='https://api.elevenlabs.io/v1/voices'
        calls['catalogues'].append(url)
        class R:
            def raise_for_status(self): pass
            def json(self): return {'voices':[{'voice_id':'invented_voice','name':'Invented gallery narrator'}]}
        return R()
    def post(url, **kw):
        assert url in ('https://api.elevenlabs.io/v1/text-to-speech/invented_voice',
                       'https://api.elevenlabs.io/v1/music', 'https://api.elevenlabs.io/v1/sound-generation')
        calls['audio_posts'].append({'url':url,'body':kw['json']})
        seconds=2 if url.endswith('sound-generation') else 12 if url.endswith('/music') else 1
        b=io.BytesIO()
        with wave.open(b,'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000)
            f.writeframes(b'\0\0'*(8000*seconds-1)+bytes([len(calls['audio_posts']),0]))
        class R:
            status_code=200
            headers={'character-cost':'1','request-id':'invented-readiness-'+str(len(calls['audio_posts']))}
            content=b.getvalue()
            def raise_for_status(self): pass
        return R()
    monkeypatch.setattr(requests,'get',get);monkeypatch.setattr(requests,'post',post)
    from tools.video import _shared
    from tests.tools.test_signed_footage import clip_bytes
    def upload(path):
        calls['uploads'].append(Path(path).name);return 'https://simulation.invalid/ref.png'
    def submit(model,payload,**kw):
        calls['video_posts'].append({'model':model,'payload':payload});return {'request_id':'invented-readiness-video'}
    def wait(*args,**kw):
        calls['waits'].append(1);return {'video':{'url':'https://simulation.invalid/take.mp4'},'cash_usd':.56,'credits_usd':0}
    def download(url,dest,**kw):
        calls['downloads'].append(1);Path(dest).write_bytes(clip_bytes())
    for name,fn in [('upload_image_fal',upload),('fal_queue_submit',submit),('fal_queue_wait',wait),('fal_download',download)]:
        monkeypatch.setattr(_shared,name,fn)
    yield calls


def shot_picture(page,name,target=None):
    folder=os.environ.get('EARLY_TEST_PICTURES')
    if not folder:return
    if target is not None: target.evaluate('(e)=>e.scrollIntoView({block:"center",behavior:"instant"})')
    path=Path(folder)/(RUN+'-'+name+'.png');assert not path.exists()
    page.screenshot(path=str(path),full_page=False)


def start_browser(pw,url):
    browser=pw.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.errors=[];page.on('pageerror',lambda e:page.errors.append(str(e)))
    page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url.split('/p/')[0]+'/') else r.abort())
    return browser,page


def sign_audio(page):
    page.get_by_role('button',name='Start signing',exact=True).click()
    page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
    expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded.',timeout=20000)
    page.get_by_role('button',name='Return to audio',exact=True).click()


def set_prices(page,panel,prefix):
    panel.get_by_text('Audio account consent and price ceilings',exact=True).click()
    for label,value in [('Dollar replacement value per credit','.0002'),('Highest speech credits per character','1'),
                        ('Highest music credits per minute','1200'),('Highest voice rate multiplier','2'),
                        ('Highest sound-effect credits per second','40'),('Prices expire','2026-11-15T12:00')]:
        panel.get_by_label(label,exact=True).fill(value)
    panel.get_by_role('button',name='Prepare price settings for signing',exact=True).click()
    expect(page.get_by_role('heading',name='Review and sign audio',exact=True)).to_be_visible(timeout=20000)
    expect(page.get_by_role('heading',name='Review and sign audio',exact=True).locator('..')).to_contain_text('Whole trailer ceiling: $10.0000')
    shot_picture(page,prefix+'-audio-settings-signing',page.get_by_role('heading',name='Review and sign audio',exact=True))
    sign_audio(page)


def test_fresh_trailer_audio_panel_can_offer_its_existing_settings_operation(tmp_path,monkeypatch,isolated_standins):
    from lib.trailer_edit import create_trailer
    from tests.lib.look_lock_helpers import approve_request
    from backlot.voice_panel import snapshot
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',tmp_path)
    root=create_trailer(tmp_path,'Invented empty settings check',28,10)
    approve_request(json.loads(next((root/'.gate-requests').glob('*.json')).read_text()),root)
    assert snapshot(root)['budget']['ceiling']==10


def save_paper_with_controls(page,reference=''):
    page.get_by_role('button',name='Paper cut workspace',exact=True).click()
    paper=page.get_by_role('region',name='Trailer paper cut',exact=True)
    for label,value in [('Hook','A light burns in the empty gallery.'),('Escalation','Someone is coming.'),
                        ('Reversal','The door is already open.'),('Final invitation','Come inside.')]:
        paper.get_by_label(label,exact=True).fill(value)
    paper.get_by_role('button',name='Add spoken line',exact=True).click()
    line=paper.get_by_role('group',name='Line 1',exact=True)
    line.get_by_label('Exact wording',exact=True).fill(LINE)
    line.get_by_label('Screen time in frames',exact=True).fill('72')
    paper.get_by_role('button',name='Add shot',exact=True).click()
    shot=paper.get_by_role('group',name='Shot 1',exact=True)
    shot.get_by_label('Purpose (private plan)',exact=True).fill('The gallery keeper looks towards a doorway.')
    shot.get_by_label('Screen time in frames',exact=True).fill('120')
    shot.get_by_label('References, separated by commas',exact=True).fill(reference)
    shot.get_by_label('Difficulty',exact=True).fill('One simple action.')
    paper.get_by_role('button',name='Add shot',exact=True).click()
    remainder=paper.get_by_role('group',name='Shot 2',exact=True)
    remainder.get_by_label('Purpose (private plan)',exact=True).fill('Leave time for the closing invitation.')
    remainder.get_by_label('Screen time in frames',exact=True).fill('552')
    remainder.get_by_label('Difficulty',exact=True).fill('Unfinished placeholder.')
    paper.get_by_role('button',name='Save a new version',exact=True).click()
    expect(paper.get_by_role('status').first).to_contain_text('Saved.')
    return paper


def buy_three_audio(page,panel,calls,prefix):
    panel.get_by_role('button',name='Load account voices',exact=True).click()
    line=panel.locator('[data-voice-line="0"]')
    line.get_by_label('Voice for line 1',exact=True).select_option('invented_voice')
    line.get_by_label('I have permission to use this voice.').check()
    line.get_by_role('button',name='Prepare a spending card',exact=True).click()
    for kind,maximum in [('speech','$0.0088'),('music','$0.0480'),('effect','$0.0160')]:
        if kind!='speech':
            form=panel.locator('#'+kind+'-compose')
            form.get_by_label('Music brief' if kind=='music' else 'Effect description',exact=True).fill(
                'A quiet instrumental pulse with room for speech.' if kind=='music' else 'A soft gallery door latch clicks.')
            form.get_by_label('Music length in seconds' if kind=='music' else 'Effect length in seconds',exact=True).fill('12' if kind=='music' else '2')
            form.get_by_role('button',name='Save '+kind+' brief',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('brief saved')
            form.get_by_role('button',name='Prepare '+kind+' spending card',exact=True).click()
        card=panel.locator('.voice-spend[data-kind="'+kind+'"]')
        expect(card).to_contain_text(maximum)
        before=len(calls['audio_posts'])
        card.get_by_role('button',name='Review and sign this request',exact=True).click()
        expect(page.get_by_role('heading',name='Review and sign audio',exact=True)).to_be_visible(timeout=20000)
        shot_picture(page,prefix+'-'+kind+'-purchase-signing',page.get_by_role('heading',name='Review and sign audio',exact=True))
        assert len(calls['audio_posts'])==before
        sign_audio(page)
        assert len(calls['audio_posts'])==before
        card.get_by_role('button',name='Make this approved take',exact=True).click()
        tile=panel.locator('.voice-take[data-kind="'+kind+'"]')
        expect(tile).to_be_visible(timeout=20000)
        player=tile.locator('audio');expect(player).to_have_js_property('readyState',4,timeout=15000)
        player.evaluate('(p)=>{p.muted=true;return p.play()}');player.evaluate('(p)=>p.pause()')
        tile.get_by_role('button',name='Keep this take',exact=True).click();sign_audio(page)
        expect(tile).to_contain_text('Kept',timeout=20000)
        shot_picture(page,prefix+'-'+kind+'-kept',tile)
        assert len(calls['audio_posts'])==before+1


def test_empty_production_signs_story_checks_paper_and_keeps_three_audio_purchases(tmp_path,monkeypatch,isolated_standins):
    from tests.backlot.test_footage_controls_browser import browser_board
    projects=tmp_path/'empty-projects';projects.mkdir()
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',projects)
    monkeypatch.setattr('lib.events.PROJECTS_DIR',projects)
    with browser_board(projects/'not-created',monkeypatch) as board_url,sync_playwright() as pw:
        base=board_url.split('/p/')[0]
        browser,page=start_browser(pw,board_url)
        try:
            page.goto(base+'/#k=invented-browser-writer')
            page.get_by_text('Start a trailer',exact=True).click()
            page.get_by_label('Trailer name',exact=True).fill('Invented ten dollar screen test')
            page.get_by_label('Target length in seconds',exact=True).fill('28')
            page.get_by_label('Total spending ceiling in dollars',exact=True).fill('10')
            shot_picture(page,'empty-creation',page.get_by_role('button',name='Create trailer and review signing'))
            page.get_by_role('button',name='Create trailer and review signing',exact=True).click()
            page.get_by_role('button').filter(has=page.locator('span.what',has_text='Start this trailer')).click()
            page.locator('#gate-detail .gate-primary').click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            page.get_by_role('button',name='Voice workspace',exact=True).click(timeout=20000)
            root=next(p for p in projects.iterdir() if (p/'project.json').exists())
            panel=page.get_by_role('region',name='Trailer audio')
            expect(panel.locator('.voice-budget')).to_contain_text('$10.0000 remaining of $10.0000')
            set_prices(page,panel,'empty')
            paper=save_paper_with_controls(page)
            expect(paper).to_contain_text('This draft is not yet checked against the approved story.')
            shot_picture(page,'empty-unchecked-story',paper.get_by_role('button',name='Save a new version'))
            page.get_by_role('button',name='Voice workspace',exact=True).click()
            for label in ('Prepare a spending card','Prepare music spending card','Prepare effect spending card'):
                expect(panel.get_by_role('button',name=label,exact=True)).to_be_disabled()
            shot_picture(page,'empty-audio-blocked',panel.locator('[data-voice-line="0"]'))
            story=page.get_by_role('region',name='Trailer story',exact=True)
            expect(story).to_be_visible(timeout=5000)
            text='  A gallery waits.\nThe gallery is closed.\nKeep the light on.\nThe keeper is the thief.  \n'
            story.get_by_label('Exact story text',exact=True).fill(text)
            story.get_by_role('button',name='Add protected wording',exact=True).click()
            story.get_by_label('Protected wording 1',exact=True).fill('Keep the light on.')
            story.get_by_role('button',name='Add spoiler passage',exact=True).click()
            story.get_by_label('Spoiler passage 1',exact=True).fill('The keeper is the thief.')
            story.get_by_role('button',name='Save story for review',exact=True).click()
            expect(story.get_by_role('status')).to_contain_text('Saved')
            assert all(not v for v in isolated_standins.values())
            story.get_by_role('button',name='Review and sign this story',exact=True).click()
            review=page.get_by_role('region',name='Story approval',exact=True)
            expect(review).to_contain_text(text)
            assert '\nnull\n' not in review.inner_text()
            expect(page.get_by_role('button',name='Review: Review this story',exact=True)).to_be_visible()
            shot_picture(page,'exact-story-signing',review.get_by_role('heading').first)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded.',timeout=20000)
            page.get_by_role('button',name='Return to story',exact=True).click(timeout=20000)
            expect(story.get_by_role('status')).to_contain_text('signed',timeout=15000)
            for label in ('Prepare a spending card','Prepare music spending card','Prepare effect spending card'):
                expect(panel.get_by_role('button',name=label,exact=True)).to_be_disabled()
            paper.get_by_role('button',name='Check paper cut against approved story',exact=True).click()
            expect(paper.get_by_role('status').first).to_contain_text('compared')
            for label in ('Prepare a spending card','Prepare music spending card','Prepare effect spending card'):
                expect(panel.get_by_role('button',name=label,exact=True)).to_be_enabled(timeout=15000)
            shot_picture(page,'paper-compared',paper.get_by_role('button',name='Check paper cut against approved story'))
            buy_three_audio(page,panel,isolated_standins,'empty')
            page.get_by_role('button',name='Open footage requests',exact=True).click()
            footage=page.get_by_label('Footage workspace',exact=True)
            expect(footage).to_contain_text('those approval controls are not built here')
            for control in footage.get_by_role('button',name='Create footage request',exact=True).all():
                expect(control).to_be_disabled()
            shot_picture(page,'empty-footage-blocked',footage)
            assert len(isolated_standins['audio_posts'])==3 and len(isolated_standins['catalogues'])==1
            assert all(not isolated_standins[k] for k in ('uploads','video_posts','waits','downloads'))
            assert not page.errors
            evidence={'root':str(root),'calls':isolated_standins,'body':page.locator('body').inner_text(),
                      'state':__import__('backlot.state',fromlist=['load_board_state']).load_board_state(root)}
            with (PARENT/(RUN+'-empty-evidence.json')).open('x') as f:json.dump(evidence,f,default=str,indent=2)
        except Exception:
            with (tmp_path/'empty-failure.txt').open('x') as f:f.write(page.locator('body').inner_text())
            page.screenshot(path=str(tmp_path/'empty-failure.png'));raise
        finally:browser.close()


def test_prepared_prerequisites_allow_four_separately_signed_purchases(tmp_path,monkeypatch,isolated_standins):
    # This is explicitly NOT a screen-only setup. It measures what works after
    # the missing upstream controls, without disguising them as writer steps.
    from lib.trailer_edit import create_trailer
    from tests.lib import look_lock_helpers as looks
    from tests.lib.test_trailer_edit import signed_story
    from tests.backlot.test_footage_controls_browser import browser_board,sign
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',tmp_path)
    monkeypatch.setattr('lib.events.PROJECTS_DIR',tmp_path)
    root=create_trailer(tmp_path,'Invented prepared gallery',28,10)
    looks.approve_request(json.loads(next((root/'.gate-requests').glob('*.json')).read_text()),root)
    signed_story(root)
    # Existing helpers establish exactly the prerequisites the empty screen
    # cannot create. No reference is generated or imported from real work.
    import yaml,hashlib
    from tests.tools.test_signed_footage import sign as sign_settings
    from tests.tools._authored_film_helpers import write_receipted_png
    data=yaml.safe_load((root/'project.yaml').read_text())
    data['version']='1.4'
    data['default_video_endpoint']='fal-ai/kling-video/o3/pro/reference-to-video'
    data['provider_egress'].append({'provider':'fal','content_classes':['prompts','reference_images']})
    data['footage_price_basis']={'provider':'fal','model':data['default_video_endpoint'],
        'cash_usd_per_second':.112,'credits_usd_per_second':0,'set_at':time.time()-60,'expires_at':1794772800}
    raw=yaml.safe_dump(data).encode();(root/'project.yaml').write_bytes(raw)
    sign_settings(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    monkeypatch.setattr(looks,'PROJECT',root.name)
    looks.activate_look(root,looks.character_look('gallery-keeper'))
    write_receipted_png(root,'assets/keeper.png')
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url)
            save_paper_with_controls(page,'assets/keeper.png')
            page.get_by_role('button',name='Voice workspace',exact=True).click()
            panel=page.get_by_role('region',name='Trailer audio')
            set_prices(page,panel,'prepared')
            panel.get_by_role('button',name='Load account voices',exact=True).click()
            line=panel.locator('[data-voice-line="0"]')
            line.get_by_label('Voice for line 1',exact=True).select_option('invented_voice')
            line.get_by_label('I have permission to use this voice.').check()
            line.get_by_role('button',name='Prepare a spending card',exact=True).click()
            prices={}
            for kind,maximum in [('speech','$0.0088'),('music','$0.0480'),('effect','$0.0160')]:
                if kind!='speech':
                    form=panel.locator('#'+kind+'-compose')
                    form.get_by_label('Music brief' if kind=='music' else 'Effect description',exact=True).fill(
                        'A quiet instrumental pulse with room for speech.' if kind=='music' else 'A soft gallery door latch clicks.')
                    form.get_by_label('Music length in seconds' if kind=='music' else 'Effect length in seconds',exact=True).fill('12' if kind=='music' else '2')
                    form.get_by_role('button',name='Save '+kind+' brief',exact=True).click()
                    expect(panel.get_by_role('status')).to_contain_text('brief saved')
                    form.get_by_role('button',name='Prepare '+kind+' spending card',exact=True).click()
                card=panel.locator('.voice-spend[data-kind="'+kind+'"]')
                expect(card).to_contain_text(maximum)
                prices[kind]=card.inner_text()
                assert len(isolated_standins['audio_posts'])==len(prices)-1
                card.get_by_role('button',name='Review and sign this request',exact=True).click()
                expect(page.get_by_role('heading',name='Review and sign audio',exact=True)).to_be_visible(timeout=20000)
                shot_picture(page,'prepared-'+kind+'-purchase-signing',page.get_by_role('heading',name='Review and sign audio',exact=True))
                sign_audio(page)
                card.get_by_role('button',name='Make this approved take',exact=True).click()
                tile=panel.locator('.voice-take[data-kind="'+kind+'"]')
                expect(tile).to_be_visible(timeout=20000)
                player=tile.locator('audio');expect(player).to_have_js_property('readyState',4,timeout=15000)
                player.evaluate('(p)=>{p.muted=true;return p.play()}');player.evaluate('(p)=>p.pause()')
                tile.get_by_role('button',name='Keep this take',exact=True).click();sign_audio(page)
                expect(tile).to_contain_text('Kept',timeout=20000)
                shot_picture(page,'prepared-'+kind+'-kept',tile)
            # Footage is purchased last, after all three kept audio takes.
            page.get_by_role('button',name='Open footage requests',exact=True).click()
            shot=page.get_by_role('article',name='Shot 1 footage',exact=True)
            shot.get_by_label('Clip length in seconds',exact=True).fill('5')
            shot.get_by_label('Clip length in seconds',exact=True).press('Tab')
            shot.get_by_role('button',name='Refresh price',exact=True).click()
            expect(shot).to_contain_text('Highest charge: $0.5600',timeout=15000)
            with page.expect_response(lambda r:'/footage/create' in r.url,timeout=30000) as created:
                shot.get_by_role('button',name='Create footage request',exact=True).click()
            with (PARENT/('footage-create-'+os.environ.get('EARLY_PICTURE_PREFIX','probe')+'.json')).open('x') as f:
                json.dump({'status':created.value.status,'body':created.value.json()},f,indent=2)
            details=page.get_by_label('Footage request details',exact=True)
            expect(details).to_contain_text('$0.5600',timeout=20000)
            prices['footage']=details.inner_text()
            shot_picture(page,'prepared-footage-purchase-signing',details.get_by_role('heading').first)
            assert not isolated_standins['video_posts']
            sign(page,'request')
            expect(shot).to_contain_text('Take waiting for your decision',timeout=30000)
            player=shot.locator('video').first;expect(player).to_have_js_property('readyState',4,timeout=20000)
            player.evaluate('(v)=>{v.muted=true;return v.play()}')
            page.wait_for_function('()=>[...document.querySelectorAll("video")].some(v=>v.ended)',timeout=12000)
            shot.get_by_role('button',name='Keep this take',exact=True).click()
            expect(page.get_by_label('Footage request details',exact=True)).to_be_visible(timeout=20000)
            sign(page,'decision')
            expect(shot).to_contain_text('Kept',timeout=20000)
            shot_picture(page,'prepared-footage-kept',shot)
            assert len(isolated_standins['audio_posts'])==3
            assert all(len(isolated_standins[k])==1 for k in ['catalogues','uploads','video_posts','waits','downloads'])
            from tests.backlot.signing_outcome_helpers import figures
            assert figures(root,10,fresh=True)==pytest.approx([.5606,0,9.4394])
            assert not page.errors
            from backlot.state import load_board_state
            state=load_board_state(root)
            assert all(not stage['human_approved'] for stage in state['stages'] if stage['name']!='canon_ingest')
            with (PARENT/(RUN+'-prepared-evidence.json')).open('x') as f:
                json.dump({'root':str(root),'prices':prices,'calls':isolated_standins,'figures':figures(root,10),
                           'page_errors':page.errors,'setup_was_not_screen_only':True},f,indent=2)
            preview=os.environ.get('EARLY_TEST_PREVIEW')
            if preview:
                with Path(preview).open('x') as f:json.dump({'url':url},f)
                end=time.monotonic()+180
                while not Path(preview+'.done').exists():
                    assert time.monotonic()<end,'The shared preview must be closed before this test stops.'
                    threading.Event().wait(.1)
        except Exception:
            with (tmp_path/'prepared-failure.txt').open('x') as f:f.write(page.locator('body').inner_text())
            page.screenshot(path=str(tmp_path/'prepared-failure.png'));raise
        finally:browser.close()
