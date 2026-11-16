import asyncio,json,os,subprocess,sys,time,threading
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.tools.test_signed_footage import film,provider
from tests.backlot.test_voice_panel_browser import board,short_signing_folder


def test_two_windows_do_not_cancel_signer_preparation(film,provider,monkeypatch,tmp_path):
    from backlot import server,tty
    from backlot.footage_controls import create
    from lib.footage_requests import MODEL
    root,shot=film;gate=create(root,{'shot_id':shot,'model':MODEL,'duration_seconds':5})['gate']
    markers=tmp_path/'markers';markers.mkdir();monkeypatch.setenv('STUCK_PREPARATION_MARKERS',str(markers))
    original=server.load_gate_detail;checks=[];processes=[]
    def load(project,identity):
        try:asyncio.get_running_loop()
        except RuntimeError:return original(project,identity)
        until=time.monotonic()+15;marker=None
        while marker is None:
            marker=next((p for p in markers.glob('*.ready') if not p.with_suffix('.release').exists()),None)
            if time.monotonic()>until:raise AssertionError('The real signer never reached its lock-held preparation.')
            if marker is None:time.sleep(.01)
        try:
            result=original(project,identity);checks.append(result);return result
        finally:
            with marker.with_suffix('.release').open('x') as stream:stream.write('The board finished its final packet read.')
    def spawn(registry,project,identity):
        process=subprocess.Popen([sys.executable,'-m','tests.backlot.stuck_preparation_child','broker',project.name,identity],cwd=Path(__file__).resolve().parents[2],env=dict(os.environ),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        registry.track_broker(process);processes.append(process);return process
    monkeypatch.setattr(server,'load_gate_detail',load);monkeypatch.setattr(tty,'_spawn_broker',spawn)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            context=browser.new_context();pages=[context.new_page(),context.new_page()];errors=[]
            context.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            for page in pages:
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url+'/p/'+root.name+'#k=invented-browser-proof')
                page.locator(f'[data-request-id="{gate["request_id"]}"]').first.click()
            for attempt in range(int(os.environ.get('STUCK_SIGNING_REPEATS','24'))):
                page=pages[attempt%2]
                page.reload();page.locator(f'[data-request-id="{gate["request_id"]}"]').first.click()
                page.get_by_role('button',name='Start signing',exact=True).click(timeout=30000)
                expect(page.get_by_role('button',name='Sign and make this clip',exact=True)).to_be_enabled(timeout=20000)
                assert not checks[-1]['packet'].get('packet_error'),checks[-1]
                page.get_by_role('button',name='Not now',exact=True).click()
                page.get_by_role('button',name='Return to footage',exact=True).click()
                # Wait for this owned signer to exit before the next start.
                processes[-1].wait(timeout=15)
            assert len(checks)==int(os.environ.get('STUCK_SIGNING_REPEATS','24'))
            assert all(not value for value in provider.values()) and not errors
            from lib import receipts
            from lib.run_common import request_state
            assert not receipts.verified_approvals(root,'footage_spend')
            assert request_state(root,gate['request_id'])=='pending'
        finally:browser.close()
    assert all(p.poll() is not None for p in processes)


def test_real_purchase_lock_still_refuses_signing(film,provider):
    from lib.media_spending import call_lock,PaidCallBusy
    from lib.spending_requests import signing_packet,temporary_wait
    from scripts.gate_approve import construct
    from backlot.footage_controls import create
    from lib.footage_requests import MODEL
    root,shot=film;gate=create(root,{'shot_id':shot,'model':MODEL,'duration_seconds':5})['gate']
    entered=threading.Event();release=threading.Event()
    def purchase():
        with call_lock(root):entered.set();assert release.wait(15)
    thread=threading.Thread(target=purchase);thread.start()
    try:
        assert entered.wait(10)
        with signing_packet(),pytest.raises(PaidCallBusy):construct(root,gate)
        with signing_packet(footage=False):assert 'being made' in temporary_wait(root)
        assert 'being made' in temporary_wait(root)
        assert all(not value for value in provider.values())
    finally:release.set();thread.join(15)
