"""Invented browser regressions for live history and draft reconciliation."""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager

import pytest
from playwright.sync_api import sync_playwright, expect

from lib import trailer_edit as edit
from tests.lib.test_trailer_round3_editor import collection_paper, collection_canon
from tests.lib.test_trailer_edit import signed_story
from tests.lib.look_lock_helpers import approve_request


@contextmanager
def editor_server(projects):
    # The shared suite guard has set both folders before this module imports.
    from lib.gates import gates_dir
    from backlot.server import backlot_metadata_dir
    run_root=os.environ['FRONTLOT_TEST_RUN_ROOT']
    assert str(backlot_metadata_dir()).startswith(run_root)
    import tempfile
    assert any(gates_dir().resolve().is_relative_to(p.resolve()) for p in (Path('/tmp'),Path(tempfile.gettempdir())))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ,OPENMONTAGE_PROJECTS_DIR=str(projects))
    code=f"from backlot.server import create_app;import uvicorn;uvicorn.run(create_app(port={port},capability_token='invented-browser-proof'),host='127.0.0.1',port={port},log_level='error')"
    process=subprocess.Popen([sys.executable,'-c',code],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    url=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:urllib.request.urlopen(url+'/api/health',timeout=1);break
            except OSError:time.sleep(.1)
        else:raise AssertionError('The invented board did not start.')
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def editor_world(tmp_path,monkeypatch):
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    root=edit.create_trailer(tmp_path,'The Last Collection',28,50)
    req=json.loads(next((root/'.gate-requests').glob('*.json')).read_text());approve_request(req,root)
    return root


def fill_example(page):
    p=collection_paper()
    for label,beat in zip(['Hook','Escalation','Reversal','Final invitation'],p['beats']):page.get_by_label(label,exact=True).fill(beat['text'])
    page.get_by_role('button',name='Add spoken line',exact=True).click()
    line=page.locator('fieldset').filter(has=page.locator('legend',has_text='Line 1'))
    line.get_by_label('Exact wording').fill(p['lines'][0]['text'])
    line.get_by_label('Starts at frame').fill('168');line.get_by_label('Screen time in frames').fill('72')
    for n,card in enumerate(p['cards'],1):
        page.get_by_role('button',name='Add title card',exact=True).click()
        box=page.locator('fieldset').filter(has=page.locator('legend',has_text=f'Card {n}'))
        box.get_by_label('Exact wording').fill(card['text']);box.get_by_label('Starts at frame').fill(str(card['start_frame']))
        box.get_by_label('Screen time in frames').fill(str(card['duration_frames']))
    for n,shot in enumerate(p['shots'],1):
        page.get_by_role('button',name='Add shot',exact=True).click()
        box=page.locator('fieldset').filter(has=page.locator('legend',has_text=f'Shot {n}'))
        box.get_by_label('Purpose (private plan)').fill(shot['purpose'])
        box.get_by_label('Screen time in frames').fill(str(shot['duration_frames']))
        box.get_by_label('References, separated by commas').fill(', '.join(shot['references']))
        box.get_by_label('Difficulty').fill(shot['difficulty'])


def test_example_live_versions_full_details_restore_and_stale_draft(editor_world):
    root=editor_world
    with editor_server(root.parent) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            expect(page.get_by_role('heading',name='Story and paper cut')).to_be_visible()
            fill_example(page)
            expect(page.get_by_text('The shots match the target length.',exact=True)).to_be_visible()
            page.get_by_role('button',name='Save a new version',exact=True).click()
            expect(page.get_by_role('button',name='Open version 1',exact=False)).to_be_visible()
            expect(page.get_by_text('This draft is not yet checked against the approved story. Later stages cannot use it.',exact=True)).to_be_visible()
            page.get_by_label('Hook',exact=True).fill('The door opens a second time.')
            page.get_by_role('button',name='Save a new version',exact=True).click()
            expect(page.get_by_role('button',name='Open version 2',exact=False)).to_be_visible()
            assert page.get_by_role('button',name='Open version 1',exact=False).inner_text().count('·')>=1
            page.get_by_role('button',name='Open version 1',exact=False).click()
            expect(page.get_by_text('Lasts 120 frames. References: Gallery sketch. Difficulty: Simple.',exact=True)).to_be_visible()
            page.get_by_role('button',name='Restore this as a new version',exact=True).click()
            expect(page.get_by_role('button',name='Open version 3',exact=False)).to_be_visible()
            expect(page.get_by_label('Hook',exact=True)).to_have_value(collection_paper()['beats'][0]['text'])
            # A second window saves. Opening its version must keep the typed draft.
            page.get_by_label('Hook',exact=True).fill('My unsaved hook')
            current=edit.read_revision(root);other=copy.deepcopy(current['paper_cut']);other['beats'][0]['text']='Another window hook'
            edit.save_revision(root,other,current['edit_revision_sha256'])
            page.get_by_role('button',name='Refresh story checks and saved versions',exact=True).click()
            expect(page.get_by_role('button',name='Open the latest saved version',exact=True)).to_be_visible()
            expect(page.get_by_label('Hook',exact=True)).to_have_value('My unsaved hook')
            page.get_by_role('button',name='Open the latest saved version',exact=True).click()
            page.get_by_role('button',name='Use this latest version and keep my unsaved draft',exact=True).click()
            expect(page.get_by_label('Hook',exact=True)).to_have_value('Another window hook')
            page.get_by_role('button',name='Return to my unsaved draft',exact=True).click()
            expect(page.get_by_label('Hook',exact=True)).to_have_value('My unsaved hook')
            # Story approval outside the panels immediately rechecks the saved draft.
            signed_story(root,collection_canon())
            page.get_by_role('button',name='Refresh story checks and saved versions',exact=True).click()
            expect(page.get_by_text('Checked against the approved story.',exact=True)).to_be_visible()
            assert page.get_by_text('Start a trailer',exact=True).count()==0
        finally:browser.close()


def test_stale_window_offers_recovery_without_discarding_draft(editor_world):
    from tests.lib.test_trailer_edit import paper
    root=editor_world;first=edit.save_revision(root,paper(),None)
    with editor_server(root.parent) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_label('Hook',exact=True).fill('A draft still being written')
            newer=copy.deepcopy(first['paper_cut']);newer['beats'][0]['text']='An opening saved elsewhere'
            edit.save_revision(root,newer,first['edit_revision_sha256'])
            # Invoke exactly the module refresh the board calls, without relying
            # on filesystem watcher scheduling or a manual page reload.
            page.evaluate("async (slug)=>{const s=await fetch('/api/project/'+slug+'/state').then(r=>r.json());(await import('/ui/trailer.js')).renderPaperCut(s);}",root.name)
            expect(page.get_by_role('button',name='Open the latest saved version',exact=True)).to_be_visible()
            expect(page.get_by_label('Hook',exact=True)).to_have_value('A draft still being written')
        finally:browser.close()


def test_saved_versions_update_without_page_reload(editor_world):
    from tests.lib.test_trailer_edit import paper
    root=editor_world;edit.save_revision(root,paper(),None)
    with editor_server(root.parent) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_label('Hook',exact=True).fill('Another saved opening')
            page.get_by_role('button',name='Save a new version',exact=True).click()
            expect(page.get_by_role('button',name='Open version 2',exact=False)).to_be_visible()
            assert len(edit.paper_state(root)['history'])==2
        finally:browser.close()


def test_target_change_refreshes_timing_without_losing_draft(editor_world):
    from lib.checkpoint import init_project
    root=editor_world;edit.save_revision(root,collection_paper(),None)
    with editor_server(root.parent) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_label('Hook',exact=True).fill('Keep this unsaved opening')
            init_project(root.name,title='The Last Collection',pipeline_type='authored-film@1.6',pipeline_dir=root.parent,target_duration_seconds=29,review_target_change=True)
            page.evaluate("async (slug)=>{const s=await fetch('/api/project/'+slug+'/state').then(r=>r.json());(await import('/ui/trailer.js')).renderPaperCut(s);}",root.name)
            expect(page.get_by_text('Shots total 28.00 seconds; target 29.00 seconds. 24 frames short.',exact=True)).to_be_visible()
            expect(page.get_by_label('Hook',exact=True)).to_have_value('Keep this unsaved opening')
        finally:browser.close()
