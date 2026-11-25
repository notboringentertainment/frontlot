"""Recover waiting clips through the real writer controls under the icon PATH."""
import json,os,time
import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http
from tests.lib.test_trimmed_trailer_delivery import trailer_cut


def test_reloaded_held_clip_leads_to_its_account_review(film,provider,monkeypatch):
    from backlot.footage_controls import create
    from lib import footage_requests
    from tests.lib.look_lock_helpers import approve_request
    from tools.video import _shared
    root,shot=film
    gate=create(root,{'shot_id':shot,'model':footage_requests.MODEL,'duration_seconds':5})['gate']
    approve_request(gate,root)
    def timeout(*a,**k):provider['waits'].append(1);raise TimeoutError('Invented unknown outcome.')
    monkeypatch.setattr(_shared,'fal_queue_wait',timeout)
    with pytest.raises(ValueError,match='money is held'):footage_requests.execute(root,gate['entity_id'])
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(url+'/p/'+root.name+'#k=invented-browser-proof')
            next_step=page.get_by_label('Next step',exact=True)
            expect(next_step).to_contain_text('unresolved footage charge',timeout=30000)
            next_step.get_by_role('button',name='Go to this step',exact=True).click()
            expect(page.get_by_role('button',name='Review account outcome',exact=True)).to_be_focused()
            assert len(provider['posts'])==len(provider['uploads'])==len(provider['waits'])==1
            assert not provider['downloads'] and not errors
        finally:browser.close()


def test_next_step_reaches_footage_cutting_delivery_and_finished(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(url+'/p/'+root.name+'#k=invented-browser-proof')
            page.get_by_label('Next step',exact=True).wait_for()
            results=page.evaluate('''async()=>{
              const m=await import('/ui/next_step.js');
              const s={project_id:'invented-guidance',gates:{requests:[]},stages:[{name:'canon_ingest',status:'completed'},{name:'script',status:'pending'},{name:'edit',status:'pending'},{name:'colour_mix',status:'pending'},{name:'compose',status:'pending'}],paper_cut:{usable_downstream:true,current:{paper_cut:{lines:[{}],shots:[{}]},timeline:{picture:[]}}}};
              let n=m.renderNextStep(s,()=>{});document.body.append(n);
              const a={price_settings_signed:true,takes:[{kind:'speech',status:'Accepted'}],requests:[]};m.setAudioProgress(s.project_id,a);
              const out=[n.textContent];m.setFootageSetupProgress(s.project_id,{price_settings_signed:true,footage_prices:{},approved_looks:[{}]});
              out.push(n.textContent);s.paper_cut.whole_review_status='Signed by you';m.renderNextStep(s,()=>{});out.push(n.textContent);
              s.stages[1].status='completed';m.setFootageProgress(s.project_id,{shots:[{id:'one',label:'Shot 1',references:['picture'],takes:[],requests:[]}]});out.push(n.textContent);
              m.setFootageProgress(s.project_id,{shots:[{id:'one',label:'Shot 1',references:['picture'],takes:[{status:'Accepted'}],requests:[]}]});out.push(n.textContent);
              s.paper_cut.current.cutting_view=true;s.paper_cut.current.timeline.picture=[{}];m.renderNextStep(s,()=>{});out.push(n.textContent);
              s.stages.forEach(r=>r.status='completed');m.renderNextStep(s,()=>{});out.push(n.textContent);
              m.setFootageProgress(s.project_id,{shots:[{requests:[{reservation:{state:'pending_billing',reservation_id:'held'}}]}]});out.push(n.textContent);
              m.setFootageProgress(s.project_id,{shots:[{requests:[{reservation:{state:'pending_billing',reservation_id:'held'},signed_outcome:{}}]}]});out.push(n.textContent);
              m.setFootageProgress(s.project_id,{shots:[]});s.stages[4].status='pending';s.gates.requests=[{request_id:'buy',kind:'audio_spend',state:'pending'}];m.renderNextStep(s,()=>{});out.push(n.textContent);
              return out;
            }''')
            for text,expected in zip(results,['Footage setup','Review and sign the whole paper cut','Record the signed paper-cut review','Shot 1','Cutting workspace','Delivery workspace','Your trailer is finished','unresolved footage charge','Apply signed account outcome','one paid call']):assert expected in text
            assert 'buys nothing' not in results[-1]
            assert not posts and not gets and not errors
        finally:browser.close()


def test_sound_purchase_and_new_prices_leave_shot_recoverable(trailer_cut,monkeypatch,tmp_path):
    root,provider,posts,timeline,take=trailer_cut
    gets=[]
    class Voices:
        def raise_for_status(self):pass
        def json(self):return {'voices':[{'voice_id':'invented','name':'Invented narrator','category':'premade'}]}
    monkeypatch.setattr('requests.get',lambda *a,**k:(gets.append(1) or Voices()))
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    folder=Path(os.environ.get('STUCK_REQUEST_PICTURES',tmp_path/'pictures'));folder.mkdir(parents=True,exist_ok=True)
    def picture(page,name,control):
        control.evaluate('(e)=>e.scrollIntoView({block:"center",behavior:"instant"})')
        path=folder/(os.environ.get('STUCK_PICTURE_PREFIX','')+name+'.png');assert not path.exists();page.screenshot(path=str(path))
    def sign(page,back):
        page.get_by_role('button',name='Start signing',exact=True).click()
        page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
        expect(page.locator('#gate-sign-panel').get_by_text('Your approval is recorded. You can close this.',exact=True)).to_be_visible(timeout=30000)
        page.get_by_role('button',name=back,exact=True).click()
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            trace=tmp_path/'browser-requests.jsonl'
            def track(event,request):
                if '/api/' in request.url:
                    with trace.open('a') as stream:stream.write(json.dumps({'event':event,'url':request.url,'time':time.monotonic()})+'\n')
            for event in ['request','requestfinished','requestfailed']:page.on(event,lambda req,event=event:track(event,req))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            shot=page.get_by_role('article',name='Shot 1 footage',exact=True)
            shot.get_by_label('Clip length in seconds',exact=True).fill('5')
            shot.get_by_label('Clip length in seconds',exact=True).press('Tab')
            expect(shot).to_contain_text('Highest charge: $0.5000',timeout=30000)
            shot.get_by_role('button',name='Create a retake request',exact=True).click(timeout=30000)
            page.get_by_role('button',name='Return to footage',exact=True).click()
            page.get_by_role('button',name='Voice workspace',exact=True).click()
            audio=page.get_by_role('region',name='Trailer audio')
            audio.get_by_role('button',name='Load account voices',exact=True).click()
            line=audio.locator('[data-voice-line="0"]')
            try:line.get_by_label('Voice for line 1',exact=True).select_option('invented',timeout=60000)
            except Exception:
                picture(page,'load-voices-stop',audio)
                raise AssertionError(audio.inner_text())
            line.get_by_label('I have permission to use this voice.').check()
            line.get_by_role('button',name='Prepare a spending card',exact=True).click()
            card=audio.locator('.voice-spend').filter(has_text='Waiting for your signature')
            try:expect(card).to_have_count(1,timeout=30000)
            except Exception:
                picture(page,'prepare-sound-stop',audio)
                raise AssertionError(audio.inner_text())
            card.get_by_role('button',name='Review and sign this request',exact=True).click()
            sign(page,'Return to audio')
            card=audio.locator('.voice-spend').filter(has_text='Make this approved take')
            card.get_by_role('button',name='Make this approved take',exact=True).click()
            expect(audio.locator('.voice-take')).to_have_count(4,timeout=30000)
            assert len(posts)==2 and len(provider['posts'])==3
            expect(shot).to_contain_text('The remaining ceiling changed',timeout=30000)
            expect(shot.get_by_role('button',name='Review and sign request',exact=True)).to_have_count(0)
            picture(page,'after-sound-price-card-outdated',shot.get_by_role('button',name='Set aside unsigned request',exact=True))
            shot.get_by_role('button',name='Set aside unsigned request',exact=True).click()
            expect(shot.get_by_role('button',name='Create a retake request',exact=True)).to_be_enabled(timeout=30000)
            picture(page,'after-sound-shot-recovered',shot.get_by_role('button',name='Create a retake request',exact=True))
            shot.get_by_role('button',name='Create a retake request',exact=True).click()
            expect(page.get_by_label('Footage request details',exact=True)).to_contain_text('One clip, one signature',timeout=60000)
            page.get_by_role('button',name='Return to footage',exact=True).click()
            # Leave one more sound card waiting when the shared settings change.
            line.get_by_role('button',name='Prepare a spending card',exact=True).click()
            expect(audio.locator('.voice-spend').filter(has_text='Waiting for your signature')).to_have_count(1,timeout=30000)
            setup=page.get_by_role('region',name='Footage prerequisites',exact=True)
            setup.get_by_label('Highest cash dollars per second',exact=True).fill('0.12')
            setup.get_by_label('Highest credit replacement dollars per second',exact=True).fill('0')
            setup.get_by_label('Footage prices expire',exact=True).fill('2099-01-01T12:00')
            setup.get_by_label('Allow separately approved prompts to be sent to Fal.',exact=True).check()
            setup.get_by_label('Allow separately approved reference pictures to be sent to Fal.',exact=True).check()
            setup.get_by_role('button',name='Prepare footage price settings for signing',exact=True).click()
            sign(page,'Return to footage setup')
            expect(shot).to_contain_text('The footage price changed',timeout=30000)
            obsolete=audio.locator('.voice-spend').filter(has_text='Needs a fresh spending card')
            expect(obsolete).to_have_count(1,timeout=30000)
            expect(obsolete).to_contain_text('The signed production settings changed')
            obsolete.get_by_role('button',name='Prepare a fresh spending card',exact=True).click()
            expect(audio.locator('.voice-spend').filter(has_text='Waiting for your signature')).to_have_count(1,timeout=30000)
            picture(page,'fresh-audio-card-after-settings',audio.locator('.voice-spend').filter(has_text='Waiting for your signature'))
            picture(page,'new-prices-waiting-retake',shot.get_by_role('button',name='Set aside unsigned request',exact=True))
            # The stale review explains the same reason without starting a signer.
            from backlot.footage_controls import inspect
            stale=inspect(root,{})['shots'][0]['requests'][-1]
            page.locator(f'[data-request-id="{stale["gate_id"]}"]').first.click()
            expect(page.locator('#gate-detail')).to_contain_text(stale['signing_refusal'])
            expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_disabled()
            expect(page.locator('#gate-detail')).not_to_contain_text('until every picture')
            page.get_by_role('button',name='Return to footage',exact=True).click()
            shot.get_by_role('button',name='Set aside unsigned request',exact=True).click()
            expect(shot.get_by_role('button',name='Create a retake request',exact=True)).to_be_enabled(timeout=30000)
            shot.get_by_role('button',name='Create a retake request',exact=True).click()
            expect(page.get_by_label('Footage request details',exact=True)).to_contain_text('$0.6000',timeout=60000)
            picture(page,'fresh-footage-card-current-prices',page.get_by_label('Footage request details',exact=True))
            page.get_by_role('button',name='Return to footage',exact=True).click()
            assert len(posts)==2 and len(provider['posts'])==3 and len(gets)==1 and not errors
            if os.environ.get('STUCK_PREVIEW_READY'):
                with Path(os.environ['STUCK_PREVIEW_READY']).open('x') as stream:json.dump({'url':f'{url}/p/{root.name}#k=invented-browser-proof'},stream)
                deadline=time.monotonic()+600
                while not Path(os.environ['STUCK_PREVIEW_STOP']).exists() and time.monotonic()<deadline:page.wait_for_timeout(200)
        finally:browser.close()
