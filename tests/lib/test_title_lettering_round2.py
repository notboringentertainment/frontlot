"""Typed characters stay exact; only the drawing composes their marks."""
import io,unicodedata
import pytest
from PIL import Image
from tests.lib.test_trailer_titles import card
from tests.lib.test_cutting_view import media
from tests.tools.test_signed_footage import film,provider,looks,workspace

@pytest.mark.parametrize('separate,normal',[
 ('cafe\u0301','café'),('man\u0303ana','mañana'),('Zoe\u0308','Zoë'),('nai\u0308ve','naïve'),('A\u0300','À'),('o\u0302','ô')])
def test_separate_marks_draw_identically_to_the_single_letter(separate,normal):
 from lib import trailer_titles as t
 a=Image.open(io.BytesIO(t.card_png(card(separate))));b=Image.open(io.BytesIO(t.card_png(card(normal))))
 assert a.tobytes()==b.tobytes()

@pytest.mark.parametrize('mark',['\u20dd','\u034f','\u093e'])
def test_uncomposable_marks_are_refused_and_named(mark):
 from lib import trailer_titles as t
 with pytest.raises(ValueError,match=unicodedata.name(mark).lower()):t.card_png(card('A'+mark))

def test_every_unicode_mark_is_composed_or_refused_explicitly():
 from lib.trailer_titles import drawing_text
 for n in range(0x110000):
  c=chr(n)
  if not unicodedata.category(c).startswith('M'):continue
  try:drawn=drawing_text('A'+c)
  except ValueError as e:
   assert 'mark' in str(e) and unicodedata.name(c,'unnamed mark').lower() in str(e)
  else:assert not any(unicodedata.category(x).startswith('M') for x in drawn)

def test_nonbreaking_space_draws_as_a_space_but_stays_in_signed_text(media):
 from lib import trailer_titles as t,cutting_view,trailer_edit
 root,*_=media;raw='  A\u00a0quiet room.  '
 assert Image.open(io.BytesIO(t.card_png(card(raw)))).tobytes()==Image.open(io.BytesIO(t.card_png(card(raw.replace('\u00a0',' '))))).tobytes()
 from tests.lib.test_cutting_view import cut
 root,shot,*_=media;value=cut(shot);value['cards']=[card(raw)]
 saved=cutting_view.save_cut(root,value,trailer_edit.read_revision(root)['edit_revision_sha256'])
 assert saved['timeline']['cards'][0]['text'].encode()==raw.encode()

def test_both_clean_faces_are_local_licensed_and_bound_to_the_card(media):
 from lib import trailer_titles as t,cutting_view,trailer_edit
 from lib.pathsafe import sha256_file
 faces=t.fonts();assert any('Source Serif 4' in f['name'] for f in faces) and any('Source Sans 3' in f['name'] for f in faces)
 images=[]
 for face in faces:
  path=t.REPO_ROOT/face['path'];assert path.is_file() and sha256_file(path)==face['sha256']
  if 'Source' in face['name']:
   assert 'SIL OPEN FONT LICENSE Version 1.1' in (path.parent/'OFL.txt').read_text()
  value=card('The quiet station');value.update(font_path=face['path'],font_sha256=face['sha256']);images.append(t.card_png(value))
 assert len(set(images))==len(faces)
 root,shot,*_=media
 from tests.lib.test_cutting_view import cut
 value=cut(shot);value['cards']=[card()];first=cutting_view.save_cut(root,value,trailer_edit.read_revision(root)['edit_revision_sha256'])
 looks.approve_request(t.request_approval(root,'invented-card'),root)
 value['cards'][0].update(font_path=faces[-1]['path'],font_sha256=faces[-1]['sha256'])
 changed=cutting_view.save_cut(root,value,first['edit_revision_sha256'])
 assert t.status(root,changed['timeline']['cards'][0])=='Needs a fresh look'

def test_missing_renderer_is_a_plain_test_skip(tmp_path,monkeypatch):
 from tests.title_renderer import require_title_renderer
 monkeypatch.setenv('FRONTLOT_REMOTION_MODULES',str(tmp_path/'not-installed'))
 with pytest.raises(pytest.skip.Exception,match='Local title playback needs the installed Remotion tools'):
  require_title_renderer.__wrapped__()
