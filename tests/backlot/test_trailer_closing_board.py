"""Real board browser regressions on invented, temporary productions."""
import json,os,socket,subprocess,sys,time,urllib.request
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from lib.checkpoint import init_project
from lib import gates,receipts
from lib.pipeline_pin import migration_record
from lib.pipeline_loader import manifest_digest
from lib.canonical_json import record_sha256
from lib.trailer_contract import make_contract
from tests.backlot.test_gates_state import gate_world


def pin(root,version='1.6'):
    record=migration_record('authored-film',version,manifest_digest('authored-film@'+version))
    with gates.handler_context():
        token=gates.mint_gate_token(root.name,'pipeline','pipeline:authored-film',record_sha256(record),user_response='approve')
    return receipts.record_human_approval(root,root.name,'pipeline','pipeline:authored-film',record,token,
             'pipeline_migration',entity_id='authored-film',envelope={'supersedes_receipt_id':None})


@pytest.fixture
def closing_board(tmp_path,gate_world):
    projects=gate_world['projects']
    for name in ['trailer','older','broken']:
        if name=='broken':
            root=projects/name;root.mkdir();pin(root)
            (root/'project.yaml').write_text("pipeline: authored-film\nversion: '1.6'\n")
            (root/'approvals.jsonl').write_text((root/'approvals.jsonl').read_text()+'{"forged":true}\n')
        else:
            root=init_project(name,title='The Lantern Keeper and the Museum of Unfinished Letters — '+ 'A Long Invented Mystery Title '*6,
                pipeline_type='authored-film@1.6' if name=='trailer' else 'authored-film@1.5',pipeline_dir=projects,
                **({'target_duration_seconds':28} if name=='trailer' else {}))
            pin(root,'1.6' if name=='trailer' else '1.5')
        script={'version':'1.0','title':'Night Window','total_duration_seconds':28,
                'sections':[{'id':'s1','text':'An empty room holds one light.','start_seconds':0,'end_seconds':28}]}
        cp={'stage':'script','status':'completed' if name=='broken' else 'awaiting_human','human_approved':name=='broken',
            'pipeline_type':'authored-film@1.6' if name!='older' else 'authored-film','artifacts':{'script':script}}
        (root/'checkpoint_script.json').write_text(json.dumps(cp))
    # Reuse the existing invented look/portrait packets; only their board needs the trailer reference.
    p=gate_world['project'];m=json.loads((p/'project.json').read_text());m.update(production_kind='trailer',trailer=make_contract(28),pipeline_type='authored-film@1.6',title=json.loads((projects/'trailer/project.json').read_text())['title'])
    (p/'project.json').write_text(json.dumps(m))
    (p/'.run-lease').write_text(json.dumps({'pid':99999999,'hostname':socket.gethostname()}))
    from tests.lib.look_lock_helpers import pin_project
    pin_project(p,'1.6',supersedes=gate_world['prior_migration']['receipt_id'])
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ,OPENMONTAGE_PROJECTS_DIR=str(projects),OPENMONTAGE_GATES_DIR=str(gate_world['external_gates']))
    code="from backlot.server import create_app;import uvicorn;uvicorn.run(create_app(port="+str(port)+"),host='127.0.0.1',port="+str(port)+",log_level='error')"
    process=subprocess.Popen([sys.executable,'-c',code],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    url=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url+'/api/health',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.set_default_timeout(5000)
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            yield page,url,errors,gate_world
            browser.close()
    finally:
        process.terminate();process.wait(timeout=15)


@pytest.mark.parametrize('name',['trailer','older'])
def test_full_script_opens_on_real_board(closing_board,name):
    page,url,errors,_=closing_board
    page.goto(url+'/p/'+name+'?static=1');page.locator('.script-preview').click()
    assert page.locator('#modal.open').is_visible()
    assert 'An empty room' in page.locator('#modal').inner_text()
    assert not errors


@pytest.mark.parametrize('kind',['look_lock','headshot'])
def test_existing_trailer_gate_view_and_signing_are_reachable(closing_board,kind):
    page,url,errors,world=closing_board
    page.goto(url+'/p/p?static=1')
    page.get_by_role('button',name=f"Inspect {kind} gate {world['rids'][kind]}",exact=True).click()
    page.wait_for_selector('.gate-detail')
    page.get_by_role('button',name='Start signing',exact=True).wait_for()
    assert page.get_by_role('button',name='Start signing',exact=True).is_visible()
    assert page.get_by_role('button',name='Start signing',exact=True).is_enabled()
    assert 'Not available yet' in page.locator('.counter').inner_text()
    assert not errors


def test_missing_marker_and_broken_history_never_shows_completion(closing_board):
    page,url,errors,_=closing_board
    page.goto(url+'/p/broken?static=1')
    page.get_by_text("This trailer's approval history can't be verified, so nothing can be completed. Nothing has been changed.",exact=True).wait_for()
    assert page.locator('.counter .stage.done').count()==0
    state=page.request.get(url+'/api/project/broken/state').json()
    assert state['trailer_damaged'] and not any(s['status']=='completed' for s in state['stages'])
    assert not errors


@pytest.mark.parametrize('project',['trailer','p'])
def test_long_title_header_never_overlaps_viewer_at_900(closing_board,project):
    page,url,errors,_=closing_board
    page.set_viewport_size({'width':900,'height':900})
    page.goto(url+'/p/'+project+'?static=1');page.wait_for_selector('.counter')
    box=page.locator('.bar').bounding_box();viewer=page.locator('.viewer').bounding_box()
    assert viewer['y']>=box['y']+box['height']-1
    counter=page.locator('.counter').bounding_box()
    assert counter['y']+counter['height']<=viewer['y']+1
    assert page.locator('.bar').evaluate('(e)=>e.scrollHeight<=e.clientHeight+1')
    assert not errors
