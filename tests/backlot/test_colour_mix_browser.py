"""A writer compares, adjusts and reopens a real signed local cut."""
import os,subprocess,json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.lib.test_trailer_titles import card
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.title_renderer import require_title_renderer

def test_colour_mix_controls_before_after_save_reopen_and_play(signed_cut,monkeypatch):
 from lib import trailer_edit,cutting_view,audio_workspace as a,trailer_titles as titles
 from lib.pathsafe import sha256_file
 root,provider,posts,*_=signed_cut
 for name,source in [('bed','sine=frequency=211:duration=3'),('door','anoisesrc=color=white:amplitude=0.2:duration=1:sample_rate=48000')]:
  args=['ffmpeg','-v','error','-n','-f','lavfi','-i',source]
  if name=='bed':args+=['-af','volume=6']
  subprocess.run([*args,str(root/f'assets/{name}-finish.wav')],check=True)
 brief=a.save_effect_brief(root,'An invented wooden latch.',1,False,0,0)
 music=a.import_music(root,root/'assets/bed-finish.wav','My invented bed','I made and own this test recording.')
 effect=a.import_music(root,root/'assets/door-finish.wav','My invented latch','I made and own this test recording.',kind='effect',brief_id=brief)
 for take in (music,effect):sign(root,'audio_take',a.decision_record(root,take['id'],'accept'),take['id'])
 saved=trailer_edit.read_revision(root);value=cutting_view.editable(saved['timeline'])
 value['audio']=[value['audio'][0],dict(take_id=music['id'],start_frame=0,source_in_frame=0,source_out_frame=72,level_db=-3,fade_in_frames=0,fade_out_frames=0),dict(take_id=effect['id'],start_frame=36,source_in_frame=0,source_out_frame=12,level_db=-6,fade_in_frames=0,fade_out_frames=0)]
 value['cards']=[]
 for i,(words,start,duration) in enumerate([('One lamp.',6,6),('A quiet room.',36,6),('The night gallery',60,12)]):
  item=card(words);item.update(id=f'finish-card-{i}',start_frame=start,duration_frames=duration,role='end' if i==2 else 'intertitle');value['cards'].append(item)
 cutting_view.save_cut(root,value,saved['edit_revision_sha256'])
 for item in value['cards']:looks.approve_request(titles.request_approval(root,item['id']),root)
 destination=os.environ.get('COLOUR_MIX_CAPTURE')
 def capture(page,name,target):
  if not destination:return
  directory=Path(destination);directory.mkdir(parents=True,exist_ok=True);path=directory/(os.environ.get('COLOUR_MIX_PREFIX','')+name+'.png');assert not path.exists()
  target.evaluate('(e)=>e.scrollIntoView({block:"center",behavior:"instant"})');expect(target).to_be_in_viewport();page.screenshot(path=str(path))
 with board(root,monkeypatch) as url,sync_playwright() as pw:
  if destination:print('Invented colour board:',url+'/p/'+root.name+'#k=invented-browser-proof',flush=True)
  browser=pw.chromium.launch(headless=True)
  try:
   page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
   page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
   panel=page.get_by_role('region',name='Cutting view');expect(panel).to_contain_text('Saved version',timeout=30000)
   # The first missing control is the red test before UI implementation.
   expect(panel.get_by_label('Brighter or darker for picture 1',exact=True)).to_be_visible(timeout=10000)
   panel.get_by_role('button',name='Compare before colour and mix',exact=True).click()
   expect(panel.get_by_role('status')).to_contain_text('Before colour and automatic balancing',timeout=240000)
   player=panel.locator('video');expect(player).to_have_js_property('readyState',4,timeout=20000)
   player.evaluate('(v)=>v.play()');page.wait_for_timeout(250);player.evaluate('(v)=>v.pause()');capture(page,'before',player)
   for _ in range(4):panel.get_by_label('Brighter or darker for picture 1',exact=True).press('ArrowRight')
   for _ in range(4):panel.get_by_label('Warmer or cooler for picture 2',exact=True).press('ArrowLeft')
   for _ in range(2):panel.get_by_label('More or less contrast for picture 1',exact=True).press('ArrowRight')
   for _ in range(2):panel.get_by_label('More or less colour for picture 2',exact=True).press('ArrowLeft')
   for _ in range(3):panel.get_by_label('Space below speech',exact=True).press('ArrowRight')
   expect(panel.get_by_label('Brighter or darker for picture 1',exact=True)).to_have_value('20')
   expect(player).not_to_have_attribute('src')
   expect(panel.get_by_role('status')).to_contain_text('Unsaved changes')
   capture(page,'unsaved-changes',panel.get_by_role('status'))
   panel.get_by_role('button',name='Compare before colour and mix',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('Save the cut')
   capture(page,'two-shot-adjustments',panel.locator('[data-colour-picture="2"]'))
   panel.get_by_role('button',name='Save cut',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('saved',timeout=30000)
   panel.get_by_role('button',name='Compare picture 2 after',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('ready',timeout=240000)
   expect(player).to_have_js_property('readyState',4,timeout=20000)
   assert player.evaluate('(v)=>v.currentTime')>=.99
   player.evaluate('(v)=>v.play()');page.wait_for_timeout(300);player.evaluate('(v)=>v.pause()');capture(page,'after-playing',player)
   review=panel.get_by_role('region',name='Picture and sound check');expect(review).to_contain_text('Loudness is on target')
   capture(page,'measured-output',review)
   page.reload();panel=page.get_by_role('region',name='Cutting view')
   expect(panel.get_by_label('Brighter or darker for picture 1',exact=True)).to_have_value('20',timeout=30000)
   expect(panel.get_by_label('Warmer or cooler for picture 2',exact=True)).to_have_value('-20')
   expect(panel.get_by_label('More or less contrast for picture 1',exact=True)).to_have_value('10')
   expect(panel.get_by_label('More or less colour for picture 2',exact=True)).to_have_value('-10')
   expect(panel.get_by_label('Space below speech',exact=True)).to_have_value('15')
   panel.get_by_role('button',name='Prepare saved playback',exact=True).click();expect(panel.get_by_role('status')).to_contain_text('ready',timeout=240000)
   player=panel.locator('video');expect(player).to_have_js_property('readyState',4,timeout=20000)
   player.evaluate('(v)=>v.play()');page.wait_for_timeout(300);player.evaluate('(v)=>v.pause()');capture(page,'reopened-playing',player)
   if destination:
    saved=trailer_edit.read_revision(root);previews=sorted((root/'artifacts/cut-previews').glob(saved['edit_revision_sha256']+'-*.mp4'),key=lambda p:p.stat().st_mtime)
    preview=previews[-1];report=json.loads(preview.with_suffix('.review.json').read_text());assert sha256_file(preview)==report['output_sha256']
    prefix=os.environ.get('COLOUR_MIX_PREFIX','')
    with (Path(destination)/(prefix+'colour-mix-playable-proof.mp4')).open('xb') as f:f.write(preview.read_bytes())
    with (Path(destination)/(prefix+'colour-mix-measurements.json')).open('x') as f:json.dump(report,f,indent=2)
   assert len(provider['posts'])==3 and len(posts)==1 and not errors
  finally:browser.close()
