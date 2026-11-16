"""Time the actual local Delivery journey, with real temporary signatures.

An explicit timing parent can retain the same invented material for the
before/after runs. Normal focused runs build their own independent material.
"""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect

from tests.backlot.test_delivery_browser import delivery_cut
from tests.backlot.test_voice_panel_browser import board, short_signing_folder
from tests.lib.test_trimmed_trailer_delivery import trailer_cut
from tests.tools.test_signed_footage import film, provider, looks, workspace, sign


@pytest.fixture
def tmp_path(tmp_path_factory):
    parent = os.environ.get('DELIVERY_TIMING_PARENT')
    if not parent:
        return tmp_path_factory.mktemp('delivery-timing')
    material = Path(parent) / 'material'
    material.mkdir(parents=True, exist_ok=True)
    return material


@pytest.fixture
def timed_material(request, monkeypatch, tmp_path, short_signing_folder):
    from lib import gates, trailer_edit, cutting_view
    parent = Path(os.environ.get('DELIVERY_TIMING_PARENT', str(tmp_path / 'evidence')))
    parent.mkdir(parents=True, exist_ok=True)
    manifest = parent / 'material.json'
    if os.environ.get('DELIVERY_TIMING_REUSE'):
        saved = json.loads(manifest.read_text())
        root = Path(saved['root'])
        monkeypatch.setenv('OPENMONTAGE_GATES_DIR', saved['signing'])
        monkeypatch.setattr('lib.paths.PROJECTS_DIR', root.parent)
        monkeypatch.setattr('lib.events.PROJECTS_DIR', root.parent)
        # Give the same pictures, sound, card words and settings a fresh
        # readiness revision through the existing edit writer. An unchanged
        # Save cut deliberately preserves signatures in the joined app.
        old = trailer_edit.read_revision(root)
        trailer_edit.save_edit_revision(root,{**old,'unfinished':True},old['edit_revision_sha256'])
    else:
        signing = parent / 'g'
        signing.mkdir(exist_ok=True)
        monkeypatch.setenv('OPENMONTAGE_GATES_DIR', str(signing))
        root, calls, posts = delivery_cut.__wrapped__(request.getfixturevalue('trailer_cut'))
        assert len(calls['posts']) == 3 and len(posts) == 1
        with manifest.open('x') as output:
            json.dump({'root': str(root), 'signing': str(gates.gates_dir())}, output)
    contacts = []
    def refuse(*args, **kwargs):
        contacts.append(1)
        raise AssertionError('Delivery must not contact a service.')
    monkeypatch.setattr('requests.post', refuse)
    monkeypatch.setattr('requests.get', refuse)
    yield root, parent, contacts
    assert contacts == []


def test_timed_saved_cut_to_finder(timed_material, monkeypatch):
    root, parent, contacts = timed_material
    phase = os.environ.get('DELIVERY_TIMING_PHASE', 'proof')
    timings = []
    reveals = []
    original_run = subprocess.run
    def local_run(args, *values, **options):
        if Path(args[0]).name == 'open' and args[1] == '-R':
            assert Path(args[2]).resolve().is_relative_to(root.resolve())
            reveals.append(args[2])
            return subprocess.CompletedProcess(args, 0)
        return original_run(args, *values, **options)
    monkeypatch.setattr(subprocess, 'run', local_run)
    # A second board records a separate isolation audit without replacing
    # the first run's evidence or any production source.
    original_open = Path.open
    def audit_open(path, mode='r', *args, **kwargs):
        if path == root / 'browser-isolation.json' and mode == 'x' and path.exists():
            path = parent / ('browser-isolation-' + phase + '.json')
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', audit_open)
    monkeypatch.setenv('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')
    with board(root, monkeypatch) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.set_default_timeout(240000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(url + '/') else route.abort())
        delivery = page.get_by_role('region', name='Trailer delivery', exact=True)
        review = page.get_by_role('region', name='Delivery review', exact=True)
        def measure(label, action, ready):
            started = time.monotonic()
            action()
            ready()
            seconds = time.monotonic() - started
            timings.append({'press': label, 'seconds': seconds})
            print(label + ': ' + format(seconds, '.3f') + ' seconds', flush=True)
            return started
        def photo(name, target):
            folder = os.environ.get('DELIVERY_SPEED_PICTURES')
            if folder:
                target.scroll_into_view_if_needed()
                path = Path(folder) / ('delivery-' + phase + '-' + name + '.png')
                assert not path.exists()
                page.screenshot(path=str(path))
        def signing(stage):
            movie = review.locator('video')
            expect(movie).to_have_js_property('readyState', 4, timeout=180000)
            movie.scroll_into_view_if_needed()
            # Headless Chromium's unavailable audio sink can stop its clock.
            # Muting automation changes no source bytes or technical checks;
            # this test proves full playback, not human listening findings.
            movie.evaluate('''v=>{v.muted=true;window.deliveryVideoEvents=[];for(const name of ['play','pause','ended','emptied','loadedmetadata','error'])v.addEventListener(name,()=>window.deliveryVideoEvents.push({name,time:v.currentTime,source:v.currentSrc,connected:v.isConnected}));return v.play();}''')
            page.wait_for_function('(v)=>v.ended||v.paused', arg=movie.element_handle(), timeout=60000)
            assert movie.evaluate('(v)=>v.ended'), 'The review paused before its full playback finished.'
            photo(stage + '-review', review)
            measure('Start signing ' + stage,
                    lambda: page.get_by_role('button', name='Start signing', exact=True).click(),
                    lambda: expect(page.locator('#gate-sign-panel .sign-question')).to_be_visible(timeout=180000))
            measure('Approve and sign ' + stage,
                    lambda: page.get_by_role('button', name='Approve and sign', exact=True).click(timeout=180000),
                    lambda: expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded', timeout=180000))
            measure('Return from ' + stage,
                    lambda: page.get_by_role('button', name='Return to delivery', exact=True).click(),
                    lambda: expect(delivery.get_by_role('button',name={'rough cut':'Record the signed rough-cut approval','colour and sound':'Record the signed colour and sound approval','final file':'Record the signed final approval'}[stage],exact=True).first).to_be_enabled(timeout=240000))
        def record(label, next_label):
            started=time.monotonic()
            suffix='/rough/apply' if 'rough-cut' in label else '/colour/apply' if 'colour and sound' in label else '/'+delivery.locator('[data-delivery]').first.get_attribute('data-delivery')+'/apply'
            with page.expect_response(lambda r:r.url.endswith('/delivery'+suffix) and r.request.method=='POST',timeout=240000) as response:
                delivery.get_by_role('button', name=label, exact=True).click()
            assert response.value.ok,response.value.text()
            expect(delivery.get_by_role('status').first).to_contain_text(response.value.json()['message'],timeout=240000)
            timings.append({'press':label,'seconds':time.monotonic()-started})
            print(label+': '+format(time.monotonic()-started,'.3f')+' seconds',flush=True)
            expect(delivery.get_by_label('Next delivery step')).to_have_text('Next: ' + next_label + '.', timeout=240000)
            timings.append({'press': label + ' until highlight moves', 'seconds': time.monotonic() - started})
        try:
            measure('Open saved trailer', lambda: page.goto(f'{url}/p/{root.name}#k=invented-browser-proof'),
                    lambda: expect(delivery.get_by_role('button',name='Review and sign the saved rough cut',exact=True)).to_be_enabled(timeout=240000))
            cut = page.get_by_role('region', name='Cutting view', exact=True)
            page.get_by_role('button', name='Cutting workspace', exact=True).click()
            measure('Save cut', lambda: cut.get_by_role('button', name='Save cut', exact=True).click(),
                    lambda: expect(cut.get_by_role('status').first).to_contain_text('saved', timeout=240000))
            measure('Prepare saved playback', lambda: cut.get_by_role('button', name='Prepare saved playback', exact=True).click(),
                    lambda: expect(cut.get_by_role('status').first).to_contain_text('ready', timeout=240000))
            page.get_by_role('button', name='Delivery workspace', exact=True).click()
            measure('Review and sign the saved rough cut',
                    lambda: delivery.get_by_role('button', name='Review and sign the saved rough cut', exact=True).click(),
                    lambda: expect(review.get_by_role('heading', name='Review the saved rough cut', exact=True)).to_be_visible(timeout=240000))
            signing('rough cut')
            record('Record the signed rough-cut approval', 'Review and sign colour and sound')
            measure('Review and sign colour and sound',
                    lambda: delivery.get_by_role('button', name='Review and sign colour and sound', exact=True).click(),
                    lambda: expect(review.get_by_role('heading', name='Review the saved colour and sound', exact=True)).to_be_visible(timeout=240000))
            signing('colour and sound')
            record('Record the signed colour and sound approval', 'Make the final file')
            def make_file():
                with page.expect_response(lambda r:r.url.endswith('/delivery/export') and r.request.method=='POST',timeout=300000) as exported:
                    delivery.get_by_role('button',name='Make the final file',exact=True).click()
                assert exported.value.ok,exported.value.text()
                expect(delivery.locator('[data-delivery]').first).to_have_attribute('data-delivery',exported.value.json()['id'],timeout=240000)
            measure('Make the final file',make_file,
                    lambda: expect(delivery).to_contain_text('Loudness is on target', timeout=300000))
            tile = delivery.locator('[data-delivery]').first
            movie = tile.locator('video')
            expect(movie).to_have_js_property('readyState', 4, timeout=180000)
            movie.scroll_into_view_if_needed()
            movie.evaluate('(v)=>{v.muted=true;return v.play();}')
            page.wait_for_function('(v)=>v.ended', arg=movie.element_handle(), timeout=60000)
            comparison = tile.get_by_role('region', name='Final story comparison', exact=True)
            for checkbox in comparison.get_by_role('checkbox').all():
                checkbox.check()
            comparison.get_by_label('How this final file keeps the story’s tone', exact=True).fill('This invented comparison records the test controls; its speech is a counted test tone, not a real performance.')
            measure('Review and sign final approval',
                    lambda: tile.get_by_role('button', name='Review and sign final approval', exact=True).click(),
                    lambda: expect(review.get_by_role('heading', name='Review this exact final file', exact=True)).to_be_visible(timeout=240000))
            signing('final file')
            record('Record the signed final approval', 'Show approved file in Finder')
            measure('Show approved file in Finder', lambda: tile.get_by_role('button', name='Show approved file in Finder', exact=True).click(),
                    lambda: expect(tile.get_by_role('note')).to_contain_text('shown in Finder', timeout=180000))
            photo('finished', delivery)
            identity = tile.get_attribute('data-delivery')
            measure('Reload the finished trailer', lambda: page.reload(),
                    lambda: expect(delivery.locator('[data-delivery]').first).to_contain_text('Approved by you', timeout=240000))
            assert delivery.locator('[data-delivery]').first.get_attribute('data-delivery') == identity
            assert len(reveals) == 1 and not errors and not contacts
            photo('reopened', delivery)
        finally:
            with (parent / ('timings-' + phase + '.json')).open('x') as output:
                json.dump({'root': str(root), 'times': timings, 'reveals': reveals, 'page_errors': errors, 'outside_contacts': contacts,
                           'video_events':page.evaluate('()=>window.deliveryVideoEvents||[]'),
                           'players':page.locator('video').evaluate_all('(vs)=>vs.map(v=>({source:v.currentSrc,time:v.currentTime,ended:v.ended,paused:v.paused,ready:v.readyState,error:v.error?.message}))')}, output, indent=2)
            browser.close()
