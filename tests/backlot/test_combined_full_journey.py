"""Empty library to separately signed speech and footage, on the icon PATH."""
import json,os,subprocess,time
from pathlib import Path
from tests.backlot.test_journey_map import _wav
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_early_paid_readiness import isolated_standins,start_browser,set_prices,save_paper_with_controls,buy_three_audio,shot_picture
from tests.backlot.test_footage_controls_browser import browser_board
from tests.backlot.footage_prereq_helpers import complete_footage_prerequisites,simple_sign
RUN=os.environ.get('EARLY_PICTURE_PREFIX','journey')

def test_full_visible_journey(tmp_path,monkeypatch,isolated_standins):
    parent=Path(os.environ['TMPDIR']).resolve().parent
    from tests.tools import test_signed_footage as fake
    # Prepare the stand-in's downloaded bytes before starting the board. All
    # real filing and board operations below run with the icon's short PATH.
    clip=fake.clip_bytes();monkeypatch.setattr(fake,'clip_bytes',lambda:clip)
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    calls=isolated_standins
    audio_bytes={kind:_wav(length,kind) for kind,length in [('speech',1),('music',12),('effect',2)]}
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
            content=_wav(2,'effect',len(calls['audio_posts'])) if allowed[url]=='effect' else audio_bytes[allowed[url]]
            def raise_for_status(self):pass
        return Reply()
    monkeypatch.setattr('requests.post',post)
    reveals=[];calls['reveals']=reveals;original_run=subprocess.run
    def local_run(args,*values,**options):
        if Path(args[0]).name=='open' and args[1]=='-R':
            assert Path(args[2]).resolve().is_relative_to(projects.resolve())
            reveals.append(args[2]);return subprocess.CompletedProcess(args,0)
        return original_run(args,*values,**options)
    monkeypatch.setattr(subprocess,'run',local_run)
    projects=tmp_path/'empty-library';projects.mkdir()
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',projects);monkeypatch.setattr('lib.events.PROJECTS_DIR',projects)
    monkeypatch.setattr('backlot.state.PROJECTS_DIR',projects)
    monkeypatch.setattr('backlot.server._PROJECTS_ROOT_STR',os.path.normcase(str(projects.resolve())))
    with browser_board(projects/'not-created',monkeypatch) as url,sync_playwright() as pw:
        url=url.replace('?static=1','')
        browser,page=start_browser(pw,url)
        try:
            page.goto(url.split('/p/')[0]+'/#k=invented-browser-writer')
            page.get_by_text('Start a trailer',exact=True).click()
            for label,value in [('Trailer name','Invented combined gallery'),('Target length in seconds','28'),('Total spending ceiling in dollars','10')]:page.get_by_label(label,exact=True).fill(value)
            shot_picture(page,'creation',page.get_by_role('button',name='Create trailer and review signing'))
            page.get_by_role('button',name='Create trailer and review signing').click()
            page.get_by_role('button').filter(has=page.locator('span.what',has_text='Start this trailer')).click()
            page.locator('#gate-detail .gate-primary').click();page.get_by_role('button',name='Approve and sign').click(timeout=20000)
            story=page.get_by_role('region',name='Trailer story')
            story.get_by_label('Exact story text',exact=True).fill('A gallery waits.\nThe gallery is closed.\nKeep the light on.')
            story.get_by_role('button',name='Add protected wording',exact=True).click()
            story.get_by_label('Protected wording 1',exact=True).fill('The gallery is closed.')
            story.get_by_role('button',name='Save story for review').click()
            story.get_by_role('button',name='Review and sign this story').click()
            shot_picture(page,'story-signing',page.get_by_role('region',name='Story approval'))
            simple_sign(page,'Return to story')
            page.get_by_role('button',name='Voice workspace',exact=True).click()
            audio=page.get_by_role('region',name='Trailer audio')
            expect(audio.locator('.voice-budget')).to_contain_text('$10.0000 remaining of $10.0000')
            set_prices(page,audio,'combined')
            paper=save_paper_with_controls(page,purpose='A wide view of the invented empty gallery. No people.')
            paper.get_by_role('button',name='Check paper cut against approved story').click()
            expect(paper.get_by_role('status').first).to_contain_text('compared')

            root=next(p for p in projects.iterdir() if (p/'project.json').exists())
            evidence={'root':str(root),'steps':[],'calls':calls}
            def paper_and_sound():
                page.get_by_role('button',name='Paper cut workspace',exact=True).click()
                paper.get_by_role('button',name='Review and sign the whole paper cut',exact=True).click()
                shot_picture(page,'paper-review',page.get_by_role('region',name='Whole paper-cut review',exact=True))
                simple_sign(page,'Return to paper cut')
                paper.get_by_role('button',name='Record the signed paper-cut review',exact=True).click()
                expect(paper.get_by_role('status').first).to_contain_text('Your signed paper-cut review is recorded.')
                page.get_by_role('button',name='Voice workspace',exact=True).click()
                buy_three_audio(page,audio,calls,'purchase')
            complete_footage_prerequisites(page,root,calls,tmp_path,audio_count=0,check_changed=False,before_purchase=paper_and_sound)
            shot=page.get_by_role('article',name='Shot 1 footage',exact=True)
            expect(shot.get_by_role('button',name='Keep this take',exact=True)).to_have_count(0)
            expect(shot.get_by_role('button',name='Decline this take',exact=True)).to_have_count(0)
            def note(name,panel):
                evidence['steps'].append({'step':name,'text':panel.inner_text()})
                with (Path(os.environ['TMPDIR']).resolve().parent/('journey-progress.jsonl')).open('a') as progress:
                    progress.write(json.dumps({'step':name,'at':time.time()})+'\n')
                target=panel
                if name=='cut-titles-colour-playback':target=panel.locator('video').first
                elif name=='exported-file':target=panel.locator('[data-delivery] video').first
                elif name in ('rough-signed-stage-incomplete','signed-final-and-reveal'):target=panel.get_by_role('status').first
                shot_picture(page,name,target)
            def record_stage(panel,label,path):
                # The operation rechecks actual files and the complete signature
                # history before writing. Measure that response separately from the
                # screen update: an old confirmation is not evidence of this action.
                endpoint=url.split('/p/')[0]+f'/api/project/{root.name}/delivery'+path
                posts=[];began=time.monotonic()
                def requested(request):
                    if request.url==endpoint and request.method=='POST':posts.append(request)
                page.on('request',requested)
                try:
                    with page.expect_response(lambda response:response.url==endpoint and response.request in posts,timeout=180000) as applied:
                        panel.get_by_role('button',name=label,exact=True).click()
                    response=applied.value
                    assert response.ok,response.text()
                    assert response.json()['message']=='Your signed stage approval is recorded.'
                    expect(panel.get_by_role('status').first).to_have_text(response.json()['message'],timeout=5000)
                    assert len(posts)==1
                    evidence.setdefault('recording_seconds',{})[path]=time.monotonic()-began
                finally:page.remove_listener('request',requested)
            page.get_by_role('button',name='Cutting workspace',exact=True).click()
            cut=page.get_by_role('region',name='Cutting view',exact=True)
            expect(cut.get_by_label('Kept picture take',exact=True).locator('option')).to_have_count(1,timeout=180000)
            expect(cut.get_by_label('Kept sound take',exact=True).locator('option')).to_have_count(3,timeout=180000)
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
            with page.expect_response(lambda r:r.url.endswith('/cut') and r.request.method=='POST',timeout=180000) as saving:
                cut.get_by_role('button',name='Save cut',exact=True).click()
            assert saving.value.ok,saving.value.text()
            expect(cut.get_by_role('status').first).to_contain_text(f'Version {saving.value.json()["number"]} saved.',timeout=180000)
            for i in (1,2,3):
                cut.get_by_role('button',name=f'Review and sign card {i}',exact=True).click()
                expect(page.get_by_role('region',name='Exact card review')).to_be_visible(timeout=180000)
                page.get_by_role('button',name='Start signing',exact=True).click()
                page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=180000)
                expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=180000)
                page.get_by_role('button',name='Return to cutting view',exact=True).click()
            for _ in range(2):cut.get_by_label('Brighter or darker for picture 1',exact=True).press('ArrowRight')
            with page.expect_response(lambda r:r.url.endswith('/cut') and r.request.method=='POST',timeout=180000) as saving:
                cut.get_by_role('button',name='Save cut',exact=True).click()
            assert saving.value.ok,saving.value.text()
            expect(cut.get_by_role('status').first).to_contain_text(f'Version {saving.value.json()["number"]} saved.',timeout=180000)
            cut.get_by_role('button',name='Prepare saved playback',exact=True).click()
            expect(cut.get_by_role('status').first).to_contain_text('ready',timeout=240000)
            player=cut.locator('video');expect(player).to_have_js_property('readyState',4,timeout=20000)
            player.evaluate('''v=>{window.cutPlaybackEvents=[];for(const kind of ['play','pause','ended','emptied','loadedmetadata','seeking','seeked','error'])v.addEventListener(kind,()=>window.cutPlaybackEvents.push({kind,time:v.currentTime,source:v.currentSrc,connected:v.isConnected}));}''')
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=60000)
            note('cut-titles-colour-playback',cut)
            page.reload()
            page.get_by_role('button',name='Delivery workspace',exact=True).click()
            delivery=page.get_by_role('region',name='Trailer delivery',exact=True)
            delivery.get_by_role('button',name='Review and sign the saved rough cut',exact=True).click()
            review=page.get_by_role('region',name='Delivery review');expect(review).to_be_visible(timeout=240000)
            player=review.locator('video');expect(player).to_have_js_property('readyState',4,timeout=180000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=60000)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=180000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=60000)
            page.get_by_role('button',name='Return to delivery',exact=True).click()
            expect(delivery).to_contain_text('Signed by you',timeout=180000)
            record_stage(delivery,'Record the signed rough-cut approval','/rough/apply')
            note('rough-signed-stage-recorded',delivery)
            if reload_points:page.reload()
            delivery.get_by_role('button',name='Review and sign colour and sound',exact=True).click()
            review=page.get_by_role('region',name='Delivery review',exact=True)
            expect(review.get_by_role('heading',name='Review the saved colour and sound',exact=True)).to_be_visible(timeout=240000)
            player=review.locator('video');expect(player).to_have_js_property('readyState',4,timeout=180000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=60000)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=180000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=60000)
            page.get_by_role('button',name='Return to delivery',exact=True).click()
            record_stage(delivery,'Record the signed colour and sound approval','/colour/apply')
            note('colour-and-sound-signed',delivery)
            delivery.get_by_role('button',name='Make the final file',exact=True).click()
            expect(delivery).to_contain_text('Loudness is on target',timeout=300000)
            note('exported-file',delivery)
            tile=delivery.locator('[data-delivery]').first
            player=tile.locator('video');expect(player).to_have_js_property('readyState',4,timeout=180000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=60000)
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
            player=review.locator('video');expect(player).to_have_js_property('readyState',4,timeout=180000)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=60000)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=180000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=120000)
            page.get_by_role('button',name='Return to delivery',exact=True).click()
            expect(tile).to_contain_text('Approved by you',timeout=60000)
            record_stage(delivery,'Record the signed final approval','/'+tile.get_attribute('data-delivery')+'/apply')
            if True:
                # Check reopening from its fresh verified answer, as with a
                # signing return. File/receipt verification has its own bound;
                # the screen then has five seconds to show that exact record.
                # This never accepts an old response or an unverified player.
                from lib import trailer_edit
                expected_id=tile.get_attribute('data-delivery')
                expected_revision=trailer_edit.read_revision(root)['edit_revision_sha256']
                endpoint=url.split('/p/')[0]+f'/api/project/{root.name}/delivery';started=[]
                def requested(request):
                    if request.method=='GET' and request.url==endpoint:started.append(request)
                page.on('request',requested)
                opening=time.monotonic()
                try:
                    with page.expect_response(lambda r:r.url==endpoint and r.request in started,timeout=180000) as reloaded:
                        page.reload()
                    response=reloaded.value
                    assert response.ok,response.text()
                    current=response.json()
                    assert current['revision']==expected_revision
                    assert current['rough_status']==current['colour_status']=='Signed by you'
                    assert current['exports'][0]['id']==expected_id
                    assert current['exports'][0]['status']=='Approved by you'
                    expect(tile).to_contain_text('Approved by you',timeout=5000)
                    expect(tile.get_by_role('button',name='Show approved file in Finder',exact=True)).to_be_enabled(timeout=5000)
                    evidence['last_reopening_seconds']=time.monotonic()-opening
                finally:page.remove_listener('request',requested)
                reopened=tile.locator('video')
                expect(reopened).to_have_js_property('readyState',4,timeout=180000)
                reopened.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=reopened.element_handle(),timeout=60000)
            tile.get_by_role('button',name='Show approved file in Finder',exact=True).click()
            expect(delivery.get_by_role('status').first).to_contain_text('shown in Finder',timeout=180000)
            note('signed-final-and-reveal',delivery)
            assert len(reveals)==1
            import shutil
            picture_folder=os.environ.get('EARLY_TEST_PICTURES')
            if picture_folder:
                with Path(calls['reveals'][0]).open('rb') as source,(Path(picture_folder)/('journey-final-trailer.mp4')).open('xb') as destination:
                    shutil.copyfileobj(source,destination)
            assert len(calls['audio_posts'])==3 and len(calls['catalogues'])==1
            assert all(len(calls[k])==1 for k in ('uploads','video_posts','waits','downloads'))
            assert not page.errors
            from backlot.state import load_board_state
            evidence['state']=load_board_state(root)
            assert [stage['name'] for stage in evidence['state']['stages'] if stage['status']=='completed']==['canon_ingest','script','edit','colour_mix','compose']
            assert all(not stage.get('availability_message') for stage in evidence['state']['stages'])
            evidence['body']=page.locator('body').inner_text()
            with (parent/(RUN+'-journey-map.json')).open('x') as f:json.dump(evidence,f,default=str,indent=2)
        except Exception:
            evidence=locals().get('evidence',{})
            evidence['body']=page.locator('body').inner_text();evidence['page_errors']=page.errors
            evidence['playback_events']=page.evaluate('()=>window.cutPlaybackEvents||[]')
            evidence['players']=page.locator('video').evaluate_all('(items)=>items.map(v=>({source:v.currentSrc,time:v.currentTime,duration:v.duration,paused:v.paused,ended:v.ended,ready:v.readyState,error:v.error?.message}))')
            with (parent/(RUN+'-journey-map-stop.json')).open('x') as f:json.dump(evidence,f,default=str,indent=2)
            shot_picture(page,'journey-stop')
            raise
        finally:browser.close()
