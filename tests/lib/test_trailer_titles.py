"""Removing signature, literal-text, layout or timing checks breaks these cases."""
import copy,io
import pytest
from tests.lib.test_cutting_view import media,cut
from tests.tools.test_signed_footage import film,provider,looks,workspace

def card(text='“Café — tonight.”',**changes):
 from lib.trailer_titles import fonts
 font=fonts()[0]
 return dict(id='invented-card',text=text,font_path=font['path'],font_sha256=font['sha256'],font_size=32,start_frame=0,duration_frames=12,background='black',role='intertitle',**changes)

def test_unsigned_and_changed_text_save_but_never_play(media):
 from lib import cutting_view as cutlib,trailer_edit,trailer_titles as titles
 root,shot,*_=media;timeline=cut(shot);timeline['cards']=[card()]
 saved=cutlib.save_cut(root,timeline,trailer_edit.read_revision(root)['edit_revision_sha256'])
 with pytest.raises(ValueError,match='signature'):cutlib.validate_cut(root,cutlib.editable(saved['timeline']))
 gate=titles.request_approval(root,'invented-card');looks.approve_request(gate,root)
 assert cutlib.validate_cut(root,cutlib.editable(saved['timeline']))['cards'][0]['text']=='“Café — tonight.”'
 timeline['cards'][0]['text']='“Café — tomorrow!”'
 changed=cutlib.save_cut(root,timeline,saved['edit_revision_sha256'])
 assert titles.status(root,changed['timeline']['cards'][0])=='Needs a fresh look'
 with pytest.raises(ValueError,match='fresh look'):cutlib.validate_cut(root,cutlib.editable(changed['timeline']))
 assert trailer_edit.read_revision(root,saved['edit_revision_sha256'])==saved
 assert not (root/'checkpoint_edit.json').exists()

def test_text_bytes_spaces_accents_and_quotes_survive_layout(media):
 from lib import trailer_titles as titles
 root,*_=media;text='  “À bientôt!”\nCafé’s last light…  '
 value=card(text)
 checked=titles.validate_cards(root,[value],24,require_approval=False)[0]
 assert checked['text'].encode()==text.encode()
 from PIL import Image,ImageDraw,ImageFont
 image=Image.open(io.BytesIO(titles.card_png(checked))).convert('RGBA')
 expected=Image.new('RGBA',(640,360),(0,0,0,0))
 ImageDraw.Draw(expected).multiline_text((320,180),text,font=ImageFont.truetype(str(titles.REPO_ROOT/titles.FONT_PATH),32),fill='#FFFFFF',anchor='mm',align='center',spacing=8)
 assert image.tobytes()==expected.tobytes()
 x0,y0,x1,y1=image.getbbox()
 assert x0>=64 and x1<=576 and y0>=36 and y1<=324

@pytest.mark.parametrize('change,reason',[
 ({'text':'A very long title '*40},'safe margins'),
 ({'font_path':'missing.ttf'},'font'),
 ({'font_sha256':'0'*64},'font'),
 ({'text':'No glyph: 🦕'},'letter'),
 ({'text':'a\rb'},'control'),
 ({'start_frame':.5},'whole frames'),
 ({'duration_frames':25},'after the cut'),
 ({'duration_frames':0},'whole frames'),
 ({'font_size':100},'letter size'),
 ({'text':'   '},'words'),
])
def test_improper_cards_refused_without_contact(media,change,reason,monkeypatch):
 from lib import trailer_titles as titles
 root,*_=media;posts=[]
 monkeypatch.setattr('requests.post',lambda *a,**kw:posts.append(1))
 value=card();value.update(change)
 with pytest.raises(ValueError,match=reason):titles.validate_cards(root,[value],24,require_approval=False)
 assert posts==[]

def test_overlapping_cards_refused_but_two_intertitles_and_end_fit(media):
 from lib import trailer_titles as titles
 root,*_=media;rows=[]
 for i,word in enumerate(('First light.','“Only tonight.”','The quiet station')):
  row=card(word);row.update(id=f'card-{i}',start_frame=i*24,duration_frames=24,role='end' if i==2 else 'intertitle');rows.append(row)
 assert len(titles.validate_cards(root,rows,72,require_approval=False))==3
 rows[1]['start_frame']=23
 with pytest.raises(ValueError,match='overlap'):titles.validate_cards(root,rows,72,require_approval=False)

def test_signer_rechecks_saved_words_and_font(media):
 from lib import cutting_view as cutlib,trailer_edit,trailer_titles as titles
 from scripts import gate_approve
 root,shot,*_=media;timeline=cut(shot);timeline['cards']=[card()]
 old=trailer_edit.read_revision(root);saved=cutlib.save_cut(root,timeline,old['edit_revision_sha256'])
 gate=titles.request_approval(root,'invented-card');shown=gate_approve.construct(root,gate)
 timeline['cards'][0]['text']='Different words.'
 cutlib.save_cut(root,timeline,saved['edit_revision_sha256'])
 with pytest.raises(gate_approve.GateHandlerError,match='changed'):
  shown.pre_commit_check()

def test_unicode_normalisation_cannot_reuse_a_card_signature(media):
 from lib import cutting_view,trailer_edit,trailer_titles as titles
 root,shot,*_=media;value=cut(shot);value['cards']=[card('Café')]
 saved=cutting_view.save_cut(root,value,trailer_edit.read_revision(root)['edit_revision_sha256'])
 first=titles.approval_record(root,'invented-card')
 looks.approve_request(titles.request_approval(root,'invented-card'),root)
 value['cards'][0]['text']='Cafe\u0301'
 # The byte binding is independent of the common canonical JSON's NFC rule.
 second=titles._record(root,value['cards'][0])
 assert first['card']['text_utf8_sha256']!=second['card']['text_utf8_sha256']
 assert titles.status(root,value['cards'][0])=='Needs a fresh look'

def test_missing_and_changed_real_font_refuse(media,monkeypatch,tmp_path):
 from lib import trailer_titles as titles
 root,*_=media;value=card();font=tmp_path/titles.FONT_PATH
 font.parent.mkdir(parents=True);font.write_bytes(b'not the supplied font')
 monkeypatch.setattr(titles,'REPO_ROOT',tmp_path)
 with pytest.raises(ValueError,match='font is missing or changed'):titles.card_png(value)
 monkeypatch.setattr(titles,'REPO_ROOT',tmp_path/'nonexistent')
 with pytest.raises(ValueError,match='font is missing or changed'):titles.card_png(value)
