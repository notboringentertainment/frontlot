"""The trimmed trailer journey uses visible controls and real signatures.

Only the three unmerged footage prerequisites are supplied as labelled setup.
No stand-in supplies a stage approval. All paid-service boundaries count.
"""
import io
import json
import math
import os
import random
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path
from contextlib import contextmanager

import pytest
from playwright.sync_api import sync_playwright, expect
from tests.backlot.test_early_paid_readiness import (
    isolated_standins, start_browser, shot_picture, RUN as BASE_RUN,
    test_empty_production_signs_story_checks_paper_and_keeps_three_audio_purchases as walk_to_footage,
)
RUN=BASE_RUN+'-journey'


def _wav(seconds,kind,seed=4):
    rate=24000; samples=[]; rng=random.Random(seed)
    for n in range(int(seconds*rate)):
        t=n/rate
        value=(rng.uniform(-6000,6000) if kind=='effect' else
               4000*math.sin(2*math.pi*(173 if kind=='speech' else 211)*t))
        samples.append(struct.pack('<h',int(value)))
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes(b''.join(samples))
    return buffer.getvalue()


@pytest.mark.parametrize('reload_points',[False,True],ids=['fresh','reopen'])
def test_existing_screens_map_a_fresh_trailer(tmp_path,monkeypatch,isolated_standins,reload_points):
    # The readiness test also runs on its own in the full suite. This joined
    # rehearsal must not reuse its exclusive evidence filenames.
    run=RUN+('-reopen' if reload_points else '-fresh')
    monkeypatch.setattr(__import__(__name__,fromlist=['RUN']),'RUN',run)
    monkeypatch.setattr('tests.backlot.test_early_paid_readiness.RUN',run)
    # This invented trailer actually quotes its protected line as voice.
    # It must not pretend an unquoted protected line was heard at delivery.
    monkeypatch.setattr('tests.backlot.test_early_paid_readiness.LINE','Keep the light on.')
    calls=isolated_standins
    parent=Path(os.environ['TMPDIR']).resolve().parent
    locations=[]
    from tests.backlot import test_footage_controls_browser as browser_helpers
    original_board=browser_helpers.browser_board
    @contextmanager
    def live_board(root,patch):
        import backlot.server as server
        from lib.gates import gates_dir
        home=Path.home().resolve();signing=gates_dir().resolve();metadata=server.backlot_metadata_dir().resolve()
        assert all(path.is_relative_to(parent) for path in (home,signing,metadata))
        locations.append({'home':str(home),'signing':str(signing),'metadata':str(metadata),'project':str(root)})
        patch.setattr(server,'_PROJECTS_ROOT_STR',os.path.normcase(str(root.parent.resolve())))
        patch.setattr('backlot.state.PROJECTS_DIR',root.parent)
        with original_board(root,patch) as address:
            address=address.replace('?static=1','')
            with (parent/(RUN+f'-board-{len(locations)}.url')).open('x') as handle:handle.write(address)
            yield address
    monkeypatch.setattr(browser_helpers,'browser_board',live_board)
    calls['reveals']=[]
    original_run=subprocess.run
    def local_run(args,*values,**options):
        if args[:2]==['open','-R']:
            assert Path(args[2]).resolve().is_relative_to(root.resolve())
            calls['reveals'].append(args[2]);return subprocess.CompletedProcess(args,0)
        return original_run(args,*values,**options)
    monkeypatch.setattr(subprocess,'run',local_run)
    audio={kind:_wav(length,kind) for kind,length in [('speech',1),('music',12),('effect',2)]}
    def post(url,**kw):
        allowed={'https://api.elevenlabs.io/v1/text-to-speech/invented_voice':'speech',
                 'https://api.elevenlabs.io/v1/music':'music',
                 'https://api.elevenlabs.io/v1/sound-generation':'effect'}
        assert url in allowed
        calls['audio_posts'].append({'url':url,'body':kw['json']})
        class Reply:
            status_code=200
            headers={'character-cost':'1','request-id':'journey-audio-'+str(len(calls['audio_posts']))}
            # Distinct requested effects get distinct invented recordings.
            # Reusing identical bytes for a different brief correctly trips
            # immutable provenance rather than fabricating a second origin.
            content=_wav(2,'effect',len(calls['audio_posts'])) if allowed[url]=='effect' else audio[allowed[url]]
            def raise_for_status(self):pass
        return Reply()
    monkeypatch.setattr('requests.post',post)
    # This existing real-browser test creates and signs the trailer, signs
    # prices and story, checks the paper cut, and buys/keeps all three sounds.
    # It records the genuine first-time stop at the missing footage controls.
    walk_to_footage(tmp_path,monkeypatch,calls)
    root=next(p for p in (tmp_path/'empty-projects').iterdir() if (p/'project.json').is_file())
    evidence={'root':str(root),'steps':[],
              'labelled_setup':['A signed fixture look, an already-made receipted picture and signed footage prices stand in only for the other builder’s three unfinished controls.'],
              'calls':calls,'board_locations':locations}
    def note(name,panel):
        evidence['steps'].append({'step':name,'text':panel.inner_text()})
        with (parent/(RUN+'-progress.jsonl')).open('a') as progress:
            progress.write(json.dumps({'step':name,'at':time.time()})+'\n')
        target=panel
        if name=='cut-titles-colour-playback':target=panel.locator('video').first
        elif name=='exported-file':target=panel.locator('[data-delivery] video').first
        elif name in ('rough-signed-stage-incomplete','signed-final-and-reveal'):target=panel.get_by_role('status').first
        shot_picture(page,'journey-'+name,target)
    # No earlier stage or story approval is invented here. Creation, story,
    # price approval, all sound purchases and keep decisions used the screens.
    from tests.lib import look_lock_helpers as looks
    from tests.tools.test_signed_footage import sign as fixture_config
    from tests.tools._authored_film_helpers import write_receipted_png
    import hashlib,yaml
    config=yaml.safe_load((root/'project.yaml').read_text())
    config['version']='1.4'
    config['default_video_endpoint']='fal-ai/kling-video/o3/pro/reference-to-video'
    config['provider_egress'].append({'provider':'fal','content_classes':['prompts','reference_images']})
    config['footage_price_basis']={'provider':'fal','model':config['default_video_endpoint'],
        'cash_usd_per_second':.112,'credits_usd_per_second':0,'set_at':time.time()-60,'expires_at':time.time()+86400}
    raw=yaml.safe_dump(config).encode();(root/'project.yaml').write_bytes(raw)
    fixture_config(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    monkeypatch.setattr(looks,'PROJECT',root.name)
    looks.activate_look(root,looks.character_look('gallery-keeper'))
    write_receipted_png(root,'assets/keeper.png')
    from tests.backlot.test_footage_controls_browser import browser_board,sign
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url)
            paper=page.get_by_role('region',name='Trailer paper cut',exact=True)
            paper.get_by_role('group',name='Shot 1',exact=True).get_by_label('References, separated by commas',exact=True).fill('assets/keeper.png')
            paper.get_by_role('button',name='Save a new version',exact=True).click()
            expect(paper.get_by_role('status').first).to_contain_text('Saved.')
            paper.get_by_role('button',name='Check paper cut against approved story',exact=True).click()
            expect(paper.get_by_role('status').first).to_contain_text('compared')
            paper.get_by_role('button',name='Review and sign the whole paper cut',exact=True).click()
            whole=page.get_by_role('region',name='Whole paper-cut review',exact=True)
            expect(whole).to_contain_text('Keep the light on.',timeout=30000)
            note('whole-paper-review',whole)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=30000)
            page.get_by_role('button',name='Return to paper cut',exact=True).click()
            expect(paper.get_by_role('status',name='Whole paper-cut approval',exact=True)).to_have_text('Signed by you',timeout=30000)
            paper.get_by_role('button',name='Record the signed paper-cut review',exact=True).click()
            expect(paper.get_by_role('status').first).to_contain_text('Your signed paper-cut review is recorded.',timeout=30000)
            if reload_points:page.reload()
            # Adding the labelled reference changes the effect's exact shot
            # binding. Buy a new effect for that revised moment through the
            # same visible controls; never clear the previous take's warning.
            page.get_by_role('button',name='Sound effects workspace',exact=True).click()
            panel=page.get_by_role('region',name='Trailer audio',exact=True)
            form=panel.locator('#effect-compose')
            form.get_by_label('Effect description',exact=True).fill('A second gentle gallery door latch clicks.')
            form.get_by_label('Effect length in seconds',exact=True).fill('2')
            form.get_by_role('button',name='Save effect brief',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('brief saved')
            form.get_by_role('button',name='Prepare effect spending card',exact=True).click()
            card=panel.locator('.voice-spend[data-kind="effect"]').filter(has_text='A second gentle gallery door latch clicks.')
            card.get_by_role('button',name='Review and sign this request',exact=True).click()
            from tests.backlot.test_early_paid_readiness import sign_audio
            sign_audio(page)
            card.get_by_role('button',name='Make this approved take',exact=True).click()
            tile=panel.locator('.voice-take[data-kind="effect"]').filter(has_text='Waiting for your decision')
            expect(tile).to_be_visible(timeout=20000)
            tile.get_by_role('button',name='Keep this take',exact=True).click();sign_audio(page)
            expect(panel.locator('.voice-take[data-kind="effect"]').filter(has_text='Kept')).to_have_count(1,timeout=20000)
            page.get_by_role('button',name='Open footage requests',exact=True).click()
            shot=page.get_by_role('article',name='Shot 1 footage',exact=True)
            shot.get_by_label('Clip length in seconds',exact=True).fill('5')
            shot.get_by_label('Clip length in seconds',exact=True).press('Tab')
            shot.get_by_role('button',name='Refresh price',exact=True).click()
            expect(shot).to_contain_text('Highest charge: $0.5600',timeout=15000)
            shot.get_by_role('button',name='Create footage request',exact=True).click()
            expect(page.get_by_label('Footage request details',exact=True)).to_be_visible(timeout=20000)
            assert not calls['video_posts'];sign(page,'request')
            expect(shot).to_contain_text('Take waiting for your decision',timeout=30000)
            player=shot.locator('video').first;expect(player).to_have_js_property('readyState',4,timeout=15000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=15000)
            shot.get_by_role('button',name='Keep this take',exact=True).click();sign(page,'decision')
            expect(shot).to_contain_text('Kept',timeout=20000);note('kept-footage',shot)
            if reload_points:page.reload()
            page.get_by_role('button',name='Cutting workspace',exact=True).click()
            cut=page.get_by_role('region',name='Cutting view',exact=True)
            expect(cut.get_by_label('Kept picture take',exact=True).locator('option')).to_have_count(1,timeout=30000)
            expect(cut.get_by_label('Kept sound take',exact=True).locator('option')).to_have_count(3,timeout=30000)
            while cut.get_by_role('button',name='Remove this clip',exact=True).count():
                cut.get_by_role('button',name='Remove this clip',exact=True).first.click()
            for _ in range(3):cut.get_by_role('button',name='Add picture',exact=True).click()
            for i in range(3):
                cut.get_by_label(f'Starts at frame for picture {i+1}',exact=True).fill(str(i*24))
                cut.get_by_label(f'Source out frame for picture {i+1}',exact=True).fill('24')
            for i in range(3):
                cut.get_by_label('Kept sound take',exact=True).select_option(str(i))
                cut.get_by_role('button',name='Add sound',exact=True).click()
            for i,text in enumerate(['One lamp.','Only the light remains.','The night gallery'],1):
                cut.get_by_role('button',name='Add text card',exact=True).click()
                cut.get_by_label(f'Exact words for card {i}',exact=True).fill(text)
                cut.get_by_label(f'Starts at frame for card {i}',exact=True).fill(str([6,36,60][i-1]))
                cut.get_by_label(f'Lasts for frames for card {i}',exact=True).fill(str([6,6,12][i-1]))
                if i==3:cut.get_by_label(f'Card kind for card {i}',exact=True).select_option('end')
            with page.expect_response(lambda r:r.url.endswith('/cut') and r.request.method=='POST',timeout=30000) as saving:
                cut.get_by_role('button',name='Save cut',exact=True).click()
            assert saving.value.ok,saving.value.text()
            expect(cut.get_by_role('status').first).to_contain_text(f'Version {saving.value.json()["number"]} saved.',timeout=30000)
            for i in (1,2,3):
                cut.get_by_role('button',name=f'Review and sign card {i}',exact=True).click()
                expect(page.get_by_role('region',name='Exact card review')).to_be_visible(timeout=30000)
                page.get_by_role('button',name='Start signing',exact=True).click()
                page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
                expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=30000)
                page.get_by_role('button',name='Return to cutting view',exact=True).click()
            for _ in range(2):cut.get_by_label('Brighter or darker for picture 1',exact=True).press('ArrowRight')
            with page.expect_response(lambda r:r.url.endswith('/cut') and r.request.method=='POST',timeout=30000) as saving:
                cut.get_by_role('button',name='Save cut',exact=True).click()
            assert saving.value.ok,saving.value.text()
            expect(cut.get_by_role('status').first).to_contain_text(f'Version {saving.value.json()["number"]} saved.',timeout=30000)
            cut.get_by_role('button',name='Prepare saved playback',exact=True).click()
            expect(cut.get_by_role('status').first).to_contain_text('ready',timeout=240000)
            player=cut.locator('video');expect(player).to_have_js_property('readyState',4,timeout=20000)
            player.evaluate('''v=>{window.cutPlaybackEvents=[];for(const kind of ['play','pause','ended','emptied','loadedmetadata','seeking','seeked','error'])v.addEventListener(kind,()=>window.cutPlaybackEvents.push({kind,time:v.currentTime,source:v.currentSrc,connected:v.isConnected}));}''')
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=15000)
            note('cut-titles-colour-playback',cut)
            if reload_points:page.reload()
            page.get_by_role('button',name='Delivery workspace',exact=True).click()
            delivery=page.get_by_role('region',name='Trailer delivery',exact=True)
            delivery.get_by_role('button',name='Review and sign the saved rough cut',exact=True).click()
            review=page.get_by_role('region',name='Delivery review');expect(review).to_be_visible(timeout=240000)
            player=review.locator('video');expect(player).to_have_js_property('readyState',4,timeout=30000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=15000)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=60000)
            page.get_by_role('button',name='Return to delivery',exact=True).click()
            expect(delivery).to_contain_text('Signed by you',timeout=30000)
            delivery.get_by_role('button',name='Record the signed rough-cut approval',exact=True).click()
            expect(delivery.get_by_role('status').first).to_contain_text('Your signed stage approval is recorded.',timeout=30000)
            note('rough-signed-stage-recorded',delivery)
            if reload_points:page.reload()
            delivery.get_by_role('button',name='Review and sign colour and sound',exact=True).click()
            review=page.get_by_role('region',name='Delivery review',exact=True)
            expect(review.get_by_role('heading',name='Review the saved colour and sound',exact=True)).to_be_visible(timeout=240000)
            player=review.locator('video');expect(player).to_have_js_property('readyState',4,timeout=30000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=15000)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=60000)
            page.get_by_role('button',name='Return to delivery',exact=True).click()
            delivery.get_by_role('button',name='Record the signed colour and sound approval',exact=True).click()
            expect(delivery.get_by_role('status').first).to_contain_text('Your signed stage approval is recorded.',timeout=30000)
            note('colour-and-sound-signed',delivery)
            delivery.get_by_role('button',name='Make the final file',exact=True).click()
            expect(delivery).to_contain_text('Loudness is on target',timeout=300000)
            note('exported-file',delivery)
            tile=delivery.locator('[data-delivery]').first
            player=tile.locator('video');expect(player).to_have_js_property('readyState',4,timeout=30000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=15000)
            comparison=tile.get_by_role('region',name='Final story comparison',exact=True)
            tile.get_by_role('button',name='Review and sign final approval',exact=True).click()
            expect(delivery.get_by_role('status').first).to_contain_text('Complete the final story comparison')
            for checkbox in comparison.get_by_role('checkbox').all():checkbox.check()
            comparison.get_by_label('How this final file keeps the story’s tone',exact=True).fill('This invented test keeps the gallery waiting, without showing the protected spoiler.')
            note('final-story-listening-comparison',comparison)
            tile.get_by_role('button',name='Review and sign final approval',exact=True).click()
            review=page.get_by_role('region',name='Delivery review',exact=True)
            expect(review).to_be_visible(timeout=240000)
            expect(review.get_by_role('region',name='Pictures from the final file')).to_be_visible()
            player=review.locator('video');expect(player).to_have_js_property('readyState',4,timeout=30000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=15000)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=120000)
            page.get_by_role('button',name='Return to delivery',exact=True).click()
            expect(tile).to_contain_text('Approved by you',timeout=60000)
            tile.get_by_role('button',name='Record the signed final approval',exact=True).click()
            expect(delivery.get_by_role('status').first).to_contain_text('Your signed stage approval is recorded.',timeout=120000)
            if reload_points:page.reload()
            tile.get_by_role('button',name='Show approved file in Finder',exact=True).click()
            expect(delivery.get_by_role('status').first).to_contain_text('shown in Finder',timeout=30000)
            note('signed-final-and-reveal',delivery)
            assert len(calls['reveals'])==1
            import shutil
            picture_folder=os.environ.get('EARLY_TEST_PICTURES')
            if picture_folder:
                with Path(calls['reveals'][0]).open('rb') as source,(Path(picture_folder)/(RUN+'-final-trailer.mp4')).open('xb') as destination:
                    shutil.copyfileobj(source,destination)
            assert len(calls['audio_posts'])==4 and len(calls['catalogues'])==1
            assert all(len(calls[k])==1 for k in ('uploads','video_posts','waits','downloads'))
            assert not page.errors
            from backlot.state import load_board_state
            evidence['state']=load_board_state(root)
            assert [stage['name'] for stage in evidence['state']['stages'] if stage['status']=='completed']==['canon_ingest','script','edit','colour_mix','compose']
            assert all(not stage.get('availability_message') for stage in evidence['state']['stages'])
            evidence['body']=page.locator('body').inner_text()
            with (parent/(RUN+'-journey-map.json')).open('x') as f:json.dump(evidence,f,default=str,indent=2)
        except Exception:
            evidence['body']=page.locator('body').inner_text();evidence['page_errors']=page.errors
            evidence['playback_events']=page.evaluate('()=>window.cutPlaybackEvents||[]')
            evidence['players']=page.locator('video').evaluate_all('(items)=>items.map(v=>({source:v.currentSrc,time:v.currentTime,duration:v.duration,paused:v.paused,ended:v.ended,ready:v.readyState,error:v.error?.message}))')
            with (parent/(RUN+'-journey-map-stop.json')).open('x') as f:json.dump(evidence,f,default=str,indent=2)
            shot_picture(page,'journey-stop')
            raise
        finally:browser.close()
