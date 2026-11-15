"""Changed-read pointer races and safe, visible writer refusals."""
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel import voice_world,project,priced,http,options,sign
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_followups_browser import open_page
from tests.backlot.test_first_hands import failed_filing
from tests.backlot.signing_outcome_helpers import bounded_signers,figures


@pytest.mark.parametrize('contents',['held','take-and-request'])
def test_every_audio_button_receives_its_release_during_changed_reads(voice_world,monkeypatch,contents):
    root,posts,gets=voice_world
    if contents=='held':failed_filing(root,monkeypatch)
    else:
        from tests.backlot.test_followups_browser import own_take
        from backlot import voice_panel as voice
        own_take(root);voice.catalogue(root);record=voice.prepare_voice(root,options())['request']
        sign(root,'audio_spend',record,record['request_id'])
    before=len(posts)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);audio=page.get_by_role('region',name='Trailer audio')
            expect(audio.get_by_role('button',name='Review and sign this account outcome' if contents=='held' else 'Keep this take',exact=True)).to_be_visible()
            audio.locator('details').evaluate_all('es=>es.forEach(e=>e.open=true)')
            # Observe delivery without executing purchases. Separate journey
            # tests exercise the real callbacks and signed account review.
            page.evaluate("()=>document.addEventListener('click',e=>{if(e.target===window.pressed){window.delivered++;e.stopImmediatePropagation();e.preventDefault();}},true)")
            labels=audio.get_by_role('button').evaluate_all("es=>es.filter(e=>!e.disabled).map(e=>e.textContent)")
            for number,label in enumerate(dict.fromkeys(labels)):
                button=audio.get_by_role('button',name=label,exact=True).first
                if not button.is_visible():continue
                button.scroll_into_view_if_needed();button.evaluate('(e)=>{window.pressed=e;window.delivered=0;}');box=button.bounding_box()
                page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down()
                def changed(route):
                    data=route.fetch().json();data['record_warnings']=['Invented changed information '+str(number)];route.fulfill(json=data)
                page.route('**/voice',changed)
                with page.expect_response(url+'/api/project/'+root.name+'/voice'):
                    page.evaluate("id=>import('/ui/voice.js').then(m=>m.renderVoice({project_id:id},()=>{}))",root.name)
                page.wait_for_timeout(150);page.mouse.up()
                assert page.evaluate('window.delivered')==1,label+' lost its mouse release'
                page.unroute('**/voice',changed)
            assert len(posts)==before
        finally:page.unroute_all(behavior='ignoreErrors');browser.close()


def test_refused_save_says_copy_before_opening_the_icon(voice_world,monkeypatch):
    from backlot import server
    root,posts,_=voice_world;original=server.create_app;apps=[]
    def create(*a,**kw):app=original(*a,**kw);apps.append(app);return app
    monkeypatch.setattr(server,'create_app',create)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);paper=page.get_by_role('region',name='Trailer paper cut')
            paper.get_by_label('Hook',exact=True).fill('Keep my unsaved hook.')
            apps[0].state.capability_token='invented-rotated-token'
            button=paper.get_by_role('button',name='Save a new version',exact=True);button.click()
            note=button.locator('xpath=following-sibling::*[1]')
            expect(note).to_contain_text('Copy')
            expect(note).not_to_contain_text('Reload')
            expect(paper.get_by_label('Hook',exact=True)).to_have_value('Keep my unsaved hook.')
            assert not posts
        finally:browser.close()


def test_guidance_never_points_back_to_the_signature_just_recorded(voice_world,monkeypatch):
    root,posts,_=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            text=page.evaluate("""async()=>{const m=await import('/ui/next_step.js');const s={project_id:'invented-guidance',gates:{requests:[{request_id:'invented-signature',state:'pending'}]}};
              const n=m.renderNextStep(s,()=>{});document.dispatchEvent(new CustomEvent('frontlot:signature-recorded',{detail:{id:'invented-signature'}}));return n.textContent;}""")
            assert 'signature is recorded' in text and 'Updating the saved state' in text and 'waiting for your signature' not in text
            assert not posts
        finally:browser.close()


def test_recorded_signature_with_failed_apply_never_offers_resigning(voice_world,monkeypatch,bounded_signers):
    root,posts,_=voice_world;failed_filing(root,monkeypatch)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);audio=page.get_by_role('region',name='Trailer audio')
            audio.get_by_label('The account was charged.',exact=True).check()
            audio.get_by_label('Actual credit replacement dollars',exact=True).fill('.0038')
            audio.get_by_label('What I checked in the account',exact=True).fill('I checked the invented account.')
            page.route('**/voice/resolution/*/apply',lambda route:route.fulfill(status=500,json={'detail':'Invented apply failure.'}))
            audio.get_by_role('button',name='Review and sign this account outcome',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('automatic follow-up did not finish',timeout=15000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded')
            expect(page.get_by_role('button',name='Try signing again',exact=True)).to_have_count(0)
            page.get_by_role('button',name='Return to audio',exact=True).click()
            page.unroute('**/voice/resolution/*/apply')
            audio.get_by_role('button',name='Apply the signed account outcome',exact=True).click()
            expect(audio).not_to_contain_text('Review an unresolved audio charge',timeout=15000)
            assert figures(root,1,fresh=True)==pytest.approx([.0038,0,.9962]) and len(posts)==1
        finally:page.unroute_all(behavior='ignoreErrors');browser.close()
