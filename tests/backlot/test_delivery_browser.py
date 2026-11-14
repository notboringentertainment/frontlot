"""The complete local delivery journey uses the actual board and signer."""
import json,os,subprocess
from threading import Lock
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.title_renderer import require_title_renderer

@pytest.fixture
def delivery_cut(signed_cut):
    from lib import trailer_edit,cutting_view as cut,audio_workspace as a,trailer_titles as t
    from tests.lib.test_trailer_titles import card
    root,provider,posts,*_=signed_cut
    for name,source in [('delivery-bed','sine=frequency=211:duration=3'),('delivery-latch','anoisesrc=color=white:amplitude=0.2:duration=0.5:sample_rate=48000')]:
        subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i',source,str(root/f'assets/{name}.wav')],check=True)
    effect=a.save_effect_brief(root,'A wooden latch.',.5,False,36,0)
    music=a.import_music(root,root/'assets/delivery-bed.wav','An invented music bed','I made and own this invented recording.')
    latch=a.import_music(root,root/'assets/delivery-latch.wav','An invented latch','I made and own this invented recording.',kind='effect',brief_id=effect)
    for item in (music,latch):sign(root,'audio_take',a.decision_record(root,item['id'],'accept'),item['id'])
    saved=trailer_edit.read_revision(root);v=cut.editable(saved['timeline']);v['audio']=[v['audio'][0]]
    for take,start,stop in [(music,0,72),(latch,36,12)]:v['audio'].append(dict(take_id=take['id'],start_frame=start,source_in_frame=0,source_out_frame=stop,level_db=-6,fade_in_frames=0,fade_out_frames=0))
    v['picture'][0]['colour']['brightness']=10;v['picture'][1]['colour']['warmth']=-10;v['mix']['speech_margin_db']=15
    v['cards']=[]
    for i,(words,start,length) in enumerate([('One lamp.',6,6),('“Cafe\u0301 — tonight.”',36,6),('The night gallery',60,12)]):
        row=card(words);row.update(id=f'delivery-card-{i}',start_frame=start,duration_frames=length,role='end' if i==2 else 'intertitle')
        v['cards'].append(row)
    cut.save_cut(root,v,saved['edit_revision_sha256'])
    for row in v['cards']:looks.approve_request(t.request_approval(root,row['id']),root)
    return root,provider,posts

def test_delivery_controls_sign_export_watch_approve_and_reveal(delivery_cut,monkeypatch):
    delivery_journey(delivery_cut,monkeypatch)

def delivery_journey(delivery_cut,monkeypatch,repeat=1):
    from lib import trailer_delivery as d
    from backlot import delivery as adapter
    root,provider,posts=delivery_cut;revealed=[];original=subprocess.run
    comparisons=[];comparison_lock=Lock();original_packet=adapter.signing_packet
    def packet(root,request):
        if request.get('stage')=='edit':
            with comparison_lock:
                if not comparisons:
                    from lib import cutting_view,trailer_edit
                    saved=trailer_edit.read_revision(root)
                    comparisons.append(cutting_view.render_preview(root,saved['edit_revision_sha256'],'before'))
        return original_packet(root,request)
    monkeypatch.setattr(adapter,'signing_packet',packet)
    def run(args,*a,**kw):
        if args[:2]==['open','-R']:revealed.append(args);return subprocess.CompletedProcess(args,0)
        return original(args,*a,**kw)
    monkeypatch.setattr(subprocess,'run',run)
    destination=os.environ.get('DELIVERY_CAPTURE')
    prefix=os.environ.get('DELIVERY_CAPTURE_PREFIX','')
    def capture(page,name,target):
        if destination:
            target.evaluate('(e)=>e.scrollIntoView({block:"center",behavior:"instant"})')
            path=Path(destination)/(prefix+name+'.png');assert not path.exists();page.screenshot(path=str(path))
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        if destination:print('Invented delivery board:',url+'/p/'+root.name+'#k=invented-browser-proof',flush=True)
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.add_init_script('''{
                const original=window.fetch;window.deliveryFetches=[];
                window.fetch=async(...args)=>{
                    const url=String(args[0]);
                    if(!/\\/delivery(?:$|\\/)/.test(url))return original(...args);
                    const row={url,started:Date.now()};window.deliveryFetches.push(row);
                    try{const response=await original(...args);row.status=response.status;row.finished=Date.now();return response;}
                    catch(error){row.error=String(error);row.finished=Date.now();throw error;}
                };
            }''')
            delivery_reads=[];delivery_requests=[]
            page.on('request',lambda r:delivery_requests.append(r.method) if r.url==url+f'/api/project/{root.name}/delivery' else None)
            def observed(response):
                if response.url==url+f'/api/project/{root.name}/delivery' and response.status==200:
                    value=response.json();delivery_reads.append((value['rough_status'],[(r['id'],r['status']) for r in value['exports']]))
            page.on('response',observed)
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer delivery');expect(panel).to_be_visible(timeout=30000)
            response=page.request.get(url+f'/api/project/{root.name}/delivery',timeout=60000)
            assert response.ok,response.text()
            assert response.json()['rough_status']=='Needs your signature'
            expect(panel).to_contain_text('Needs your signature',timeout=30000);capture(page,'rough-cut-waiting',panel)
            panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True).click()
            signing=page.get_by_role('region',name='Delivery review');expect(signing).to_be_visible(timeout=240000)
            expect(signing.get_by_role('heading',name='Review the saved rough cut',exact=True)).to_be_visible()
            player=signing.locator('video');expect(player).to_have_js_property('readyState',4,timeout=30000)
            assert comparisons and not player.get_attribute('src').endswith(comparisons[0].name)
            player.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=20000)
            capture(page,'rough-cut-signature',signing)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=60000)
            page.get_by_role('button',name='Return to delivery',exact=True).click()
            response=page.request.get(url+f'/api/project/{root.name}/delivery',timeout=60000)
            assert response.ok,response.text()
            assert response.json()['rough_status']=='Signed by you'
            panel=page.get_by_role('region',name='Trailer delivery');expect(panel).to_contain_text('Signed by you',timeout=30000)
            panel.get_by_role('button',name='Make the final file',exact=True).click()
            expect(panel).to_contain_text('Loudness is on target',timeout=300000)
            capture(page,'measured-final-file',panel)
            player=panel.locator('video');expect(player).to_have_js_property('readyState',4,timeout=30000)
            player.evaluate('(v)=>v.play()');page.wait_for_timeout(1200);capture(page,'final-file-playing',player)
            page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=20000)
            panel.get_by_role('button',name='Review and sign final approval',exact=True).click()
            signing=page.get_by_role('region',name='Delivery review');expect(signing).to_be_visible(timeout=60000)
            expect(signing.get_by_role('heading',name='Review this exact final file',exact=True)).to_be_visible()
            player=signing.locator('video');expect(player).to_have_js_property('readyState',4,timeout=30000)
            player.evaluate('(v)=>v.play()')
            page.wait_for_function('(v)=>v.ended',arg=player.element_handle(),timeout=20000)
            capture(page,'exact-file-signature',signing)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=60000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=120000)
            assert d.snapshot(root)['exports'][0]['status']=='Approved by you'
            page.get_by_role('button',name='Return to delivery',exact=True).click()
            try:expect(panel).to_contain_text('Approved by you',timeout=30000)
            except AssertionError as e:raise AssertionError('Verified delivery responses: '+repr(delivery_reads[-12:])+'; requests: '+repr(delivery_requests)+'; page errors: '+repr(errors)) from e
            page.reload()
            panel=page.get_by_role('region',name='Trailer delivery')
            expect(panel).to_contain_text('Approved by you',timeout=30000)
            panel.get_by_role('button',name='Record the signed final approval',exact=True).click()
            expect(panel.get_by_role('status').first).to_contain_text('signature',timeout=120000)
            panel.get_by_role('button',name='Show approved file in Finder',exact=True).click()
            expect(panel.get_by_role('status').first).to_contain_text('shown in Finder',timeout=30000)
            assert len(revealed)==1;capture(page,'approved-file-location',panel)
            for journey in range(1,repeat):
                # Every repeat starts with the same saved cut and uses the
                # visible rough-review, export, final-review and reveal
                # controls. There is no reload to rescue a signing return.
                panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True).click()
                def review_and_return(heading):
                    review=page.get_by_role('region',name='Delivery review',exact=True)
                    expect(review.get_by_role('heading',name=heading,exact=True)).to_be_visible(timeout=240000)
                    movie=review.locator('video');expect(movie).to_have_js_property('readyState',4,timeout=30000)
                    movie.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=movie.element_handle(),timeout=20000)
                    page.get_by_role('button',name='Start signing',exact=True).click()
                    page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=60000)
                    expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=120000)
                    page.get_by_role('button',name='Return to delivery',exact=True).click()
                review_and_return('Review the saved rough cut')
                expect(panel.get_by_role('button',name='Make the final file',exact=True)).to_be_enabled(timeout=30000)
                previous=panel.locator('[data-delivery]').first.get_attribute('data-delivery')
                panel.get_by_role('button',name='Make the final file',exact=True).click()
                page.wait_for_function('(old)=>document.querySelector("[data-delivery]")?.dataset.delivery!==old',arg=previous,timeout=300000)
                current=panel.locator('[data-delivery]').first
                expect(current).to_contain_text('Needs your signature',timeout=30000)
                expect(current.locator('video')).to_have_attribute('src',__import__('re').compile('/delivery/'))
                current.get_by_role('button',name='Review and sign final approval',exact=True).click()
                review_and_return('Review this exact final file')
                try:expect(current).to_contain_text('Approved by you',timeout=30000)
                except AssertionError as e:
                    with (root/'delivery-return-failure.json').open('x') as f:json.dump({
                        'journey':journey+1,'reads':delivery_reads[-12:],'fetches':page.evaluate('()=>window.deliveryFetches'),
                        'words':panel.inner_text(),'page_errors':errors},f,indent=2)
                    raise AssertionError('Signing return diagnostic: '+str(root/'delivery-return-failure.json')) from e
                expect(panel.get_by_role('status').first).not_to_contain_text('Rechecking')
                current.get_by_role('button',name='Show approved file in Finder',exact=True).click()
                expect(panel.get_by_role('status').first).to_contain_text('shown in Finder',timeout=30000)
                assert len(revealed)==journey+1
                assert d.snapshot(root)['exports'][0]['status']=='Approved by you'
                print(f'Completed local Delivery journey {journey+1} of {repeat}.',flush=True)
                if journey==1 and os.environ.get('DELIVERY_REPEAT_CAPTURE'):
                    current.evaluate('(e)=>e.scrollIntoView({block:"start",behavior:"instant"})')
                    shot=Path(os.environ['DELIVERY_REPEAT_CAPTURE'])/'round2-current-and-earlier-exports.png'
                    assert not shot.exists();page.screenshot(path=str(shot))
            snapshot=d.snapshot(root);item=snapshot['exports'][0]
            if destination:
                with (Path(destination)/(prefix+'delivery-final-proof.mp4')).open('xb') as f:f.write((root/item['path']).read_bytes())
                with (Path(destination)/(prefix+'delivery-final-measurements.json')).open('x') as f:json.dump(item['review'],f,indent=2)
            assert len(provider['posts'])==3 and len(posts)==1 and not errors
        finally:browser.close()
