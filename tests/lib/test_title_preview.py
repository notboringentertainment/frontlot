"""Render cards through the real preview; wrong wording, frame or margins fail."""
import io,subprocess
import pytest
from tests.lib.test_cutting_view import media,cut
from tests.lib.test_trailer_titles import card
from tests.tools.test_signed_footage import film,provider,looks,workspace
from tests.title_renderer import require_title_renderer

def test_saved_card_pixels_and_exact_frame_boundaries(media,tmp_path):
 from lib import cutting_view as cutting,trailer_edit,trailer_titles as titles
 from lib.pathsafe import sha256_file
 from PIL import Image,ImageDraw,ImageFont,ImageChops
 root,shot,video,_=media;video['path']='assets/blue.mp4'
 subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','color=c=blue:s=640x360:r=24:d=3','-an',str(root/video['path'])],check=True)
 video['sha256']=sha256_file(root/video['path'])
 value=cut(shot);value['audio']=[];value['picture'][0].update(source_in_frame=0,source_out_frame=72)
 value['cards']=[]
 for i,(text,start,duration,background) in enumerate([('“Café — tonight.”',6,12,'black'),('Only the light remains.',30,12,'transparent'),('The quiet station',54,18,'black')]):
  row=card(text);row.update(id=f'card-{i}',start_frame=start,duration_frames=duration,background=background,role='end' if i==2 else 'intertitle');value['cards'].append(row)
 saved=cutting.save_cut(root,value,trailer_edit.read_revision(root)['edit_revision_sha256'])
 for row in value['cards']:looks.approve_request(titles.request_approval(root,row['id']),root)
 preview=cutting.render_preview(root,saved['edit_revision_sha256'])
 raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(preview),'-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
 def frame(n):return Image.frombytes('RGB',(640,360),raw[n*640*360*3:(n+1)*640*360*3])
 def white_mask(image):
  return image.convert('RGB').point(lambda x:255 if x>210 else 0).convert('L').point(lambda x:255 if x>240 else 0)
 for row in value['cards']:
  # Independent literal typesetting, not the production card renderer.
  expected=Image.new('RGB',(640,360),'black');draw=ImageDraw.Draw(expected)
  font=ImageFont.truetype(str(titles.REPO_ROOT/titles.FONT_PATH),32)
  draw.multiline_text((320,180),row['text'],font=font,fill='white',anchor='mm',align='center',spacing=8)
  mask=white_mask(expected);bbox=mask.getbbox();assert bbox[0]>=64 and bbox[2]<=576 and bbox[1]>=36 and bbox[3]<=324
  for n in (row['start_frame'],row['start_frame']+row['duration_frames']-1):
   actual=white_mask(frame(n));difference=ImageChops.difference(actual,mask)
   assert sum(difference.tobytes())/(640*360*255)<.002
  if row['start_frame']>0:assert white_mask(frame(row['start_frame']-1)).getbbox() is None
  if row['start_frame']+row['duration_frames']<72:assert white_mask(frame(row['start_frame']+row['duration_frames'])).getbbox() is None
 assert len(raw)==72*640*360*3
