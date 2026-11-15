"""Keeping a take must not claim that another window saved the paper cut."""
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


def test_normal_keep_after_saving_words_keeps_those_words(film, provider, monkeypatch):
    root, shot = film
    take = make_take(root, shot)
    with sync_playwright() as playwright:
        board, browser, page = open_writer(root, monkeypatch, playwright)
        try:
            panel = page.get_by_role('article', name='Shot 1 footage', exact=True)
            cut, hook = hook_field(page)
            hook.fill(WORDS)
            save_cut(page, cut)
            assert hook.input_value() == WORDS
            assert current_cut(root)['paper_cut']['beats'][0]['text'] == WORDS
            page.reload()
            panel = page.get_by_role('article', name='Shot 1 footage', exact=True)
            panel.get_by_role('button', name='Keep this take', exact=True).wait_for()
            cut, hook = hook_field(page)
            assert hook.input_value() == WORDS
            watch(page)
            panel.get_by_role('button', name='Keep this take', exact=True).click()
            page.locator('[aria-label="Footage request details"]').wait_for()
            sign(page, 'decision')
            panel.get_by_role('status').filter(has_text='Kept').wait_for(timeout=20000)
            expect(cut).not_to_contain_text('Another window saved')
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


def test_recovery_keep_with_a_clean_editor_does_not_invent_another_window(film, provider, monkeypatch):
    root, shot = film
    take = make_take(root, shot, signed=True)
    with sync_playwright() as playwright:
        board, browser, page = open_writer(root, monkeypatch, playwright)
        try:
            panel = page.get_by_role('article', name='Shot 1 footage', exact=True)
            panel.get_by_role('button', name='Use signed keep in the cut', exact=True).wait_for()
            cut, _hook = hook_field(page)
            watch(page)
            panel.get_by_role('button', name='Use signed keep in the cut', exact=True).click()
            panel.get_by_role('status').filter(has_text='Kept').wait_for(timeout=20000)
            expect(cut).not_to_contain_text('Another window saved')
            assert conflict_seen(page) is False
            assert placed_take(root).get('take_id') == take['take_id']
            assert placed_take(root).get('kind') == 'footage'
            assert signature(root, take['take_id'])
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
