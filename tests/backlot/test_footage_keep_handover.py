"""Keeping a take must not claim that another window saved the paper cut."""
import os
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect
from tests.backlot.test_early_paid_readiness import start_browser
from tests.backlot.test_footage_controls_browser import browser_board, sign
from tests.tools.test_signed_footage import film as original_film, provider, workspace
from tests.lib import look_lock_helpers as looks


WORDS = 'Keep these newly written words.'
REFUSAL = 'Save your changed paper cut as a new version before assigning a picture or keeping footage. Your words are still here.'


@pytest.fixture
def film(tmp_path, monkeypatch):
    from tests.backlot.isolation_helpers import signing_directory
    short = signing_directory('k')
    home = short / 'h'
    home.mkdir()
    gates = short / 'g'
    gates.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('OPENMONTAGE_GATES_DIR', str(gates))
    return original_film.__wrapped__(tmp_path, monkeypatch)


def watch(page):
    page.evaluate('''()=>{
      window.paperConflictSeen=false;
      const hit=node=>Boolean(node&&(node.textContent||'').includes('Another window saved'));
      const see=()=>{if(hit(document.querySelector('[aria-label="Trailer paper cut"]')))window.paperConflictSeen=true;};
      new MutationObserver(records=>{
        for(const record of records){
          if(hit(record.target))window.paperConflictSeen=true;
          for(const node of record.addedNodes)if(hit(node))window.paperConflictSeen=true;
        }
        see();
      }).observe(document.documentElement,{subtree:true,childList:true,characterData:true});
      see();
    }''')


def capture(page, name):
    folder = os.environ.get('FRONTLOT_KEEP_ROUND2_PICTURES')
    if folder:
        page.get_by_role('heading', name='Story and paper cut', exact=True).evaluate("node=>node.scrollIntoView({block:'start',behavior:'instant'})")
        page.screenshot(path=str(Path(folder) / (name + '-paper-cut.png')))


def conflict_seen(page):
    return page.evaluate('()=>window.paperConflictSeen===true')


def current_cut(root):
    from lib.trailer_edit import read_revision
    return read_revision(root)


def placed_take(root):
    picture = (current_cut(root).get('timeline') or {}).get('picture') or [{}]
    return picture[0]


def signature(root, take_id):
    from lib import receipts
    return receipts.find_approval(root, 'footage_take', entity_id=take_id)


def make_take(root, shot, signed=False):
    from lib import footage_requests
    record = workspace().prepare_request(root, shot, model=workspace().MODEL, duration_seconds=5)
    looks.approve_request(workspace().request_approval(root, record['request_id']), root)
    take = workspace().execute(root, record['request_id'])
    with pytest.raises(ValueError, match='signature'):
        footage_requests.apply_decision(root, shot, take['take_id'], 'accept')
    assert signature(root, take['take_id']) is None
    assert placed_take(root).get('kind') != 'footage'
    if signed:
        looks.approve_request(workspace().request_decision(root, shot, take['take_id'], 'accept'), root)
        assert signature(root, take['take_id'])
        assert placed_take(root).get('kind') != 'footage'
    return take


def hook_field(page):
    cut = page.get_by_role('region', name='Trailer paper cut', exact=True)
    expect(cut).to_be_visible()
    return cut, cut.get_by_text('Hook', exact=True).locator('..').locator('textarea')


def save_cut(page, cut):
    cut.get_by_role('button', name='Save a new version', exact=True).click()
    expect(cut.get_by_role('status')).to_contain_text('Saved.')
    expect(cut.get_by_role('button', name='Save a new version', exact=True)).to_be_enabled()
    expect(cut).not_to_contain_text('Another window saved')
    expect(cut).not_to_contain_text('changed in another window')


def open_writer(root, monkeypatch, playwright):
    board = browser_board(root, monkeypatch)
    url = board.__enter__()
    browser, page = start_browser(playwright, url)
    page.goto(url)
    # Let the initial quote finish before interacting; its response clears
    # quote errors and can otherwise race the unsaved-words refusal.
    page.get_by_role('article', name='Shot 1 footage', exact=True).get_by_text('Highest charge:', exact=False).wait_for()
    return board, browser, page


def close_writer(board, browser):
    browser.close()
    board.__exit__(None, None, None)


def test_normal_keep_with_a_clean_editor_does_not_invent_another_window(film, provider, monkeypatch):
    root, shot = film
    take = make_take(root, shot)
    with sync_playwright() as playwright:
        board, browser, page = open_writer(root, monkeypatch, playwright)
        try:
            panel = page.get_by_role('article', name='Shot 1 footage', exact=True)
            panel.get_by_role('button', name='Keep this take', exact=True).wait_for()
            cut, _hook = hook_field(page)
            watch(page)
            panel.get_by_role('button', name='Keep this take', exact=True).click()
            page.locator('[aria-label="Footage request details"]').wait_for()
            assert placed_take(root).get('kind') != 'footage'
            assert signature(root, take['take_id']) is None
            sign(page, 'decision')
            panel.get_by_role('status').filter(has_text='Kept').wait_for(timeout=20000)
            expect(cut).not_to_contain_text('Another window saved')
            assert conflict_seen(page) is False
            assert signature(root, take['take_id'])
            assert placed_take(root).get('take_id') == take['take_id']
            assert placed_take(root).get('kind') == 'footage'
            save_cut(page, cut)
            assert placed_take(root).get('take_id') == take['take_id']
            assert not page.errors and len(provider['posts']) == 1
        finally:
            close_writer(board, browser)


@pytest.mark.parametrize('background', [None, False, True], ids=['reloaded', 'same-page', 'delayed-refresh'])
def test_normal_keep_after_saving_words_keeps_those_words(film, provider, monkeypatch, background):
    root, shot = film
    take = make_take(root, shot)
    with sync_playwright() as playwright:
        board, browser, page = open_writer(root, monkeypatch, playwright)
        try:
            panel = page.get_by_role('article', name='Shot 1 footage', exact=True)
            cut, hook = hook_field(page)
            watch(page)
            held = []
            if background:
                from tests.backlot.test_signing_outcome_browser import wait_for
                def hold_first(route):
                    if route.request.method == 'GET' and not held:
                        held.append((route, route.fetch().json()))
                    else:
                        route.continue_()
                page.route('**/paper-cut', hold_first)
                cut.get_by_role('button', name='Refresh story checks and saved versions', exact=True).click()
                wait_for(page, lambda: bool(held))
            hook.fill(WORDS)
            save_cut(page, cut)
            assert hook.input_value() == WORDS
            assert current_cut(root)['paper_cut']['beats'][0]['text'] == WORDS
            if background:
                # Return an editor refresh without reloading the page. Return
                # its pre-save snapshot late to check the existing epoch guard.
                held[0][0].fulfill(json=held[0][1])
                expect(cut.get_by_role('status')).to_contain_text('Story checks and versions refreshed')
            if background is None:
                page.reload()
                cut, hook = hook_field(page)
                watch(page)
            panel.get_by_role('button', name='Keep this take', exact=True).wait_for()
            panel.get_by_role('button', name='Keep this take', exact=True).click()
            page.locator('[aria-label="Footage request details"]').wait_for()
            sign(page, 'decision')
            panel.get_by_role('status').filter(has_text='Kept').wait_for(timeout=20000)
            expect(cut).not_to_contain_text('Another window saved')
            if background is not None:
                capture(page, 'normal-save-keep-background' if background else 'normal-save-keep')
            assert conflict_seen(page) is False
            assert hook.input_value() == WORDS
            assert current_cut(root)['paper_cut']['beats'][0]['text'] == WORDS
            assert placed_take(root).get('take_id') == take['take_id']
            assert signature(root, take['take_id'])
            save_cut(page, cut)
            assert current_cut(root)['paper_cut']['beats'][0]['text'] == WORDS
            assert placed_take(root).get('take_id') == take['take_id']
            assert not page.errors and len(provider['posts']) == 1
        finally:
            close_writer(board, browser)


@pytest.mark.parametrize('saved_words', [False, True])
def test_recovery_keep_with_a_clean_editor_does_not_invent_another_window(film, provider, monkeypatch, saved_words):
    root, shot = film
    take = make_take(root, shot, signed=True)
    with sync_playwright() as playwright:
        board, browser, page = open_writer(root, monkeypatch, playwright)
        try:
            panel = page.get_by_role('article', name='Shot 1 footage', exact=True)
            panel.get_by_role('button', name='Use signed keep in the cut', exact=True).wait_for()
            cut, hook = hook_field(page)
            watch(page)
            if saved_words:
                hook.fill(WORDS)
                save_cut(page, cut)
            panel.get_by_role('button', name='Use signed keep in the cut', exact=True).click()
            panel.get_by_role('status').filter(has_text='Kept').wait_for(timeout=20000)
            expect(cut).not_to_contain_text('Another window saved')
            assert conflict_seen(page) is False
            assert placed_take(root).get('take_id') == take['take_id']
            assert placed_take(root).get('kind') == 'footage'
            assert signature(root, take['take_id'])
            if saved_words:
                assert hook.input_value() == WORDS
                assert current_cut(root)['paper_cut']['beats'][0]['text'] == WORDS
                capture(page, 'recovery-save-keep')
            save_cut(page, cut)
            assert placed_take(root).get('take_id') == take['take_id']
            assert not page.errors and len(provider['posts']) == 1
        finally:
            close_writer(board, browser)


def test_recovery_keep_refuses_unsaved_words_and_leaves_them_in_place(film, provider, monkeypatch):
    root, shot = film
    take = make_take(root, shot, signed=True)
    before = current_cut(root)
    with sync_playwright() as playwright:
        board, browser, page = open_writer(root, monkeypatch, playwright)
        try:
            panel = page.get_by_role('article', name='Shot 1 footage', exact=True)
            panel.get_by_role('button', name='Use signed keep in the cut', exact=True).wait_for()
            cut, hook = hook_field(page)
            hook.fill(WORDS)
            watch(page)
            panel.get_by_role('button', name='Use signed keep in the cut', exact=True).click()
            expect(page.get_by_role('alert').filter(has_text=REFUSAL)).to_be_visible()
            expect(cut).not_to_contain_text('Another window saved')
            assert conflict_seen(page) is False
            assert hook.input_value() == WORDS
            assert current_cut(root) == before
            assert placed_take(root).get('kind') != 'footage'
            assert signature(root, take['take_id'])
            save_cut(page, cut)
            assert hook.input_value() == WORDS
            assert current_cut(root)['paper_cut']['beats'][0]['text'] == WORDS
            assert placed_take(root).get('kind') != 'footage'
            assert panel.get_by_role('button', name='Use signed keep in the cut', exact=True).is_visible()
            assert not page.errors and len(provider['posts']) == 1
        finally:
            close_writer(board, browser)


def test_a_second_window_save_still_warns_and_preserves_the_first_windows_words(film, provider, monkeypatch):
    root, shot = film
    make_take(root, shot)
    with sync_playwright() as playwright:
        board, browser, page = open_writer(root, monkeypatch, playwright)
        try:
            cut, hook = hook_field(page)
            hook.fill(WORDS)
            save_cut(page, cut)
            other = browser.new_page()
            other.route('**/*', lambda r: r.continue_() if r.request.url.startswith(page.url.split('/p/')[0] + '/') else r.abort())
            other.on('pageerror', lambda error: page.errors.append(str(error)))
            other.goto(page.url + '#k=invented-browser-writer')
            other_cut, other_hook = hook_field(other)
            other_hook.fill('Words saved in the second window.')
            save_cut(other, other_cut)
            hook.fill('My first window still has unsaved words.')
            watch(page)
            cut.get_by_role('button', name='Refresh story checks and saved versions', exact=True).click()
            expect(cut).to_contain_text('Another window saved a newer version')
            assert conflict_seen(page)
            assert hook.input_value() == 'My first window still has unsaved words.'
            save = cut.get_by_role('button', name='Save a new version', exact=True)
            with page.expect_response(lambda r: r.url.endswith('/paper-cut') and r.request.method == 'POST') as refused:
                save.click()
            assert refused.value.status == 400
            assert current_cut(root)['paper_cut']['beats'][0]['text'] == 'Words saved in the second window.'
            assert hook.input_value() == 'My first window still has unsaved words.'
            assert not page.errors
        finally:
            close_writer(board, browser)


@pytest.mark.parametrize('saved_words', [False, True])
def test_redraw_after_recovery_keep_does_not_invent_another_window(film, provider, monkeypatch, saved_words):
    root, shot = film
    take = make_take(root, shot, signed=True)
    with sync_playwright() as playwright:
        board, browser, page = open_writer(root, monkeypatch, playwright)
        try:
            from tests.backlot.test_signing_outcome_browser import wait_for
            panel = page.get_by_role('article', name='Shot 1 footage', exact=True)
            cut, hook = hook_field(page)
            if saved_words:
                hook.fill(WORDS)
                save_cut(page, cut)
            before_words = hook.input_value()
            held = []
            # Keep the board's re-read pending after the signed take is applied.
            # This makes the brief opportunity for a second action deterministic.
            page.route('**/state', lambda route: held.append(route))
            watch(page)
            panel.get_by_role('button', name='Use signed keep in the cut', exact=True).click()
            wait_for(page, lambda: bool(held))
            assert placed_take(root).get('take_id') == take['take_id']
            assert signature(root, take['take_id'])
            # Re-picking the existing look redraws the board without changing it.
            look = panel.get_by_label('Approved look for reference 1', exact=True)
            look.select_option(look.input_value())
            assert hook.input_value() == before_words
            for route in held:
                route.continue_()
            page.unroute('**/state')
            panel.get_by_role('status').filter(has_text='Kept').wait_for(timeout=20000)
            expect(cut).not_to_contain_text('Another window saved')
            assert conflict_seen(page) is False
            save_cut(page, cut)
            assert current_cut(root)['paper_cut']['beats'][0]['text'] == before_words
            assert placed_take(root).get('take_id') == take['take_id']
            assert not page.errors and len(provider['posts']) == 1
        finally:
            close_writer(board, browser)


@pytest.mark.parametrize('route', ['normal', 'recovery'])
def test_unreadable_state_after_keep_preserves_words_and_revision(film, provider, monkeypatch, route):
    from backlot import footage_controls
    root, shot = film
    take = make_take(root, shot, signed=route == 'recovery')
    error = 'The paper cut could not be read. Earlier versions have been kept. Restore a safe copy before editing.'
    failed_reads = []
    with sync_playwright() as playwright:
        board, browser, page = open_writer(root, monkeypatch, playwright)
        try:
            panel = page.get_by_role('article', name='Shot 1 footage', exact=True)
            cut, hook = hook_field(page)
            hook.fill(WORDS)
            save_cut(page, cut)
            before = current_cut(root)
            def unreadable_state(project):
                # Only the separate read after apply fails. The keep and the
                # board's subsequent reads still use the real saved cut.
                assert placed_take(project).get('take_id') == take['take_id']
                failed_reads.append(True)
                return {'current': None, 'needs_review': True, 'usable_downstream': False, 'error': error}
            monkeypatch.setattr(footage_controls, 'paper_state', unreadable_state)
            if route == 'normal':
                panel.get_by_role('button', name='Keep this take', exact=True).click()
                page.locator('[aria-label="Footage request details"]').wait_for()
                sign(page, 'decision')
            else:
                panel.get_by_role('button', name='Use signed keep in the cut', exact=True).click()
            panel.get_by_role('status').filter(has_text='Kept').wait_for(timeout=20000)
            assert failed_reads
            expect(page.get_by_role('alert').filter(has_text=error).first).to_be_visible()
            expect(hook).to_have_value(WORDS)
            after_keep = current_cut(root)
            assert after_keep['paper_cut']['beats'][0]['text'] == WORDS
            assert placed_take(root).get('take_id') == take['take_id']
            assert signature(root, take['take_id'])
            # The preserved revision predates the applied keep. Saving must
            # refuse that stale revision, with the complete words still sent.
            with page.expect_response(lambda r: r.url.endswith('/paper-cut') and r.request.method == 'POST') as saved:
                cut.get_by_role('button', name='Save a new version', exact=True).click()
            assert saved.value.status == 400
            sent = saved.value.request.post_data_json
            assert sent['expected_revision'] == before['edit_revision_sha256']
            assert sent['paper']['beats'][0]['text'] == WORDS
            assert current_cut(root) == after_keep
            expect(hook).to_have_value(WORDS)
            assert not page.errors and len(provider['posts']) == 1
        finally:
            close_writer(board, browser)
