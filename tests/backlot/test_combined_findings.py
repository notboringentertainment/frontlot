"""The bounded fixes required when the reviewed branches are joined."""
import subprocess
import pytest
from playwright.sync_api import expect
from tests.backlot.test_delivery_ui_later import screen
from tests.backlot.test_voice_panel import voice_world,project,priced,http
from tests.backlot.test_voice_panel_browser import short_signing_folder
from tests.backlot.test_voice_panel_browser import board
from playwright.sync_api import sync_playwright

def test_final_signature_must_be_recorded_before_finder_is_next(screen):
    page,panel=screen
    page.evaluate("()=>{saved.exports[0].status='Approved by you';saved.exports[0].approval_recorded=false;module.renderDelivery({project_id:'invented-ui'},()=>{});}")
    expect(panel.get_by_label('Next delivery step')).to_contain_text('Record the signed final approval')
    page.evaluate("()=>{saved.exports[0].approval_recorded=true;module.renderDelivery({project_id:'invented-ui'},()=>{});}")
    expect(panel.get_by_label('Next delivery step')).to_contain_text('Show approved file in Finder')

def test_lost_release_does_not_freeze_delivery(screen):
    page,panel=screen
    control=panel.get_by_role('button',name='Refresh delivery',exact=True)
    control.dispatch_event('pointerdown',{'button':0,'pointerId':19,'isPrimary':True})
    page.evaluate("()=>{saved.exports[0].status='Approved by you';module.renderDelivery({project_id:'invented-ui'},()=>{});}")
    expect(panel.locator('[data-delivery]').first).to_contain_text('Approved by you',timeout=3500)

@pytest.mark.parametrize('operation',['export','saved'])
@pytest.mark.parametrize('problem',['missing','broken'])
def test_missing_file_inspector_is_named_before_cut_is_checked(tmp_path,monkeypatch,operation,problem):
    from lib import trailer_delivery as d,programs,footage_requests
    def require(name):
        if name=='ffprobe' and problem=='missing':raise ValueError('ffprobe is missing.')
        return '/invented/programs/'+name
    def run(args,**kw):
        if args[0].endswith('/ffprobe'):raise subprocess.CalledProcessError(1,args)
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(programs,'require',require)
    monkeypatch.setattr(programs,'run',run)
    monkeypatch.setattr(d,'require_rough',lambda *a:(_ for _ in ()).throw(ValueError('A picture is no longer usable.')))
    monkeypatch.setattr(footage_requests,'require_trailer',lambda *a:None)
    monkeypatch.setattr(d.trailer_edit,'read_revision',lambda *a:{'cutting_view':True,'timeline':{'mix':{}}})
    monkeypatch.setattr(d.trailer_edit,'paper_state',lambda *a:{'usable_downstream':True})
    monkeypatch.setattr(d.cut,'validate_cut',lambda *a,**kw:(_ for _ in ()).throw(ValueError('A picture is no longer usable.')))
    monkeypatch.setattr(d.cut,'editable',lambda value:value)
    with pytest.raises(ValueError,match='ffprobe (is missing|could not run)'):
        d.export(tmp_path,'invented') if operation=='export' else d._saved(tmp_path)

def test_lost_release_does_not_freeze_audio(voice_world,monkeypatch):
    root,posts,_=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            audio=page.get_by_role('region',name='Trailer audio')
            expect(audio.locator('.voice-budget')).to_be_visible()
            audio.get_by_role('button',name='Load account voices').dispatch_event('pointerdown',{'button':0,'pointerId':21,'isPrimary':True})
            def changed(route):
                data=route.fetch().json();data['record_warnings']=['Invented update after a lost release.'];route.fulfill(json=data)
            page.route('**/voice',changed)
            page.evaluate("id=>import('/ui/voice.js').then(m=>m.refreshVoice(id))",root.name)
            expect(audio).to_contain_text('Invented update after a lost release.',timeout=3500)
            assert not posts
        finally:page.unroute_all(behavior='ignoreErrors');browser.close()
