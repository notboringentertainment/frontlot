"""The real cutting board saves, signs and plays exact local cards."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace

def test_cards_save_sign_changed_refusal_and_play(signed_cut,monkeypatch):
 root,provider,posts,*_=signed_cut
 folder=os.environ.get('TITLES_CAPTURE')
 if folder:Path(folder).mkdir(parents=True,exist_ok=True)
 def capture(page,name):
  if folder:
   path=Path(folder)/(name+'.png');assert not path.exists();page.screenshot(path=str(path))
 with board(root,monkeypatch) as url,sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  try:
   page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
   page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
   panel=page.get_by_role('region',name='Cutting view');expect(panel).to_contain_text('Saved version',timeout=30000)
   words=['“Café — tonight.”','Only the light remains.','The quiet station']
   for i,text in enumerate(words,1):
    panel.get_by_role('button',name='Add text card',exact=True).click()
    panel.get_by_label(f'Exact words for card {i}',exact=True).fill(text)
    panel.get_by_label(f'Starts at frame for card {i}',exact=True).fill(str((i-1)*24))
    panel.get_by_label(f'Lasts for frames for card {i}',exact=True).fill('24')
    if i==2:panel.get_by_label(f'Background for card {i}',exact=True).select_option('transparent')
    if i==3:panel.get_by_label(f'Card kind for card {i}',exact=True).select_option('end')
   panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('saved',timeout=30000)
   expect(panel.locator('[data-seconds-for="card 2:start_frame"]')).to_have_text('1.000 seconds')
   panel.get_by_role('button',name='Prepare saved playback',exact=True).click()
   expect(panel.get_by_role('status')).to_contain_text('signature',timeout=30000)
   panel.locator('[data-card]').first.scroll_into_view_if_needed();capture(page,'cards-unsigned')
   def sign_card(i):
    panel.get_by_role('button',name=f'Review and sign card {i}',exact=True).click()
    review=page.get_by_role('region',name='Exact card review');expect(review).to_be_visible(timeout=15000)
    expect(review.locator('pre')).to_have_text(words[i-1])
    if i==1:capture(page,'card-exact-words-review')
    page.get_by_role('button',name='Start signing',exact=True).click()
    page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
    expect(page.locator('#gate-sign-panel').get_by_text('Your approval is recorded. You can close this.',exact=True)).to_be_visible(timeout=30000)
    page.get_by_role('button',name='Return to cutting view',exact=True).click()
    expect(panel.get_by_role('button',name=f'Review and sign card {i}',exact=True)).to_be_visible(timeout=15000)
   for i in (1,2,3):sign_card(i)
   panel.get_by_role('button',name='Prepare saved playback',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('ready',timeout=240000)
   player=panel.locator('video');expect(player).to_have_js_property('readyState',4,timeout=15000)
   player.evaluate('(v)=>v.play()');page.wait_for_timeout(400);player.evaluate('(v)=>{v.pause();v.currentTime=2.5;}')
   expect(player).to_have_js_property('seeking',False,timeout=10000);panel.scroll_into_view_if_needed();capture(page,'three-cards-playing')
   words[0]='“Café — tomorrow!”'
   panel.get_by_label('Exact words for card 1',exact=True).fill(words[0]);panel.get_by_role('button',name='Save cut',exact=True).click()
   expect(panel.get_by_role('status')).to_contain_text('saved',timeout=30000)
   panel.get_by_role('button',name='Prepare saved playback',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('fresh look',timeout=15000)
   expect(player).not_to_have_attribute('src')
   panel.locator('[data-card]').first.scroll_into_view_if_needed();capture(page,'changed-card-refused')
   sign_card(1)
   panel.get_by_role('button',name='Prepare saved playback',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('ready',timeout=240000)
   player.evaluate('(v)=>v.play()');page.wait_for_timeout(500);player.evaluate('(v)=>v.pause()')
   panel.scroll_into_view_if_needed();capture(page,'changed-card-approved-playback')
   page.reload();panel=page.get_by_role('region',name='Cutting view');expect(panel.get_by_label('Exact words for card 1',exact=True)).to_have_value(words[0],timeout=30000)
   if folder:
    from lib import trailer_edit
    revision=trailer_edit.read_revision(root)['edit_revision_sha256'];preview=next((root/'artifacts/cut-previews').glob(revision+'-*.mp4'))
    with (Path(folder)/'titles-playable-proof.mp4').open('xb') as f:f.write(preview.read_bytes())
   assert len(provider['posts'])==3 and len(posts)==1 and not errors
  finally:browser.close()
