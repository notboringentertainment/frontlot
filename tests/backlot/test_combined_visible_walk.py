"""Empty library to separately signed speech and footage, on the icon PATH."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_early_paid_readiness import isolated_standins,start_browser,set_prices,save_paper_with_controls,buy_three_audio,shot_picture
from tests.backlot.test_footage_controls_browser import browser_board
from tests.backlot.footage_prereq_helpers import complete_footage_prerequisites,simple_sign

def test_empty_library_to_kept_voice_and_footage(tmp_path,monkeypatch,isolated_standins):
    from tests.tools import test_signed_footage as fake
    # Prepare the stand-in's downloaded bytes before starting the board. All
    # real filing and board operations below run with the icon's short PATH.
    clip=fake.clip_bytes();monkeypatch.setattr(fake,'clip_bytes',lambda:clip)
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    projects=tmp_path/'empty-library';projects.mkdir()
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',projects);monkeypatch.setattr('lib.events.PROJECTS_DIR',projects)
    with browser_board(projects/'not-created',monkeypatch) as url,sync_playwright() as pw:
        browser,page=start_browser(pw,url)
        try:
            page.goto(url.split('/p/')[0]+'/#k=invented-browser-writer')
            page.get_by_text('Start a trailer',exact=True).click()
            for label,value in [('Trailer name','Invented combined gallery'),('Target length in seconds','28'),('Total spending ceiling in dollars','10')]:page.get_by_label(label,exact=True).fill(value)
            shot_picture(page,'creation',page.get_by_role('button',name='Create trailer and review signing'))
            page.get_by_role('button',name='Create trailer and review signing').click()
            page.get_by_role('button').filter(has=page.locator('span.what',has_text='Start this trailer')).click()
            page.locator('#gate-detail .gate-primary').click();page.get_by_role('button',name='Approve and sign').click(timeout=20000)
            story=page.get_by_role('region',name='Trailer story')
            story.get_by_label('Exact story text',exact=True).fill('A gallery waits.\nThe gallery is closed.\nKeep the light on.')
            story.get_by_role('button',name='Save story for review').click()
            story.get_by_role('button',name='Review and sign this story').click()
            shot_picture(page,'story-signing',page.get_by_role('region',name='Story approval'))
            simple_sign(page,'Return to story')
            page.get_by_role('button',name='Voice workspace',exact=True).click()
            audio=page.get_by_role('region',name='Trailer audio')
            expect(audio.locator('.voice-budget')).to_contain_text('$10.0000 remaining of $10.0000')
            set_prices(page,audio,'combined')
            paper=save_paper_with_controls(page,purpose='A wide view of the invented empty gallery. No people.')
            paper.get_by_role('button',name='Check paper cut against approved story').click()
            expect(paper.get_by_role('status').first).to_contain_text('compared')
            page.get_by_role('button',name='Voice workspace',exact=True).click()
            buy_three_audio(page,audio,isolated_standins,'combined',kinds=('speech',))
            root=next(p for p in projects.iterdir() if (p/'project.json').exists())
            complete_footage_prerequisites(page,root,isolated_standins,tmp_path,audio_count=1,check_changed=False)
            assert len(isolated_standins['audio_posts'])==1 and len(isolated_standins['video_posts'])==1
            assert not page.errors
        finally:browser.close()
