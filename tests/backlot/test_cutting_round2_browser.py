"""Real board controls preserve an unusable cut and restore earlier timelines."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_footage_controls_browser import sign
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace

def picture(page,name,target):
    folder=os.environ.get('CUTTING_R2_CAPTURE')
    if not folder:return
    target.evaluate('(e)=>e.scrollIntoView({block:"center",behavior:"instant"})')
    expect(target).to_be_in_viewport()
    path=Path(folder)/('round2-'+os.environ.get('CUTTING_R2_CAPTURE_TAG','')+name+'.png');assert not path.exists()
    page.screenshot(path=str(path))

def test_decline_open_replace_restore_and_remove(signed_cut,monkeypatch):
    from lib.trailer_edit import read_revision
    root,provider,posts,_,_=signed_cut;old=read_revision(root)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            shot=page.get_by_role('article',name='Shot 1 footage',exact=True)
            tile=shot.locator('.take-tile').nth(1);video=tile.locator('video')
            expect(video).to_have_js_property('readyState',4,timeout=20000)
            video.evaluate('(v)=>{v.currentTime=v.duration-.15;return v.play();}')
            expect(video).to_have_js_property('ended',True,timeout=15000)
            tile.get_by_role('button',name='Decline this take',exact=True).click();sign(page,'decision')
            page.get_by_role('button',name='Cutting workspace',exact=True).click()
            panel=page.get_by_role('region',name='Cutting view')
            expect(panel.locator('[data-cut-kind="picture"]')).to_have_count(3,timeout=15000)
            expect(panel.locator('[data-cut-kind="audio"]')).to_have_count(3)
            row=panel.locator('[data-cut-kind="picture"]').nth(1);expect(row).to_contain_text('declined',timeout=15000)
            picture(page,'declined-clip-kept-on-screen',row)
            panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('Picture 2',timeout=15000)
            panel.get_by_role('button',name='Prepare saved playback',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('declined')
            row.get_by_label('Replacement for picture 2',exact=True).select_option('0')
            row.get_by_role('button',name='Replace picture 2',exact=True).click()
            panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('saved',timeout=15000)
            panel.get_by_label('Saved cut versions',exact=True).select_option(old['edit_revision_sha256'])
            panel.get_by_role('button',name='Restore selected cut',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('restored',timeout=15000)
            expect(panel.locator('[data-cut-kind="picture"]').nth(1)).to_contain_text('declined')
            expect(panel.locator('[data-cut-kind="audio"]')).to_have_count(3)
            expect(panel.get_by_label('Source in frame for picture 1',exact=True)).to_have_value('12')
            picture(page,'restored-cut-with-declined-clip',panel.locator('[data-cut-kind="picture"]').nth(1))
            panel.locator('[data-cut-kind="picture"]').nth(1).get_by_role('button',name='Remove this clip',exact=True).click()
            panel.get_by_role('button',name='Close picture gaps',exact=True).click()
            panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('saved',timeout=30000)
            panel.get_by_role('button',name='Prepare saved playback',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('ready',timeout=30000)
            panel.locator('video').evaluate('(v)=>v.play()');page.wait_for_timeout(300);panel.locator('video').evaluate('(v)=>v.pause()')
            picture(page,'repaired-cut-plays',panel.locator('video'))
            assert read_revision(root)['timeline']['audio']==old['timeline']['audio']
            assert len(provider['posts'])==3 and len(posts)==1 and not errors
        finally:browser.close()

def test_seconds_round_to_frames_gaps_and_safe_new_clip_default(signed_cut,monkeypatch):
    root,provider,posts,_,_=signed_cut
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof');panel=page.get_by_role('region',name='Cutting view')
            expect(panel).to_contain_text('Saved version',timeout=20000)
            panel.get_by_role('button',name='Add picture',exact=True).click()
            expect(panel.get_by_label('Source out frame for picture 4',exact=True)).to_have_value('24')
            panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('saved',timeout=30000)
            panel.get_by_role('button',name='Add picture',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('No room')
            seconds=panel.get_by_label('Source in seconds for picture 1',exact=True);seconds.fill('0.55');seconds.press('Tab')
            expect(panel.get_by_label('Source in frame for picture 1',exact=True)).to_have_value('13')
            expect(seconds).to_have_value('0.542')
            expect(panel.locator('[data-gap-note]')).to_contain_text('gap')
            expect(panel.locator('[data-cut-kind="picture"]').first).to_contain_text('Take 1')
            expect(panel.locator('[data-cut-kind="picture"]').first.locator('img')).to_have_js_property('naturalWidth',320,timeout=20000)
            picture(page,'seconds-and-take-identification',panel.locator('[data-cut-kind="picture"]').first)
            panel.get_by_role('button',name='Close picture gaps',exact=True).click()
            expect(panel.get_by_label('Starts at frame for picture 2',exact=True)).to_have_value('23')
            expect(panel.locator('[data-gap-note]')).to_contain_text('No picture gaps')
            panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('saved',timeout=30000)
            assert len(provider['posts'])==3 and len(posts)==1 and not errors
        finally:browser.close()
