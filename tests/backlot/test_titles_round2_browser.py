"""The real board shows the chosen lettering and every signed space."""
import os,json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace

def capture(page,name,target):
 folder=os.environ.get('TITLES_R2_CAPTURE')
 if not folder:return
 path=Path(folder)/('round2-'+os.environ.get('TITLES_R2_PREFIX','')+name+'.png');assert not path.exists()
 target.evaluate('(e)=>e.scrollIntoView({block:"center",behavior:"instant"})');expect(target).to_be_in_viewport();page.screenshot(path=str(path))

def test_card_accepts_seconds_in_both_timing_boxes(signed_cut,monkeypatch):
 root,provider,posts,*_=signed_cut
 with board(root,monkeypatch) as url,sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  try:
   page=browser.new_page(viewport={'width':1440,'height':1000});page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
   panel=page.get_by_role('region',name='Cutting view');expect(panel).to_contain_text('Saved version',timeout=30000)
   panel.get_by_role('button',name='Add text card',exact=True).click();panel.get_by_label('Exact words for card 1',exact=True).fill('Only the light.')
   for label,value,wanted in [('Starts at seconds','0.53','13'),('Lasts for seconds','0.5','12')]:
    field=panel.get_by_label(label+' for card 1',exact=True);field.fill(value);field.press('Tab')
    frame_label='Starts at frame' if label.startswith('Starts') else 'Lasts for frames'
    expect(panel.get_by_label(frame_label+' for card 1',exact=True)).to_have_value(wanted)
   panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('saved',timeout=30000)
   from lib.trailer_edit import read_revision
   saved=read_revision(root)['timeline']['cards'][0];assert saved['start_frame']==13 and saved['duration_frames']==12
   assert len(provider['posts'])==3 and len(posts)==1
  finally:browser.close()

def test_each_face_inline_accents_visible_spaces_and_real_signature(signed_cut,monkeypatch):
 from lib import trailer_titles as t,trailer_edit
 root,provider,posts,*_=signed_cut;raw='  cafe\u0301\nman\u0303ana\u00a0  '
 with board(root,monkeypatch) as url,sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  try:
   page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
   page.goto(f'{url}/p/{root.name}#k=invented-browser-proof');panel=page.get_by_role('region',name='Cutting view');expect(panel).to_contain_text('Saved version',timeout=30000)
   panel.get_by_role('button',name='Add text card',exact=True).click();panel.get_by_label('Exact words for card 1',exact=True).fill(raw)
   selector=panel.get_by_label('Title face for card 1',exact=True);expect(selector).to_be_visible(timeout=10000)
   canvases=[]
   for face,name in zip(t.fonts(),('patrick-hand','source-serif','source-sans')):
    selector.select_option(face['path']);panel.get_by_role('button',name='Preview words for card 1',exact=True).click()
    canvas=panel.get_by_label('Lettering preview for card 1',exact=True)
    expect(canvas).to_be_visible(timeout=15000);assert canvas.evaluate('(c)=>c.getContext("2d").getImageData(0,0,640,360).data.some((x,i)=>i%4===3&&x>0)')
    canvases.append(canvas.evaluate('(c)=>c.toDataURL()'));capture(page,name+'-accent-card',canvas)
   assert len(set(canvases))==3
   panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('saved',timeout=30000)
   saved=trailer_edit.read_revision(root)['timeline']['cards'][0];assert saved['text'].encode()==raw.encode() and saved['font_path']==t.fonts()[-1]['path']
   panel.get_by_role('button',name='Review and sign card 1',exact=True).click()
   review=page.get_by_role('region',name='Exact card review');expect(review).to_be_visible(timeout=15000)
   assert review.locator('pre').evaluate('(e)=>e.textContent')==raw
   expect(review.get_by_label('Visible spaces and line breaks',exact=True)).to_contain_text('··cafe')
   expect(review.get_by_label('Visible spaces and line breaks',exact=True)).to_contain_text('↵')
   expect(review.get_by_label('Visible spaces and line breaks',exact=True)).to_contain_text('⍽··')
   expect(review).to_contain_text('Source Sans 3')
   capture(page,'accents-and-visible-spaces',review)
   page.get_by_role('button',name='Start signing',exact=True).click();page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=30000)
   expect(page.locator('#gate-sign-panel').get_by_text('Your approval is recorded. You can close this.',exact=True)).to_be_visible(timeout=30000)
   page.get_by_role('button',name='Return to cutting view',exact=True).click();expect(panel.locator('[data-card]').first).to_contain_text('Signed by you',timeout=20000)
   panel.get_by_label('Exact words for card 1',exact=True).fill('A\u20dd');panel.get_by_role('button',name='Preview words for card 1',exact=True).click()
   expect(panel.locator('[data-card-preview-message]').first).to_contain_text('combining enclosing circle',timeout=15000)
   expect(panel.get_by_label('Lettering preview for card 1',exact=True)).to_be_hidden()
   capture(page,'unshowable-mark-refused',panel.locator('[data-card-preview-message]').first)
   assert len(provider['posts'])==3 and len(posts)==1 and not errors
  finally:browser.close()
