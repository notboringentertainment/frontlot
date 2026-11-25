"""Continue trailers produced by the earlier app without retiring their source reviews."""
import json,os,uuid,re
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.lib.test_journey_round2 import older_cut,film,provider,looks,workspace,sign,human_terminal
from tests.backlot.test_voice_panel_browser import board,short_signing_folder

@pytest.fixture
def earlier(request,monkeypatch):
    folder=os.environ.get('JOURNEY_R3_FIXTURES')
    if folder:
        entry=json.loads((Path(folder)/(request.param+'.json')).read_text());assert entry['producer']=='56ad1a9'
        root=Path(entry['root']);monkeypatch.setenv('OPENMONTAGE_GATES_DIR',entry['gates'])
        monkeypatch.setattr('lib.paths.PROJECTS_DIR',root.parent)
        monkeypatch.setattr('lib.events.PROJECTS_DIR',root.parent)
    else:root=request.getfixturevalue('older_cut')[0]
    calls=[]
    def refuse(*args,**kwargs):calls.append(1);raise AssertionError('No service contact belongs in this continuation.')
    monkeypatch.setattr('requests.post',refuse);monkeypatch.setattr('requests.get',refuse)
    yield root
    assert calls==[]

def sources_still_kept(root,saved):
    from lib import audio_workspace as audio,trailer_titles as titles
    assert all(titles.status(root,c)=='Signed by you' for c in saved['timeline'].get('cards',[]))
    for row in saved['timeline']['audio']:audio.require_accepted(root,row['take_id'])

@pytest.mark.parametrize('earlier',['compare'],indirect=True)
def test_older_comparison_is_a_true_no_op(earlier,monkeypatch):
    from lib import trailer_edit as edit,story_ingest,trailer_delivery as delivery
    root=earlier;saved=edit.read_revision(root);raw=(root/'artifacts/edit_decisions.json').read_bytes()
    script=root/'checkpoint_script.json';upstream=script.read_bytes() if script.exists() else None
    def no_commit(*a,**k):pytest.fail('An unchanged older paper cut must not commit a new version.')
    monkeypatch.setattr(edit,'_commit',no_commit)
    story_ingest.check_paper(root,saved['edit_revision_sha256'])
    assert (root/'artifacts/edit_decisions.json').read_bytes()==raw
    assert (script.read_bytes() if script.exists() else None)==upstream
    assert delivery.rough_state(root)=='Signed by you'
    sources_still_kept(root,saved)

@pytest.mark.parametrize('earlier',['resolution'],indirect=True)
def test_account_followup_does_not_create_an_upstream_script_change(earlier,monkeypatch):
    from lib import trailer_records as records,trailer_edit as edit,trailer_delivery as delivery
    root=earlier;saved=edit.read_revision(root)
    script=root/'checkpoint_script.json';upstream=script.read_bytes() if script.exists() else None
    def no_publish(*a,**k):pytest.fail('Do not publish derived paper as an older upstream checkpoint.')
    monkeypatch.setattr(records,'_publish',no_publish)
    records.after_signature(root,'audio_resolution')
    assert (script.read_bytes() if script.exists() else None)==upstream
    assert edit.paper_state(root)['usable_downstream']
    assert delivery.rough_state(root)=='Signed by you'
    sources_still_kept(root,saved)

@pytest.mark.parametrize('earlier',['save'],indirect=True)
def test_older_cut_save_restore_and_real_changes(earlier):
    from lib import trailer_edit as edit,cutting_view as cutting,trailer_delivery as delivery
    root=earlier;saved=edit.read_revision(root);value=cutting.editable(saved['timeline'])
    value['picture'][0]['colour']['brightness']+=5
    newer=cutting.save_cut(root,value,saved['edit_revision_sha256'])
    assert newer['inputs']==saved['inputs']
    sources_still_kept(root,newer)
    restored=edit.restore_revision(root,saved['edit_revision_sha256'],newer['edit_revision_sha256'])
    assert not restored.get('paper_cut_pending')
    assert edit.paper_state(root)['usable_downstream']
    sources_still_kept(root,restored)
    for stage in ('canon_ingest','script'):
        path=root/f'checkpoint_{stage}.json';before=path.read_bytes() if path.exists() else None
        path.write_text(json.dumps({'artifacts':{'invented_change':'A different story.'}}))
        assert not edit.paper_state(root)['usable_downstream']
        with pytest.raises(ValueError):delivery.require_rough(root)
        with pytest.raises(ValueError):cutting.save_cut(root,value,restored['edit_revision_sha256'])
        if before is not None:path.write_bytes(before)

@pytest.mark.parametrize('earlier',['browser'],indirect=True)
def test_older_writer_can_save_in_cutting_view(earlier,monkeypatch):
    from lib import trailer_edit as edit
    root=earlier
    # Re-reading this same earlier production after the failing-first walk
    # retains its first isolation record rather than overwriting it.
    original_open=Path.open
    def isolated_open(path,mode='r',*args,**kwargs):
        if path==root/'browser-isolation.json' and mode=='x':path=root/('browser-isolation-r3-'+uuid.uuid4().hex+'.json')
        return original_open(path,mode,*args,**kwargs)
    monkeypatch.setattr(Path,'open',isolated_open)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Cutting workspace',exact=True).click()
            panel=page.get_by_role('region',name='Cutting view',exact=True)
            field=panel.get_by_label('Brighter or darker for picture 1',exact=True)
            expect(field).to_be_visible(timeout=120000)
            field.press('ArrowRight')
            with page.expect_response(lambda r:r.url.endswith('/cut') and r.request.method=='POST',timeout=180000) as response:
                panel.get_by_role('button',name='Save cut',exact=True).click()
            assert response.value.ok,response.value.text()
            expect(panel).to_contain_text('saved. Nothing was approved.',timeout=30000)
            sources_still_kept(root,edit.read_revision(root))
            folder=os.environ.get('JOURNEY_R3_PICTURES')
            prefix=os.environ.get('JOURNEY_R3_PREFIX','r3-older')
            if folder:page.screenshot(path=str(Path(folder)/(prefix+'-cut-saved.png')))
            page.get_by_role('button',name='Paper cut workspace',exact=True).click()
            paper=page.get_by_role('region',name='Trailer paper cut',exact=True)
            expect(paper).to_contain_text('Whole-paper review is optional')
            expect(paper).not_to_contain_text(re.compile(r'\bnull\b'))
            if folder:
                paper.get_by_role('status',name='Whole paper-cut approval',exact=True).scroll_into_view_if_needed()
                page.screenshot(path=str(Path(folder)/(prefix+'-paper-wording.png')))
            assert errors==[]
        finally:browser.close()
