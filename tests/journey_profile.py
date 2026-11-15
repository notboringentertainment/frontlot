"""Opt-in local diagnosis of a retained invented journey, under pytest's guard.

Run this explicitly with JOURNEY_DIAGNOSTIC naming a stop record inside the
isolated run parent. Its original temporary signing history must still exist;
missing keys are a refusal, never permission to bypass verification. This
utility is outside pytest's normal test-file pattern and sends nothing.
"""
import cProfile
import json
import os
import pstats
import uuid
from pathlib import Path


def retained_journey(monkeypatch):
    from tests import conftest as guard
    parent=Path(os.environ['TMPDIR']).resolve().parent
    diagnostic=Path(os.environ['JOURNEY_DIAGNOSTIC']).resolve()
    assert diagnostic.is_relative_to(parent)
    evidence=json.loads(diagnostic.read_text());root=Path(evidence['root']).resolve()
    assert root.is_relative_to(parent)
    locations=evidence['board_locations'][-1]
    for key,label in [('HOME','home'),('OPENMONTAGE_HOME','home'),('OPENMONTAGE_GATES_DIR','signing'),('OPENMONTAGE_METADATA_PATH','metadata')]:
        value=Path(locations[label]).resolve();assert value.is_relative_to(parent)
        monkeypatch.setenv(key,str(value))
    assert not guard._unsafe_locations(os.environ)
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',root.parent)
    monkeypatch.setenv('OPENMONTAGE_PROJECTS_DIR',str(root.parent))
    calls=[]
    def refuse(*args,**kwargs):calls.append(1);raise AssertionError('A local review read must never contact a service.')
    monkeypatch.setattr('requests.sessions.Session.request',refuse)
    return root,calls


def test_read_saved_invented_journey(monkeypatch):
    root,calls=retained_journey(monkeypatch)
    parent=Path(os.environ['TMPDIR']).resolve().parent
    from lib import cutting_view,trailer_edit
    from lib.trailer_contract import signed_trailer_pin
    assert signed_trailer_pin(root) is not None
    from backlot.cutting import snapshot
    from lib import trailer_delivery
    for label,operation in [('snapshot',lambda:snapshot(root)),('validate',lambda:cutting_view.validate_cut(root,cutting_view.editable(trailer_edit.read_revision(root)['timeline']),verify_files=True)),('delivery',lambda:trailer_delivery.snapshot(root))]:
        profile=cProfile.Profile();result=profile.runcall(operation)
        profile.dump_stats(str(parent/('journey-'+label+'-'+uuid.uuid4().hex+'.profile')))
        print(label);pstats.Stats(profile).sort_stats('cumulative').print_stats(18)
        if label=='delivery':print({key:result[key] for key in ('rough_status','colour_status')})
    assert calls==[]


def test_record_colour_on_retained_invented_journey(monkeypatch):
    import time
    from playwright.sync_api import sync_playwright,expect
    from tests.backlot.test_footage_controls_browser import browser_board
    from backlot import server,state
    root,calls=retained_journey(monkeypatch)
    monkeypatch.setattr(server,'_PROJECTS_ROOT_STR',os.path.normcase(str(root.parent.resolve())))
    monkeypatch.setattr(state,'PROJECTS_DIR',root.parent)
    with browser_board(root,monkeypatch) as address,sync_playwright() as pw:
        address=address.replace('?static=1','');origin=address.split('/p/')[0]
        endpoint=origin+f'/api/project/{root.name}/delivery'
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];events=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:events.append({'kind':'request','url':request.url,'method':request.method,'at':time.monotonic()}))
            page.on('response',lambda response:events.append({'kind':'response','url':response.url,'status':response.status,'at':time.monotonic()}))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
            with page.expect_response(lambda response:response.url==endpoint,timeout=90000) as opened:
                page.goto(address)
            assert opened.value.ok,opened.value.text()
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            control=panel.get_by_role('button',name='Record the signed colour and sound approval',exact=True)
            expect(control).to_be_enabled(timeout=5000)
            began=time.monotonic()
            with page.expect_response(lambda response:response.url==endpoint+'/colour/apply' and response.request.method=='POST',timeout=90000) as applied:
                control.click()
            response=applied.value;assert response.ok,response.text()
            assert response.json()['message']=='Your signed stage approval is recorded.'
            print(json.dumps({'record_seconds':time.monotonic()-began,'events':events}),flush=True)
            expect(panel.get_by_role('status').first).to_have_text(response.json()['message'],timeout=5000)
            assert not errors and calls==[]
        finally:browser.close()


def test_reopen_saved_invented_journey_in_the_real_board(monkeypatch):
    import subprocess,time
    from playwright.sync_api import sync_playwright,expect
    from tests.backlot.test_footage_controls_browser import browser_board
    from lib import trailer_edit
    from backlot import server,state
    root,calls=retained_journey(monkeypatch)
    monkeypatch.setattr(server,'_PROJECTS_ROOT_STR',os.path.normcase(str(root.parent.resolve())))
    monkeypatch.setattr(state,'PROJECTS_DIR',root.parent)
    before={path:path.read_bytes() for path in root.glob('checkpoint_*.json')}
    revision=trailer_edit.read_revision(root)['edit_revision_sha256']
    revealed=[];original=subprocess.run
    def run(args,*values,**options):
        if args[:2]==['open','-R']:
            assert Path(args[2]).resolve().is_relative_to(root)
            revealed.append(args[2]);return subprocess.CompletedProcess(args,0)
        return original(args,*values,**options)
    monkeypatch.setattr(subprocess,'run',run)
    with browser_board(root,monkeypatch) as address,sync_playwright() as pw:
        address=address.replace('?static=1','');origin=address.split('/p/')[0]
        endpoint=origin+f'/api/project/{root.name}/delivery'
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];outside=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def route(request):
                if request.request.url.startswith(origin+'/'):request.continue_()
                else:outside.append(request.request.url);request.abort()
            page.route('**/*',route)
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            for action in ('open','reload'):
                started=[]
                def requested(request):
                    if request.url==endpoint and request.method=='GET':started.append(request)
                page.on('request',requested);opening=time.monotonic()
                try:
                    with page.expect_response(lambda response:response.url==endpoint and response.request in started,timeout=90000) as loaded:
                        page.goto(address) if action=='open' else page.reload()
                    response=loaded.value;assert response.ok,response.text();data=response.json()
                    assert data['revision']==revision and data['rough_status']==data['colour_status']=='Signed by you'
                    assert data['exports'][0]['status']=='Approved by you'
                    tile=panel.locator('[data-delivery]').first
                    expect(tile).to_have_attribute('data-delivery',data['exports'][0]['id'],timeout=5000)
                    expect(tile.get_by_role('button',name='Show approved file in Finder',exact=True)).to_be_enabled(timeout=5000)
                    print(json.dumps({'action':action,'verified_read_and_controls_seconds':time.monotonic()-opening}),flush=True)
                    video=tile.locator('video');expect(video).to_have_js_property('readyState',4,timeout=30000)
                    video.evaluate('(v)=>v.play()');page.wait_for_function('(v)=>v.ended',arg=video.element_handle(),timeout=15000)
                finally:page.remove_listener('request',requested)
            with page.expect_response(lambda response:response.url==endpoint and response.request.method=='GET',timeout=90000):
                tile.get_by_role('button',name='Show approved file in Finder',exact=True).click()
            expect(panel.get_by_role('status').first).to_contain_text('shown in Finder')
            assert len(revealed)==1 and not errors and not outside and calls==[]
            assert all(path.read_bytes()==raw for path,raw in before.items())
        finally:browser.close()
