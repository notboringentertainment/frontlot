"""Writing and revision ownership survive delayed board requests."""
import json
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.lib.test_story_ingest import root
from tests.lib.look_lock_helpers import approve_request
from tests.backlot.test_early_paid_readiness import isolated_standins,start_browser
from tests.backlot.test_footage_controls_browser import browser_board
from tests.backlot.test_signing_outcome_browser import wait_for


@pytest.mark.parametrize('field',['Exact story text','Protected wording 1','Spoiler passage 1'])
def test_story_save_locks_every_word_until_refresh_finishes(root,monkeypatch,isolated_standins,field):
    from lib import story_ingest as story
    story.supply(root,b'The gallery is closed. Keep the light on.',protected=['Keep the light on.'],spoilers=['The gallery is closed.'])
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url);panel=page.get_by_role('region',name='Trailer story',exact=True)
            area=panel.get_by_label(field,exact=True);expect(area).not_to_have_value('')
            original=area.input_value();held=[]
            page.route('**/story',lambda route:held.append(route) if route.request.method=='POST' else route.continue_())
            panel.get_by_role('button',name='Save story for review',exact=True).click()
            wait_for(page,lambda:bool(held));expect(area).to_be_disabled()
            # Keystrokes during the locked save cannot enter and then vanish.
            page.keyboard.insert_text(' Never lose these words.')
            held.pop().continue_();expect(area).to_be_enabled(timeout=10000)
            assert area.input_value()==original
            area.fill(original+' Later words.' if field=='Exact story text' else original)
            assert area.input_value().endswith('Later words.') if field=='Exact story text' else area.input_value()==original
            assert not page.errors and all(not v for v in isolated_standins.values())
        finally:browser.close()


def test_old_window_cannot_adopt_new_revision_by_checking(root,monkeypatch,isolated_standins):
    from lib import story_ingest as story,trailer_edit as edit
    from tests.lib.test_trailer_edit import paper
    approve_request(story.supply(root,b'The gallery is closed.')['gate'],root)
    value=paper();value['shots'][0]['duration_frames']=672
    edit.save_revision(root,value,None)
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url);other=browser.new_page();other.goto(url)
            first=page.get_by_role('region',name='Trailer paper cut',exact=True)
            second=other.get_by_role('region',name='Trailer paper cut',exact=True)
            latest='A newer hook written in another window.'
            second.get_by_text('Hook',exact=True).locator('..').locator('textarea').fill(latest)
            second.get_by_role('button',name='Save a new version',exact=True).click()
            expect(second.get_by_role('status')).to_contain_text('Saved.')
            current=edit.read_revision(root)
            first.get_by_role('button',name='Check paper cut against approved story',exact=True).click()
            expect(first.get_by_role('status')).to_contain_text('changed elsewhere',timeout=10000)
            expect(first.get_by_role('button',name='Open the latest saved version',exact=True)).to_be_visible()
            first.get_by_role('button',name='Save a new version',exact=True).click()
            expect(first.get_by_role('status')).not_to_contain_text('Saved.')
            assert edit.read_revision(root)==current
            assert not page.errors and all(not v for v in isolated_standins.values())
        finally:browser.close()


@pytest.mark.parametrize('kind',['paper','look'])
def test_other_writing_panels_lock_through_their_save(root,monkeypatch,isolated_standins,kind):
    with browser_board(root,monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url);held=[]
        try:
            page.goto(url)
            if kind=='paper':
                panel=page.get_by_role('region',name='Trailer paper cut',exact=True)
                area=panel.get_by_text('Hook',exact=True).locator('..').locator('textarea')
                area.fill('A gallery waits.');path='**/paper-cut';save='Save a new version'
                # A complete minimal draft; no story signature is claimed.
                panel.get_by_role('button',name='Add shot',exact=True).click()
                panel.get_by_text('Purpose (private plan)',exact=True).locator('..').locator('textarea').fill('An empty gallery.')
                panel.get_by_text('Difficulty',exact=True).locator('..').locator('input').fill('simple')
            else:
                from tests.lib.test_frontlot_location_source import form
                panel=page.get_by_role('region',name='Footage prerequisites',exact=True)
                labels=['Location name','Exact location description','Establishing view','Default time of day','Palette anchors, one per line','Architecture or terrain','Dressing, one item per line','Weather and light rules','Continuity risks, one per line','Things to avoid, one per line']
                data=form()
                for label,key in zip(labels,['name','text','establishing_view','time_of_day_default','palette_anchors','architecture_or_terrain','dressing','weather_or_light_rules','continuity_risks','negative_lines']):
                    value=data[key];panel.get_by_label(label,exact=True).fill('\n'.join(value) if isinstance(value,list) else value)
                panel.get_by_label('This is an invented location, not a person or character.').check()
                area=panel.get_by_label('Exact location description',exact=True);path='**/footage-setup/look';save='Prepare location look for signing'
            value=area.input_value()
            page.route(path,lambda route:held.append(route) if route.request.method=='POST' else route.continue_())
            panel.get_by_role('button',name=save,exact=True).click();wait_for(page,lambda:bool(held))
            expect(area).to_be_disabled();page.keyboard.insert_text(' Never disappear.')
            held.pop().continue_()
            if kind=='paper':expect(area).to_be_enabled(timeout=10000);assert area.input_value()==value
            else:expect(page.get_by_role('region',name='Location source approval',exact=True)).to_be_visible(timeout=10000)
            assert not page.errors
        finally:
            for route in held:route.abort()
            browser.close()
