"""Local playback on the actual board; media verifiers are explicit stand-ins."""
import hashlib,json,subprocess,os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace

def test_build_save_play_seek_trim_and_reopen(signed_cut,monkeypatch):
 root,provider,posts,_timeline,_take=signed_cut
 gets=[]
 folder=Path('/Users/ben/Projects/housekeeping/screens-frontlot-cutting-view-2026-11-13');folder.mkdir(exist_ok=True)
 with board(root,monkeypatch) as url,sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  try:
   page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(f'{url}/p/{root.name}#k=invented-browser-proof');panel=page.get_by_role('region',name='Cutting view')
   expect(panel).to_contain_text('Saved version',timeout=15000)
   while panel.get_by_role('button',name='Remove this clip',exact=True).count():panel.get_by_role('button',name='Remove this clip',exact=True).first.click()
   for i in range(3):
    panel.get_by_label('Kept picture take').select_option(str(i));panel.get_by_role('button',name='Add picture',exact=True).click()
   for i in range(3):
    panel.get_by_label(f'Starts at frame for picture {i+1}').fill(str(i*24));panel.get_by_label(f'Source out frame for picture {i+1}').fill('24')
   for i in range(3):
    panel.get_by_label('Kept sound take').select_option(str(i));panel.get_by_role('button',name='Add sound',exact=True).click()
   panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('saved',timeout=30000)
   panel.get_by_role('button',name='Prepare saved playback',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('ready',timeout=30000)
   player=panel.locator('video');expect(player).to_have_js_property('readyState',4,timeout=15000)
   player.evaluate('(v)=>v.play()');page.wait_for_timeout(700);player.evaluate('(v)=>{v.pause();v.currentTime=1.5;}')
   expect(player).to_have_js_property('seeking',False,timeout=5000)
   panel.evaluate('(e)=>e.scrollIntoView({block:"start"})')
   if os.environ.get('CUTTING_CAPTURE'):page.screenshot(path=str(folder/(os.environ['CUTTING_CAPTURE']+'-first-play.png')))
   panel.get_by_label('Source in frame for picture 1').fill('12');panel.get_by_label('Source out frame for picture 1').fill('24')
   panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('saved',timeout=30000)
   page.reload();panel=page.get_by_role('region',name='Cutting view');expect(panel.get_by_label('Source in frame for picture 1')).to_have_value('12',timeout=15000)
   panel.get_by_role('button',name='Prepare saved playback',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('ready',timeout=30000)
   panel.locator('video').evaluate('(v)=>v.play()');page.wait_for_timeout(500);panel.locator('video').evaluate('(v)=>v.pause()')
   panel.evaluate('(e)=>e.scrollIntoView({block:"start"})')
   if os.environ.get('CUTTING_CAPTURE'):
    page.screenshot(path=str(folder/(os.environ['CUTTING_CAPTURE']+'-reopened-trim.png')))
    preview=next((root/'artifacts/cut-previews').glob('*.mp4'));destination=folder/(os.environ['CUTTING_CAPTURE']+'-preview.mp4')
    with destination.open('xb') as f:f.write(preview.read_bytes())
   assert len(provider['posts'])==3 and len(posts)==1 and gets==[] and errors==[]
  finally:browser.close()
