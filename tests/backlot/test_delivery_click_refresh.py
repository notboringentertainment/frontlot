"""A real board refresh must not swallow a Delivery press or the writer's place."""
import copy
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http

def setup(voice_world,monkeypatch):
    from lib import trailer_delivery as delivery
    from tests.tools.test_signed_footage import clip_bytes
    root,posts,gets=voice_world
    row={'id':'invented-final','status':'Approved by you','samples':[],
         'review':{'issues_found':[],'checks':{'technical_probe':{'duration_seconds':3,'file_size_bytes':491460}},
                   'metadata':{'test_only':False,'boundary_samples':[],
                               'measurements':{'integrated_lufs':-16,'true_peak_dbtp':-1.5}}}}
    saved={'number':1,'revision':'invented-saved-revision','rough_status':'Signed by you','exports':[row],'recorded_stages':[]}
    monkeypatch.setattr(delivery,'snapshot',lambda _:copy.deepcopy(saved))
    movie=root/'assets/invented-local.mp4';movie.write_bytes(clip_bytes())
    monkeypatch.setattr(delivery,'playback',lambda *_:movie)
    reveals=[]
    def reveal(project,identity):
        assert project==root and identity=='invented-final';reveals.append(identity)
        return {'message':'The approved trailer has been shown in Finder.','path':str(movie)}
    monkeypatch.setattr(delivery,'reveal',reveal)
    return root,posts,gets,saved,reveals

def open_board(browser,url,root):
    page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];outside=[]
    page.add_init_script('''(()=>{
      window.pendingBoardFetches=0;
      const original=fetch;window.fetch=async(...args)=>{window.pendingBoardFetches++;try{return await original(...args);}finally{window.pendingBoardFetches--;}};
    })();''')
    page.on('pageerror',lambda error:errors.append(str(error)))
    def route(request):
        if request.request.url.startswith(url+'/'):request.continue_()
        else:outside.append(request.request.url);request.abort()
    page.route('**/*',route);page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
    panel=page.get_by_role('region',name='Trailer delivery',exact=True)
    button=panel.get_by_role('button',name='Show approved file in Finder',exact=True)
    expect(button).to_be_enabled(timeout=15000)
    page.wait_for_function('()=>window.pendingBoardFetches===0')
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    button.scroll_into_view_if_needed();button.focus()
    return page,panel,button,errors,outside

@pytest.mark.parametrize('refresh',['board','delivery'])
def test_refresh_between_mouse_down_and_up_delivers_one_click(voice_world,monkeypatch,refresh):
    root,posts,gets,saved,reveals=setup(voice_world,monkeypatch)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page,panel,button,errors,outside=open_board(browser,url,root)
            box=button.bounding_box();point={'x':box['x']+box['width']/2,'y':box['y']+box['height']/2}
            assert page.evaluate('(p)=>document.elementFromPoint(p.x,p.y)?.textContent',point)=='Show approved file in Finder'
            page.mouse.move(point['x'],point['y']);page.mouse.down()
            if refresh=='board':
                with page.expect_response(url+f'/api/project/{root.name}/state'):
                    page.evaluate('()=>window.dispatchEvent(new Event("focus"))')
            else:
                older=copy.deepcopy(saved['exports'][0]);older.update(id='invented-earlier',status='Needs a fresh look');saved['exports'].append(older)
                with page.expect_response(url+f'/api/project/{root.name}/delivery'):
                    page.evaluate('async(id)=>{const module=await import("/ui/delivery.js");module.renderDelivery({project_id:id},()=>{});}',root.name)
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            with page.expect_response(url+f'/api/project/{root.name}/delivery/invented-final/reveal',timeout=5000) as response:
                page.mouse.up()
            assert response.value.status==200 and reveals==['invented-final']
            assert posts==[] and gets==[] and outside==[] and errors==[]
        finally:browser.close()

def test_window_switch_keeps_delivery_focus_scroll_and_keyboard_action(voice_world,monkeypatch):
    root,posts,gets,_,reveals=setup(voice_world,monkeypatch)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page,panel,button,errors,outside=open_board(browser,url,root)
            before=page.evaluate('()=>({page:scrollY,viewer:document.getElementById("viewer").scrollTop})')
            other=browser.new_page();other.goto('about:blank');other.bring_to_front();page.bring_to_front()
            with page.expect_response(url+f'/api/project/{root.name}/state'):
                page.evaluate('()=>window.dispatchEvent(new Event("focus"))')
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            expect(button).to_be_focused()
            after=page.evaluate('()=>({page:scrollY,viewer:document.getElementById("viewer").scrollTop})')
            assert abs(after['page']-before['page'])<=1 and abs(after['viewer']-before['viewer'])<=1
            with page.expect_response(url+f'/api/project/{root.name}/delivery/invented-final/reveal',timeout=5000):page.keyboard.press('Enter')
            assert reveals==['invented-final'] and posts==[] and gets==[] and outside==[] and errors==[]
        finally:browser.close()
