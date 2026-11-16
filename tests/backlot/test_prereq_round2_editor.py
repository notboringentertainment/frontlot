"""Same-page picture assignment is version-bound and cannot discard writing."""
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.lib.test_story_ingest import root
from tests.lib.look_lock_helpers import approve_request
from tests.lib.test_frontlot_location_source import form
from tests.lib.test_trailer_edit import paper
from tests.backlot.test_footage_prereq_panel import picture
from tests.backlot.test_early_paid_readiness import isolated_standins,start_browser,shot_picture
from tests.backlot.test_footage_controls_browser import browser_board

def prepared(root):
    from lib import story_ingest,trailer_edit,frontlot_location_source as locations
    from backlot import footage_prereqs
    approve_request(story_ingest.supply(root,b'The gallery is closed.')['gate'],root)
    value=paper();value['shots'][0]['duration_frames']=672
    trailer_edit.save_revision(root,value,None)
    look=locations.prepare(root,form());approve_request(look['gate'],root)
    image=footage_prereqs.prepare_picture(root,picture(look['look_refs'][0]));receipt=approve_request(image['gate'],root)
    return look['look_refs'][0],receipt['receipt_id']

def test_assigning_a_picture_updates_the_same_pages_clean_paper_cut(root,monkeypatch,isolated_standins):
    from lib import trailer_edit
    prepared(root)
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url)
            setup=page.get_by_role('region',name='Footage prerequisites',exact=True)
            cut=page.get_by_role('region',name='Trailer paper cut',exact=True)
            setup.get_by_role('button',name='Use signed picture for selected shot',exact=True).click()
            expect(setup.get_by_role('status')).to_contain_text('picture')
            refs=cut.get_by_text('References, separated by commas',exact=True).locator('..').locator('input')
            expect(refs).not_to_have_value('')
            expect(cut).not_to_contain_text('Another window saved')
            shot_picture(page,'round2-picture-in-current-paper-cut',refs)
            before=trailer_edit.read_revision(root)['paper_cut']['shots'][0]['references']
            cut.get_by_role('button',name='Save a new version',exact=True).click()
            expect(cut.get_by_role('status').first).to_contain_text('Saved.')
            assert trailer_edit.read_revision(root)['paper_cut']['shots'][0]['references']==before
            assert not page.errors and all(not v for v in isolated_standins.values())
        finally:browser.close()

def test_unsaved_words_must_be_saved_before_assigning_a_picture(root,monkeypatch,isolated_standins):
    from lib import trailer_edit
    prepared(root);before=trailer_edit.read_revision(root)
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url);setup=page.get_by_role('region',name='Footage prerequisites',exact=True)
            cut=page.get_by_role('region',name='Trailer paper cut',exact=True)
            hook=cut.get_by_label('Hook',exact=True)
            hook.fill('Keep these newly written words.')
            setup.get_by_role('button',name='Use signed picture for selected shot',exact=True).click()
            expect(setup.get_by_role('alert')).to_contain_text('Save your changed paper cut')
            shot_picture(page,'round2-save-words-before-picture',setup.get_by_role('alert'))
            assert hook.input_value()=='Keep these newly written words.'
            assert trailer_edit.read_revision(root)==before
            cut.get_by_role('button',name='Save a new version',exact=True).click()
            expect(cut.get_by_role('status').first).to_contain_text('Saved.')
            setup.get_by_role('button',name='Use signed picture for selected shot',exact=True).click()
            expect(setup.get_by_role('status')).to_contain_text('picture')
            expect(cut).not_to_contain_text('Another window saved')
            assert trailer_edit.read_revision(root)['paper_cut']['beats'][0]['text']==hook.input_value()
            assert not page.errors
        finally:browser.close()

def test_picture_assignment_refuses_a_version_that_changed_elsewhere(root):
    from lib import trailer_edit
    from backlot.footage_prereqs import use_picture
    look,receipt=prepared(root);old=trailer_edit.read_revision(root)
    next_cut=old['paper_cut'];next_cut['beats'][0]['text']='A new hook from another window.'
    new=trailer_edit.save_revision(root,next_cut,old['edit_revision_sha256'])
    with pytest.raises(ValueError,match='changed elsewhere'):
        use_picture(root,{'receipt_id':receipt,'shot_id':next_cut['shots'][0]['id'],'look':look,'expected_revision':old['edit_revision_sha256']})
    assert trailer_edit.read_revision(root)==new

def test_old_refresh_cannot_replace_state_after_a_save(root,monkeypatch,isolated_standins):
    from lib import trailer_edit
    from tests.backlot.test_signing_outcome_browser import wait_for
    prepared(root)
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url);held=[]
        try:
            page.goto(url);cut=page.get_by_role('region',name='Trailer paper cut',exact=True)
            def hold_first(route):
                if not held and route.request.method=='GET':held.append((route,route.fetch().json()))
                else:route.continue_()
            page.route('**/paper-cut',hold_first)
            # Do not let a later SSE-triggered board read mask the stale one.
            page.route('**/state',lambda route:None)
            cut.get_by_role('button',name='Refresh story checks and saved versions',exact=True).click()
            wait_for(page,lambda:bool(held))
            hook=cut.get_by_label('Hook',exact=True);hook.fill('A newly saved hook.')
            cut.get_by_role('button',name='Save a new version',exact=True).click()
            expect(cut.get_by_role('status').first).to_contain_text('Saved.')
            current=trailer_edit.read_revision(root)
            assert held[0][1]['current']['edit_revision_sha256']!=current['edit_revision_sha256']
            # Observe even a brief false conflict; another UI update may follow.
            page.evaluate('''()=>{window.paperConflictSeen=false;const cut=document.querySelector('[aria-label="Trailer paper cut"]');
                const watch=new MutationObserver(()=>{if(cut.textContent.includes('Another window saved'))window.paperConflictSeen=true;});watch.observe(cut,{subtree:true,childList:true,characterData:true});}''')
            held[0][0].fulfill(json=held[0][1])
            expect(cut.get_by_role('status').first).to_contain_text('Story checks and versions refreshed')
            assert hook.input_value()=='A newly saved hook.'
            expect(cut).not_to_contain_text('Another window saved')
            assert not page.evaluate('()=>window.paperConflictSeen')
            cut.get_by_role('button',name='Check paper cut against approved story',exact=True).click()
            expect(cut.get_by_role('status').first).not_to_contain_text('changed elsewhere')
            expect(cut.get_by_role('button',name='Check paper cut against approved story',exact=True)).to_be_enabled()
            assert trailer_edit.read_revision(root)['paper_cut']==current['paper_cut']
            assert not page.errors
        finally:browser.close()

def test_returning_to_a_preserved_draft_keeps_its_original_identity(root,monkeypatch,isolated_standins):
    from lib import trailer_edit
    from backlot.footage_prereqs import use_picture
    look,receipt=prepared(root)
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url);cut=page.get_by_role('region',name='Trailer paper cut',exact=True)
            hook=cut.get_by_label('Hook',exact=True);hook.fill('My preserved words.')
            old=trailer_edit.read_revision(root)
            use_picture(root,{'receipt_id':receipt,'shot_id':old['paper_cut']['shots'][0]['id'],'look':look,'expected_revision':old['edit_revision_sha256']})
            current=trailer_edit.read_revision(root)
            cut.get_by_role('button',name='Refresh story checks and saved versions',exact=True).click()
            cut.get_by_role('button',name='Open the latest saved version',exact=True).click()
            cut.get_by_role('button',name='Use this latest version and keep my unsaved draft',exact=True).click()
            cut.get_by_role('button',name='Return to my unsaved draft',exact=True).click()
            assert hook.input_value()=='My preserved words.'
            with page.expect_response(lambda r:r.url.endswith('/paper-cut') and r.request.method=='POST') as refused:
                cut.get_by_role('button',name='Save a new version',exact=True).click()
            assert refused.value.status==400
            assert trailer_edit.read_revision(root)==current and not page.errors
        finally:browser.close()
