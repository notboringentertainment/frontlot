"""The visible first purchase, on a child started with the Mac icon's PATH."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import short_signing_folder
from tests.backlot.test_early_paid_readiness import set_prices
from tests.backlot.signing_outcome_helpers import figures


@contextmanager
def icon_board(tmp_path,failed_filing=False):
    parent=Path(os.environ.get('OPENMONTAGE_METADATA_PATH') or (Path(tempfile.gettempdir())/'metadata.json')).resolve().parent
    projects=tmp_path/'empty-library';projects.mkdir();counter=tmp_path/'counted-services.jsonl'
    env={k:v for k,v in os.environ.items() if not k.upper().endswith(('_KEY','_TOKEN'))}
    defaults={'HOME':tmp_path/'home','OPENMONTAGE_HOME':tmp_path/'app','OPENMONTAGE_GATES_DIR':tmp_path/'gates',
        'OPENMONTAGE_METADATA_PATH':tmp_path/'metadata.json','TMPDIR':tmp_path/'runtime-tmp'}
    for name,path in defaults.items():
        env.setdefault(name,str(path));(Path(env[name]).parent if name=='OPENMONTAGE_METADATA_PATH' else Path(env[name])).mkdir(parents=True,exist_ok=True)
    for name in ('OPENMONTAGE_FFPROBE','OPENMONTAGE_FFMPEG','OPENMONTAGE_PS','OPENMONTAGE_NPX'):env.pop(name,None)
    env['PATH']='/usr/bin:/bin:/usr/sbin:/sbin'
    env['FIRST_HANDS_FAIL_LOCAL_FILING']='1' if failed_filing else '0'
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    assert port!=4750
    processes=[]
    def start(number):
        ready=tmp_path/f'icon-ready-{number}.json';log=(tmp_path/f'icon-board-{number}.log').open('x')
        process=subprocess.Popen([sys.executable,'-m','tests.backlot.first_hands_board_child',str(parent),str(projects),str(ready),str(counter),str(port)],
            env=env,cwd=Path(__file__).resolve().parents[2],stdout=log,stderr=subprocess.STDOUT)
        processes.append((process,log));deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            assert process.poll() is None,'The isolated icon board stopped: '+(tmp_path/f'icon-board-{number}.log').read_text()
            if ready.exists():
                value=json.loads(ready.read_text())
                try:
                    with urllib.request.urlopen(value['url']+'api/health',timeout=.3) as response:
                        if response.status==200:return value
                except OSError:pass
            time.sleep(.05)
        raise AssertionError('The isolated icon board did not start.')
    def stop():
        process,log=processes[-1]
        if process.poll() is None:process.terminate()
        process.wait(timeout=40);log.close()
    try:yield projects,counter,start,stop
    finally:
        for process,log in processes:
            if process.poll() is None:process.terminate()
            process.wait(timeout=40)
            if not log.closed:log.close()


@pytest.mark.parametrize('failed_filing',[False,True],ids=['first-take','signed-account-recovery'])
def test_empty_library_reaches_a_kept_speech_take_under_icon_path(tmp_path,failed_filing):
    pictures=os.environ.get('FIRST_HANDS_PICTURES');prefix=os.environ.get('FIRST_HANDS_PREFIX','first-hands')
    if failed_filing:prefix+='-recovery'
    def capture(page,name,target=None):
        if not pictures:return
        folder=Path(pictures);folder.mkdir(parents=True,exist_ok=True)
        if target is not None:target.evaluate('(e)=>e.scrollIntoView({block:"center",behavior:"instant"})')
        path=folder/(prefix+'-'+name+'.png');assert not path.exists();page.screenshot(path=str(path))
    def sign(page,back):
        page.get_by_role('button',name='Start signing',exact=True).click()
        expect(page.get_by_role('button',name='Approve and sign',exact=True)).to_be_visible(timeout=20000)
        status=page.locator('#gate-terminal-state').bounding_box();history=page.locator('#history > summary').bounding_box()
        assert status and history and (status['y']>=history['y']+history['height'] or history['y']>=status['y']+status['height']), 'The signing status overlaps History.'
        if page.get_by_role('heading',name='Keep this take',exact=True).count():capture(page,'10a-signing-question',page.locator('#gate-sign-panel'))
        page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
        expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded.',timeout=20000)
        page.get_by_role('button',name=back,exact=True).click()
    with icon_board(tmp_path,failed_filing) as (projects,counter,start,stop),sync_playwright() as pw:
        ready=start(1);browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(ready['url']) else r.abort())
            page.goto(ready['url']+'#k='+ready['token']);page.get_by_text('Start a trailer',exact=True).click()
            title='Invented first hands gallery';page.get_by_label('Trailer name',exact=True).fill(title)
            page.get_by_label('Target length in seconds',exact=True).fill('28');page.get_by_label('Total spending ceiling in dollars',exact=True).fill('10')
            capture(page,'01-create',page.get_by_role('button',name='Create trailer and review signing',exact=True))
            page.get_by_role('button',name='Create trailer and review signing',exact=True).click()
            page.get_by_role('button').filter(has=page.locator('span.what',has_text='Start this trailer')).click()
            page.locator('#gate-detail .gate-primary').click();page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            story=page.get_by_role('region',name='Trailer story',exact=True);area=story.get_by_label('Exact story text',exact=True)
            area.fill('An invented empty gallery waits.\nThe gallery is closed.\nA light stays on.');area.focus()
            other=browser.new_page();other.goto('about:blank');other.bring_to_front();page.bring_to_front()
            expect(area).to_have_value('An invented empty gallery waits.\nThe gallery is closed.\nA light stays on.')
            story.get_by_role('button',name='Save story for review',exact=True).click()
            expect(story.get_by_role('button',name='Review and sign this story',exact=True)).to_be_enabled(timeout=15000)
            capture(page,'02-story',area);story.get_by_role('button',name='Review and sign this story',exact=True).click()
            capture(page,'03-story-signing',page.get_by_role('region',name='Story approval',exact=True));sign(page,'Return to story')
            page.reload();audio=page.get_by_role('region',name='Trailer audio',exact=True)
            capture(page,'00-next-step',page.get_by_role('region',name='Next step',exact=True))
            expect(audio.get_by_text('Audio account consent and price ceilings',exact=True).locator('..')).to_have_js_property('open',True,timeout=15000)
            capture(page,'04-prices-open',audio.get_by_text('Audio account consent and price ceilings',exact=True))
            def filled_prices():
                capture(page,'04b-price-figures',audio.get_by_label('Highest music credits per minute',exact=True))
                capture(page,'04c-price-expiry',audio.get_by_label('Prices expire',exact=True))
            set_prices(page,audio,'icon',review=lambda:capture(page,'05-price-signing',page.get_by_role('heading',name='Review and sign audio',exact=True)),filled=filled_prices)
            page.get_by_role('button',name='Paper cut workspace',exact=True).click();paper=page.get_by_role('region',name='Trailer paper cut',exact=True)
            for label,text in [('Hook','A light waits.'),('Escalation','The gallery is closing.'),('Reversal','The light stays on.'),('Final invitation','Come inside.')]:paper.get_by_label(label,exact=True).fill(text)
            paper.get_by_role('button',name='Add spoken line',exact=True).click();line=paper.get_by_role('group',name='Line 1',exact=True)
            line.get_by_label('Exact wording',exact=True).fill('The gallery is closed.');line.get_by_label('Screen time in seconds',exact=True).fill('3');line.get_by_label('Story material',exact=True).select_option(index=1)
            paper.get_by_role('button',name='Add shot',exact=True).click();shot=paper.get_by_role('group',name='Shot 1',exact=True)
            shot.get_by_label('Purpose (private plan)',exact=True).fill('A steady view of the invented empty gallery.');shot.get_by_label('Screen time in seconds',exact=True).fill('28')
            expect(shot.get_by_label('Difficulty (optional planning note)',exact=True)).to_have_value('Not assessed')
            capture(page,'05a-required-shot-and-seconds',shot)
            paper.get_by_role('button',name='Save a new version',exact=True).click();expect(paper.get_by_role('status').first).to_contain_text('Saved.')
            paper.get_by_role('button',name='Check paper cut against approved story',exact=True).click();expect(paper.get_by_role('status').first).to_contain_text('compared')
            capture(page,'05-checked-paper',paper.get_by_role('button',name='Check paper cut against approved story',exact=True))
            page.get_by_role('button',name='Voice workspace',exact=True).click();audio.get_by_role('button',name='Load account voices',exact=True).click()
            voice=audio.get_by_label('Voice for line 1',exact=True);voice.select_option('invented_voice');page.reload()
            expect(voice).to_have_value('invented_voice',timeout=15000);audio.get_by_label('I have permission to use this voice.').check()
            capture(page,'06-voice',audio.locator('[data-voice-line="0"]'));audio.get_by_role('button',name='Prepare a spending card',exact=True).click()
            card=audio.locator('.voice-spend');expect(card).to_contain_text('$0.0088');capture(page,'07-spending-card',card)
            assert not counter.exists() or sum(json.loads(x)['kind']=='speech' for x in counter.read_text().splitlines())==0
            card.get_by_role('button',name='Review and sign this request',exact=True).click();capture(page,'08-purchase-signing',page.get_by_role('heading',name='Review and sign audio',exact=True));sign(page,'Return to audio')
            audio.get_by_role('button',name='Make this approved take',exact=True).click()
            if failed_filing:
                expect(audio).to_contain_text('Review an unresolved audio charge',timeout=15000)
                root=next(p for p in projects.iterdir() if (p/'project.json').exists())
                assert figures(root,10,fresh=True)==pytest.approx([0,.0088,9.9912])
                capture(page,'08a-held-charge',audio.get_by_label('The account was charged.',exact=True))
                page.reload()
                audio.get_by_label('The account was charged.',exact=True).check()
                audio.get_by_label('Actual credit replacement dollars',exact=True).fill('0.0038')
                audio.get_by_label('What I checked in the account',exact=True).fill('The invented account confirms nineteen credits and no new cash.')
                button=audio.get_by_role('button',name='Review and sign this account outcome',exact=True)
                button.scroll_into_view_if_needed();box=button.bounding_box()
                page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down()
                with page.expect_response(ready['url']+'api/project/'+root.name+'/state'):
                    page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
                page.wait_for_timeout(400);page.mouse.up()
                expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_visible(timeout=10000)
                capture(page,'08b-account-signing',page.get_by_role('heading',name='Review and sign audio',exact=True));sign(page,'Return to audio')
                expect(audio.locator('.voice-budget')).to_contain_text('$0.0038 spent',timeout=15000)
                assert figures(root,10,fresh=True)==pytest.approx([.0038,0,9.9962])
                assert sum(json.loads(x)['kind']=='speech' for x in counter.read_text().splitlines())==1
                audio.get_by_label('I have permission to use this voice.').check()
                audio.get_by_role('button',name='Prepare a spending card',exact=True).click()
                waiting=audio.locator('.voice-spend').filter(has=page.get_by_role('heading',name='Waiting for your signature',exact=True))
                waiting.get_by_role('button',name='Review and sign this request',exact=True).click();sign(page,'Return to audio')
                audio.get_by_role('button',name='Make this approved take',exact=True).click()
            take=audio.locator('.voice-take');expect(take).to_be_visible(timeout=15000)
            player=take.locator('audio');expect(player).to_have_js_property('readyState',4,timeout=15000);player.evaluate('(e)=>e.play()');expect(player).to_have_js_property('ended',True,timeout=10000)
            capture(page,'09-take-arrived',take);take.get_by_role('button',name='Keep this take',exact=True).click();capture(page,'10-keep-signing',page.get_by_role('heading',name='Review and sign audio',exact=True));sign(page,'Return to audio')
            expect(take).to_contain_text('Kept',timeout=15000);capture(page,'11-kept',take)
            root=next(p for p in projects.iterdir() if (p/'project.json').exists())
            assert figures(root,10,fresh=True)==pytest.approx([.004,0,9.996] if failed_filing else [.0002,0,9.9998])
            stop();ready=start(2)
            page.reload();banner=page.get_by_role('alert').filter(has_text='Open Front Lot from its app icon');expect(banner).to_be_visible(timeout=15000)
            capture(page,'12-stale-window',banner)
            page.goto(ready['url']+'#k='+ready['token']);page.get_by_text(title,exact=True).click()
            expect(page.locator('#writer-warning')).to_have_count(0);expect(audio.locator('.voice-take')).to_contain_text('Kept',timeout=15000)
            capture(page,'13-reopened',audio.locator('.voice-budget'))
            counts=[json.loads(x)['kind'] for x in counter.read_text().splitlines()]
            assert counts.count('speech')==(2 if failed_filing else 1) and counts.count('catalogue')==1 and set(counts)=={'speech','catalogue'}
            assert not (root/'checkpoint_assets.json').exists() and not errors
            preview=os.environ.get('FIRST_HANDS_PREVIEW')
            if preview and not failed_filing:
                path=Path(preview);parent=Path(os.environ.get('OPENMONTAGE_METADATA_PATH') or (Path(tempfile.gettempdir())/'metadata.json')).resolve().parent;assert path.resolve().is_relative_to(parent)
                with path.open('x') as stream:json.dump({'url':ready['url']+'p/'+root.name+'?static=1#k='+ready['token'],'release':str(path)+'.release'},stream)
                deadline=time.monotonic()+180
                while not Path(str(path)+'.release').exists() and time.monotonic()<deadline:page.wait_for_timeout(100)
            other.close()
        finally:browser.close()
