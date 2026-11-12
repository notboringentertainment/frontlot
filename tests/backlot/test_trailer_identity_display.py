"""Unverifiable older productions retain their exact base presentation."""
import copy,json,os,shutil,socket,subprocess,sys,time,urllib.request
from pathlib import Path
from contextlib import contextmanager
import pytest
from playwright.sync_api import sync_playwright
from tests.backlot.test_gates_state import gate_world
from tests.backlot.test_trailer_closing_board import pin
from lib import checkpoint
from lib.receipts import ReceiptChainError
from lib.trailer_contract import make_contract

BASE_SOURCE=Path(__file__).parent/'fixtures/base_board_state.py.txt'


def normalize(value):
    if isinstance(value,dict):return {k:normalize(v) for k,v in value.items() if k not in {'snapshot_at','last_activity','stalled_minutes'}}
    if isinstance(value,list):return [normalize(v) for v in value]
    return value


def base_projection(root,gates,tmp_path):
    # Exact base source from local Git history, checked against its frozen digest.
    code="""import hashlib,json,subprocess,sys,types
from pathlib import Path
source=subprocess.check_output(['git','show','08b095a758c0e433ab5d462a5e2994b4ef4dd7a6:backlot/state.py'])
assert hashlib.sha256(source).hexdigest()==Path(sys.argv[1]).read_text().strip()
m=types.ModuleType('base_board_state');m.__file__=str(Path.cwd()/'backlot/state.py')
exec(compile(source,m.__file__,'exec'),m.__dict__)
p=Path(sys.argv[2]);print(json.dumps({'board':m.load_board_state(p),'film':m.summarize_project(p)}))
"""
    env=dict(os.environ,OPENMONTAGE_GATES_DIR=str(gates))
    r=subprocess.run([sys.executable,'-c',code,str(BASE_SOURCE.resolve()),str(root)],env=env,text=True,capture_output=True,check=True)
    return json.loads(r.stdout)


@pytest.fixture(params=['renamed','different_signer'])
def older_case(request,gate_world,tmp_path,monkeypatch):
    w=gate_world
    if request.param=='renamed':
        root=w['projects']/'renamed-window';shutil.copytree(w['project'],root)
        signing=w['external_gates']
    else:
        root=w['project'];signing=tmp_path/'different-signing-folder';signing.mkdir()
    monkeypatch.setenv('OPENMONTAGE_GATES_DIR',str(signing))
    return root,signing,request.param


def test_unverifiable_older_film_and_board_match_base(older_case,tmp_path):
    from backlot.state import load_board_state,summarize_project
    root,signing,_=older_case
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    expected=base_projection(root,signing,tmp_path)
    actual={'board':load_board_state(root),'film':summarize_project(root)}
    assert normalize(actual)==normalize(expected)
    assert not actual['board'].get('is_trailer') and not actual['film'].get('is_trailer')
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises((checkpoint.CheckpointValidationError, ReceiptChainError)):
        # The reader/writer pin refusal remains authoritative, not the editable marker.
        checkpoint.write_checkpoint(root.parent,root.name,'script','completed',{},human_approved=True)


@pytest.mark.parametrize('marker',['missing','ordinary','cinematic','trailer'])
def test_signed_creation_evidence_survives_broken_history(tmp_path,marker):
    from backlot.state import load_board_state
    root=tmp_path/'p';root.mkdir();pin(root)
    (root/'project.yaml').write_text('pipeline: authored-film\n')
    if marker!='missing':
        m={'pipeline_type':'cinematic' if marker=='cinematic' else 'authored-film'}
        if marker=='trailer':m.update(production_kind='trailer',trailer=make_contract(28))
        (root/'project.json').write_text(json.dumps(m))
    (root/'approvals.jsonl').write_text((root/'approvals.jsonl').read_text()+'{"forged":true}\n')
    (root/'checkpoint_script.json').write_text(json.dumps({'stage':'script','pipeline_type':'authored-film','status':'completed','human_approved':True,'artifacts':{}}))
    s=load_board_state(root)
    assert s['is_trailer'] and "approval history can't be verified" in s['trailer_damaged']
    assert not any(x['status']=='completed' for x in s['stages'])
    with pytest.raises((checkpoint.CheckpointValidationError, ReceiptChainError)):checkpoint.write_checkpoint(root.parent,'p','script','completed',{},human_approved=True)


@contextmanager
def display_server(root,signing):
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ,OPENMONTAGE_PROJECTS_DIR=str(root.parent),OPENMONTAGE_GATES_DIR=str(signing),FRONTLOT_CLAUDE='/nonexistent/invented-assistant')
    code=f"from backlot.server import create_app;import uvicorn;uvicorn.run(create_app(port={port}),host='127.0.0.1',port={port},log_level='error')"
    proc=subprocess.Popen([sys.executable,'-c',code],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    url=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:urllib.request.urlopen(url+'/api/health',timeout=1);break
            except OSError:time.sleep(.1)
        yield url
    finally:proc.terminate();proc.wait(timeout=15)


def test_unverifiable_older_browser_matches_base_controls_and_cards(older_case,tmp_path):
    root,signing,_=older_case
    expected=base_projection(root,signing,tmp_path)
    base_scripts={name:subprocess.check_output(['git','show',f'08b095a758c0e433ab5d462a5e2994b4ef4dd7a6:backlot/ui/{name}'],text=True) for name in ['board.js','library.js']}
    with display_server(root,signing) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            views=[]
            for reference in [False,True]:
                page=browser.new_page(viewport={'width':1440,'height':900});page.set_default_timeout(5000);errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') and r.request.method=='GET' else r.abort())
                if reference:
                    for name,source in base_scripts.items():
                        page.route('**/ui/'+name+'*',lambda r,_request,body=source:r.fulfill(content_type='text/javascript',body=body))
                    page.route('**/api/project/'+root.name+'/state',lambda r:r.fulfill(json=expected['board']))
                    page.route('**/api/projects',lambda r:r.fulfill(json=[expected['film']]))
                page.goto(url+'/p/'+root.name+'?static=1');page.wait_for_selector('.stage-select')
                board=page.evaluate('''() => ({header:document.querySelector('.bar').innerText,
                  stages:[...document.querySelectorAll('.stage-select')].map(e=>e.innerText),
                  controls:[...document.querySelectorAll('button,a,summary,input,textarea')].map(e=>({tag:e.tagName,text:(e.innerText||e.getAttribute('aria-label')||'').trim(),disabled:!!e.disabled})),
                  cards:[...document.querySelectorAll('.need,.approval-review,.script-card,.clip')].map(e=>e.innerText)})''')
                page.goto(url+'/');page.wait_for_selector('.lib-card')
                film=page.locator(f'.lib-card[href="/p/{root.name}"]').inner_text()
                assert not errors
                views.append({'board':board,'film':film})
                page.close()
            print({'case':older_case[2],'head_controls':len(views[0]['board']['controls']),'base_controls':len(views[1]['board']['controls']),'head_cards':len(views[0]['board']['cards']),'base_cards':len(views[1]['board']['cards'])})
            assert views[0]==views[1]
        finally:browser.close()


def test_no_remaining_trailer_evidence_uses_base_display_but_writer_refuses(tmp_path):
    from backlot.state import load_board_state
    root=tmp_path/'p';root.mkdir();pin(root)
    (root/'project.yaml').write_text('pipeline: authored-film\n')
    # A new scratch history has no creation row. The signed chain still detects its loss.
    (root/'approvals.jsonl').write_text('{"forged":true}\n')
    s=load_board_state(root)
    assert not s.get('is_trailer')
    with pytest.raises(ReceiptChainError):checkpoint.write_checkpoint(root.parent,'p','script','completed',{},human_approved=True)
