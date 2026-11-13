"""A writer makes, signs and decides invented footage without a terminal."""
import json,os,socket,threading,time,tempfile
from contextlib import contextmanager
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from tests.tools.test_signed_footage import film as original_film,provider


@pytest.fixture
def film(tmp_path,monkeypatch):
    # Unix signing sockets need a short pathname on macOS. Both locations and
    # the fallback home remain temporary and are checked before the board.
    short=Path(tempfile.mkdtemp(prefix='fc-',dir=os.environ.get('OPENMONTAGE_HOME') or '/tmp'))
    home=short/'h';home.mkdir();gates=short/'g';gates.mkdir()
    monkeypatch.setenv('HOME',str(home))
    monkeypatch.setenv('OPENMONTAGE_GATES_DIR',str(gates))
    return original_film.__wrapped__(tmp_path,monkeypatch)


@contextmanager
def browser_board(root,monkeypatch):
    import uvicorn
    from backlot import server
    from tests import conftest as guard
    assert not guard._unsafe_locations(os.environ)
    monkeypatch.setenv('OPENMONTAGE_PROJECTS_DIR',str(root.parent))
    monkeypatch.setenv('FRONTLOT_CLAUDE','/nonexistent/invented-assistant')
    monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    app=server.create_app(port=port,capability_token='invented-browser-writer')
    service=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    worker=threading.Thread(target=service.run);worker.start()
    try:
        for _ in range(100):
            if service.started:break
            time.sleep(.05)
        assert service.started
        preview=os.environ.get('FRONTLOT_FOOTAGE_CONTROLS_PREVIEW_FILE')
        if preview:
            with Path(preview).open('x') as handle:handle.write(f'http://127.0.0.1:{port}/p/{root.name}?static=1#k=invented-browser-writer')
        yield f'http://127.0.0.1:{port}/p/{root.name}?static=1#k=invented-browser-writer'
    finally:
        service.should_exit=True;worker.join(timeout=20)
        assert not worker.is_alive(),'The invented board did not stop.'


def picture(page,name,tmp_path):
    destination=os.environ.get('FRONTLOT_FOOTAGE_CONTROLS_PICTURES')
    target=Path(destination) if destination else tmp_path
    target.mkdir(parents=True,exist_ok=True)
    path=target/(name+'.png')
    assert not path.exists()
    panel=page.get_by_role('article',name='Shot 1 footage',exact=True)
    if panel.count():
        focus=panel.locator('.take-tile').last if name in ('take-waiting','kept','declined','recovered-keep') else panel.get_by_role('status').first
        if name.startswith('missing-'):focus=panel.get_by_role('button',name='Create footage request',exact=True)
        if name=='unknown-charge':focus=panel.get_by_role('button',name='Review account outcome',exact=True)
        if name=='approved-look':focus=panel.locator('.look-card').first
        if name=='price-bound':focus=panel.locator('.spending-card').first
        # Scrolling is synchronous in the page. Waiting for the old element
        # handle to become stable races the board's live refresh and quote.
        focus.evaluate('(node)=>node.scrollIntoView({block:"center",behavior:"instant"})')
    page.screenshot(path=str(path),full_page=False)


def sign(page,label):
    # The existing signing drawer starts its own local signer, not a fake yes.
    page.locator('#gate-detail .gate-primary').click()
    confirm=page.get_by_role('button',name={'request':'Sign and make this clip','decision':'Sign this decision','account':'Approve and sign'}[label],exact=True)
    try:confirm.wait_for(timeout=20000)
    except Exception:
        pytest.fail('Signer did not offer its question: '+page.locator('#gate-sign-panel').inner_text()+' '+page.locator('#gate-terminal-message').inner_text())
    confirm.click()
    page.locator('[aria-label="Footage workspace"]').wait_for(timeout=20000)


def test_writer_complete_request_keep_decline_and_retake(film,provider,monkeypatch,tmp_path):
    root,shot=film
    from tools.video import _shared
    arrive=threading.Event();waiting=threading.Event();original_wait=_shared.fal_queue_wait
    def wait(*a,**k):
        waiting.set()
        assert arrive.wait(timeout=45),'The invented clip was not released.'
        return original_wait(*a,**k)
    monkeypatch.setattr(_shared,'fal_queue_wait',wait)
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(url)
            panel=page.get_by_role('article',name='Shot 1 footage',exact=True)
            panel.get_by_text('Nothing yet',exact=True).wait_for()
            panel.get_by_label('Clip length in seconds').fill('5')
            panel.get_by_label('Clip length in seconds').press('Tab')
            panel.get_by_text('Highest charge: $0.5000',exact=False).wait_for()
            picture(page,'nothing-yet',tmp_path)
            picture(page,'approved-look',tmp_path)
            picture(page,'price-bound',tmp_path)
            assert panel.locator('img').count()>=1
            panel.get_by_role('button',name='Create footage request',exact=True).click()
            page.locator('[aria-label="Footage request details"]').wait_for()
            picture(page,'waiting-for-signature',tmp_path)
            assert not provider['posts']
            sign(page,'request')
            panel.get_by_role('status').filter(has_text='Generating').first.wait_for(timeout=30000)
            assert waiting.wait(timeout=10)
            picture(page,'generating',tmp_path)
            arrive.set()
            panel.get_by_text('Take waiting for your decision',exact=True).wait_for(timeout=30000)
            assert len(provider['posts'])==1
            video=panel.locator('video').first
            video.evaluate('(v)=>{v.muted=true;return v.play()}')
            video.evaluate('(v)=>{v.currentTime=v.duration-0.2}')
            page.wait_for_function("()=>[...document.querySelectorAll('video')].some(v=>v.ended)",timeout=12000)
            picture(page,'take-waiting',tmp_path)
            panel.get_by_role('button',name='Keep this take',exact=True).first.click()
            page.locator('[aria-label="Footage request details"]').wait_for()
            picture(page,'keep-signing',tmp_path)
            sign(page,'decision')
            panel.get_by_role('status').filter(has_text='Kept').wait_for(timeout=20000)
            picture(page,'kept',tmp_path)
            panel.get_by_role('button',name='Decline this take',exact=True).first.click()
            sign(page,'decision')
            panel.get_by_text('Declined. This decision is final',exact=False).wait_for(timeout=20000)
            assert panel.get_by_role('button',name='Keep this take',exact=True).first.is_disabled()
            picture(page,'declined',tmp_path)
            panel.get_by_role('button',name='Create a retake request',exact=True).click()
            page.locator('[aria-label="Footage request details"]').wait_for()
            picture(page,'retake-signature',tmp_path)
            assert len(provider['posts'])==1
            sign(page,'request')
            panel.get_by_text('Take waiting for your decision',exact=True).wait_for(timeout=30000)
            assert len(provider['posts'])==2
            assert not (root/'checkpoint_assets.json').exists()
            assert not errors
            page.close()
        finally:arrive.set();browser.close()


@pytest.mark.parametrize('missing',['story','looks','pictures','prices'])
def test_missing_material_controls_explain_the_next_step(film,provider,monkeypatch,tmp_path,missing):
    root,_=film
    if missing=='story':
        path=root/'checkpoint_canon_ingest.json';checkpoint=json.loads(path.read_text())
        checkpoint.update(status='in_progress',human_approved=False)
        path.write_text(json.dumps(checkpoint))
    elif missing=='looks':
        from tests.lib import look_lock_helpers as looks
        from lib.look_ingest import active_looks
        looks.retire_look(root,next(iter(active_looks(root).values())).payload)
    elif missing=='pictures':
        import copy
        from lib.trailer_edit import read_revision,save_revision
        current=read_revision(root);paper=copy.deepcopy(current['paper_cut']);paper['shots'][0]['references']=[]
        save_revision(root,paper,current['edit_revision_sha256'])
    else:
        import hashlib,yaml
        from tests.tools.test_signed_footage import sign
        config=yaml.safe_load((root/'project.yaml').read_text());config.pop('footage_price_basis')
        raw=yaml.safe_dump(config).encode();(root/'project.yaml').write_bytes(raw)
        sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url)
            response=page.request.get(url.split('/p/')[0]+f'/api/project/{root.name}/footage')
            assert response.ok,response.text()
            panel=page.get_by_role('article',name='Shot 1 footage',exact=True)
            create=panel.get_by_role('button',name='Create footage request',exact=True)
            try:create.wait_for(timeout=15000)
            except Exception:pytest.fail('Missing control: '+str(errors)+' '+page.locator('#viewer').inner_text())
            assert create.is_disabled()
            text={'story':'current signed story','looks':'No look is approved yet','pictures':'Add reference pictures','prices':'No footage price figures have been signed'}[missing]
            panel.get_by_text(text,exact=False).wait_for()
            picture(page,'missing-'+missing,tmp_path)
            assert not errors and not provider['posts'] and not provider['uploads']
        finally:browser.close()


def test_recovery_buttons_reuse_the_existing_signatures(film,provider,monkeypatch,tmp_path):
    from tests.tools.test_signed_footage import workspace,looks
    root,shot=film
    record=workspace().prepare_request(root,shot,model=workspace().MODEL,duration_seconds=5)
    looks.approve_request(workspace().request_approval(root,record['request_id']),root)
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url)
            panel=page.get_by_role('article',name='Shot 1 footage',exact=True)
            panel.get_by_role('button',name='Make signed clip',exact=True).click()
            panel.get_by_text('Take waiting for your decision',exact=True).first.wait_for(timeout=30000)
            from lib.supervised_production import history
            take=next(t for t in history(root,shot) if t.get('kind')=='take')
            looks.approve_request(workspace().request_decision(root,shot,take['take_id'],'accept'),root)
            page.reload()
            panel.get_by_role('button',name='Use signed keep in the cut',exact=True).click()
            panel.get_by_role('status').filter(has_text='Kept').wait_for(timeout=20000)
            picture(page,'recovered-keep',tmp_path)
            assert len(provider['posts'])==1 and not errors
        finally:browser.close()


def test_unknown_charge_review_is_signed_in_the_board(film,provider,monkeypatch,tmp_path):
    from tests.tools.test_signed_footage import workspace,looks
    from tools.video import _shared
    from tools.cost_tracker import nonterminal_reservations
    root,shot=film
    record=workspace().prepare_request(root,shot,model=workspace().MODEL,duration_seconds=5)
    looks.approve_request(workspace().request_approval(root,record['request_id']),root)
    def timeout(*a,**k):raise TimeoutError('An invented unknown outcome.')
    monkeypatch.setattr(_shared,'fal_queue_wait',timeout)
    with pytest.raises(ValueError):workspace().execute(root,record['request_id'])
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url)
            panel=page.get_by_role('article',name='Shot 1 footage',exact=True)
            panel.get_by_role('status').filter(has_text='Charge needs your review').wait_for()
            assert panel.get_by_role('button',name='Create a retake request',exact=True).is_disabled()
            panel.get_by_text('Highest charge: $0.4000',exact=False).wait_for()
            panel.get_by_label('What you checked in the service account').fill('The invented account confirms no charge.')
            panel.get_by_label('Clip length in seconds').fill('6')
            panel.get_by_label('Clip length in seconds').press('Tab')
            panel.get_by_text('Highest charge: $0.6000',exact=False).wait_for()
            assert panel.get_by_label('What you checked in the service account').input_value()=='The invented account confirms no charge.'
            with page.expect_response(lambda response:'/footage/quote' in response.url) as quote_response:
                panel.get_by_role('button',name='Refresh price',exact=True).click()
            assert quote_response.value.ok
            assert panel.get_by_label('What you checked in the service account').input_value()=='The invented account confirms no charge.'
            picture(page,'unknown-charge',tmp_path)
            panel.get_by_role('button',name='Review account outcome',exact=True).click()
            page.get_by_text('Spending is blocked',exact=False).first.wait_for()
            assert nonterminal_reservations(root)
            sign(page,'account')
            panel.get_by_role('button',name='Create a retake request',exact=True).wait_for()
            for _ in range(100):
                if not nonterminal_reservations(root):break
                page.wait_for_timeout(100)
            assert not nonterminal_reservations(root)
            assert len(provider['posts'])==1 and not errors
        finally:browser.close()
