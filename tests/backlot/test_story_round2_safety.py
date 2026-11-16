"""Damaged story storage refuses plainly through the real routes and signer."""
import base64,copy,json,os,subprocess,sys
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright,expect
from tests.lib.test_story_ingest import root
from tests.lib.look_lock_helpers import approve_request
from tests.backlot.test_early_paid_readiness import isolated_standins,start_browser,shot_picture
from tests.backlot.test_footage_controls_browser import browser_board
from tests.backlot.test_story_round2 import paste

@pytest.mark.parametrize('fault',['source-link','folder-link','checkpoint-link','pending-link','source-missing','outside','checkpoint-json','request-json'])
def test_storage_faults_refuse_through_routes_preview_and_signer_without_approval(root,tmp_path,monkeypatch,capsys,fault):
    from lib import story_ingest as story
    from lib.trailer_contract import signed_trailer_pin
    from scripts import gate_approve
    from backlot.state import build_gate_detail
    import backlot.server as server
    made=story.supply(root,b'An invented gallery waits.\n');req=made['gate'];source=root/made['path']
    excluded=source if fault in ('source-link','source-missing') else root/'sources/story' if fault=='folder-link' else root/'checkpoint_canon_ingest.json' if fault=='checkpoint-link' else root/'.gate-requests'/(req['request_id']+'.json') if fault=='pending-link' else None
    destination=tmp_path/'copy';destination.mkdir();clone=destination/root.name
    copytree(root,clone,ignore=lambda folder,names:{excluded.name} if excluded is not None and Path(folder)==excluded.parent else set())
    if fault in ('source-link','folder-link','checkpoint-link','pending-link'):
        (clone/excluded.relative_to(root)).symlink_to(excluded,target_is_directory=fault=='folder-link')
    elif fault=='outside':
        cp=json.loads((clone/'checkpoint_canon_ingest.json').read_text());meta=cp['artifacts']['canon_packet']['metadata']['verbatim_story']
        meta['sha256']='../../../outside';meta['path']='sources/story/../../../outside.txt'
        (clone/'checkpoint_canon_ingest.json').write_text(json.dumps(cp))
    elif fault=='checkpoint-json':(clone/'checkpoint_canon_ingest.json').write_text('{broken')
    elif fault=='request-json':(clone/'.gate-requests'/(req['request_id']+'.json')).write_text('{broken')
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',destination)
    monkeypatch.setattr('lib.events.PROJECTS_DIR',destination)
    monkeypatch.setattr(server,'PROJECTS_DIR',destination)
    assert signed_trailer_pin(clone) is not None
    before=(clone/'approvals.jsonl').read_bytes()
    with TestClient(server.create_app(port=4938,capability_token='invented-story-fault'),raise_server_exceptions=False) as client:
        headers={'origin':'http://127.0.0.1:4938','x-frontlot-capability':'invented-story-fault'}
        for method,path in [('get',''),('post','/apply')]:
            response=getattr(client,method)(f'/api/project/{clone.name}/story'+path,headers=headers)
            assert response.status_code==400,response.text
            detail=response.json()['detail']
            assert 'story' in detail.lower() or 'request' in detail.lower()
            assert not any(v in detail for v in ('Traceback','Expecting value','PathSafetyError',str(clone),str(root)))
    preview=build_gate_detail(clone,req['request_id'])
    explanation=preview.get('error') or preview.get('packet',{}).get('error')
    assert explanation and explanation!='gate evidence is malformed'
    assert gate_approve.main(['--project',clone.name,'--projects-dir',str(destination),'--request',req['request_id'],'--dry-run'])==2
    printed=capsys.readouterr();output=printed.out+printed.err
    assert 'Traceback' not in output and 'PathSafetyError' not in output and str(clone) not in output
    assert (clone/'approvals.jsonl').read_bytes()==before
    assert source.read_bytes()==b'An invented gallery waits.\n'

@pytest.mark.parametrize('payload,plain',[
    (b'{broken','could not be read'),
    (b'\xff','could not be read'),
    (b'{"text":"\\ud800"}','could not be read'),
    (b'{"file":{"name":"story.txt","base64":"!not-base64!"}}','This text file could not be read. Choose it again.'),
    (b'{"file":{"name":"story.txt","base64":"\\u00e9"}}','This text file could not be read. Choose it again.'),
])
def test_parser_failures_do_not_print_internal_errors(root,monkeypatch,payload,plain):
    import backlot.server as server
    monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    with TestClient(server.create_app(port=4939,capability_token='invented-parse'),raise_server_exceptions=False) as client:
        response=client.post(f'/api/project/{root.name}/story',content=payload,
            headers={'origin':'http://127.0.0.1:4939','x-frontlot-capability':'invented-parse','content-type':'application/json'})
        assert response.status_code==400
        assert plain in response.json()['detail']
        assert not any(v in response.json()['detail'] for v in ('Non-base64','Expecting value','surrogates','codec'))
    assert not (root/'checkpoint_canon_ingest.json').exists()

def test_non_json_story_answer_is_plain_and_not_a_page_error(root,monkeypatch,isolated_standins):
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.route('**/api/project/'+root.name+'/story',lambda r:r.fulfill(status=500,content_type='text/plain',body='Invented unreadable server answer'))
            page.goto(url);panel=page.get_by_role('region',name='Trailer story',exact=True)
            expect(panel.get_by_role('alert')).to_contain_text('could not be read')
            assert not any(v in panel.inner_text() for v in ('Unexpected token','valid JSON','JSON.parse'))
            assert not page.errors and all(not v for v in isolated_standins.values())
        finally:browser.close()

@pytest.mark.parametrize('ending',['\r\n','\r'])
def test_multiline_protected_and_spoiler_passages_keep_source_endings(root,monkeypatch,isolated_standins,ending):
    protected='Keep the light on.'+ending+'The gallery waits.'
    spoiler='The keeper is the thief.'+ending+'The room was never empty.'
    source='An invented gallery.'+ending+protected+ending+spoiler+ending
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url);panel=page.get_by_role('region',name='Trailer story',exact=True)
            paste(panel.get_by_label('Exact story text',exact=True),source)
            panel.get_by_role('button',name='Add protected wording',exact=True).click()
            paste(panel.get_by_label('Protected wording 1',exact=True),protected)
            panel.get_by_role('button',name='Add spoiler passage',exact=True).click()
            panel.get_by_label('Spoiler passage 1',exact=True).fill(spoiler.replace(ending,'\n'))
            panel.get_by_role('button',name='Save story for review',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Saved')
            cp=json.loads((root/'checkpoint_canon_ingest.json').read_text())['artifacts']['canon_packet']
            assert cp['protected_lines'][0]['line']==protected and cp['locks'][0]['decision']==spoiler
            panel.get_by_role('button',name='Review and sign this story',exact=True).click()
            review=page.get_by_role('region',name='Story approval',exact=True)
            expect(review.locator('blockquote')).to_have_count(2)
            assert review.locator('blockquote').all_text_contents()==[protected,spoiler]
            shot_picture(page,'multiline-passages-'+('windows' if ending=='\r\n' else 'old-mac'),review.get_by_role('heading').first)
            assert all(not v for v in isolated_standins.values()) and not page.errors
        finally:browser.close()

def test_story_dropdown_is_short_and_each_non_source_row_has_its_warning(root,monkeypatch,isolated_standins):
    from lib import story_ingest as story,trailer_edit as edit
    from tests.lib.test_trailer_edit import paper
    approve_request(story.supply(root,('An invented gallery waits in the cold. '*200).encode())['gate'],root)
    value=paper();value['shots'][0]['duration_frames']=672
    value['lines']=[{'text':'An honest new spoken line.','start_frame':0,'duration_frames':24}]
    value['cards']=[{'text':'Come inside.','start_frame':24,'duration_frames':24}]
    edit.save_revision(root,value,None)
    sources=edit.paper_state(root)['sources'];assert len(sources[0]['label'])<120 and sources[0]['label'].startswith('Supplied story')
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url);panel=page.get_by_role('region',name='Trailer paper cut',exact=True)
            for kind in ('Line 1','Card 1'):
                row=panel.get_by_role('group',name=kind,exact=True)
                expect(row).to_contain_text('not an exact passage')
                assert all(len(v)<120 for v in row.get_by_label('Story material',exact=True).locator('option').all_text_contents())
            shot_picture(page,'warnings-beside-lines-and-cards',panel.get_by_role('group',name='Line 1',exact=True))
            assert not page.errors
        finally:browser.close()

def test_resave_retires_only_unsigned_story_requests_never_signed_or_other_requests(root,monkeypatch):
    from lib import story_ingest as story,media_spending
    from scripts import gate_approve
    from lib.run_common import request_paths
    first=story.supply(root,b'First invented source.')['gate']
    # A real signature can exist before the pending request is archived.
    with monkeypatch.context() as patch:
        patch.setattr(gate_approve,'_complete_done',lambda *a:None)
        approve_request(first,root)
    signed_path=request_paths(root,first['request_id'])['pending'];assert signed_path.exists()
    other=media_spending.gate_request(root,'config','project_config',{'config_sha256':'a'*64},'Separate unsigned settings.')
    before=(root/'approvals.jsonl').read_bytes()
    second=story.supply(root,b'Second invented source.')['gate']
    foreign=copy.deepcopy(second);foreign.update(project_id='another-invented-production',request_id='foreign-story')
    foreign_path=root/'.gate-requests/foreign-story.json';foreign_path.write_text(json.dumps(foreign))
    third=story.supply(root,b'Third invented source.')['gate']
    assert request_paths(root,second['request_id'])['abandoned'].is_file()
    assert not request_paths(root,second['request_id'])['pending'].exists()
    assert signed_path.is_file() and request_paths(root,other['request_id'])['pending'].is_file()
    assert foreign_path.is_file() and request_paths(root,third['request_id'])['pending'].is_file()
    assert (root/'approvals.jsonl').read_bytes()==before

def test_board_test_cleanup_reaps_a_signer_that_exits_during_attachment(tmp_path,monkeypatch):
    from tests.backlot import tty_helpers
    folder=tmp_path/'closing-signature';folder.mkdir()
    process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(0.2)'],env=dict(os.environ))
    row={'pid':process.pid,'project':'invented-closing','request_id':'invented-request','session_id':'invented-session'}
    (folder/'invented.session.json').write_text(json.dumps(row))
    monkeypatch.setattr('backlot.tty.sessions_dir',lambda:folder)
    def closed(*a,**k):raise BrokenPipeError('Invented signer finished after its receipt was recorded.')
    monkeypatch.setattr(tty_helpers,'attach_when_free',closed)
    try:
        tty_helpers.finish_board_signers(SimpleNamespace(state=SimpleNamespace(tty_registry=SimpleNamespace(spawned=[process]))))
        assert process.poll()==0
    finally:process.wait(timeout=3)
