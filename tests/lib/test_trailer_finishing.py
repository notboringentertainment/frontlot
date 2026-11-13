import copy
import pytest
from tests.lib.test_cutting_view import media,cut
from tests.tools.test_signed_footage import film,provider,looks,workspace

def test_colour_and_mix_are_saved_fingerprinted_reopened_and_restored(media):
    from lib import cutting_view as c,trailer_edit as e
    root,shot,*_=media;old=e.read_revision(root);timeline=cut(shot)
    timeline['picture'][0]['colour']={'brightness':20,'warmth':-10,'contrast':5,'colour':0}
    timeline['mix']={'speech_margin_db':15}
    saved=c.save_cut(root,timeline,old['edit_revision_sha256']);again=e.read_revision(root)
    assert again['timeline']['picture'][0]['colour']==timeline['picture'][0]['colour']
    assert again['timeline']['mix']=={'speech_margin_db':15}
    changed=c.editable(again['timeline']);changed['picture'][0]['colour']['warmth']=25
    newer=c.save_cut(root,changed,saved['edit_revision_sha256'])
    assert newer['edit_revision_sha256']!=saved['edit_revision_sha256']
    restored=e.restore_revision(root,saved['edit_revision_sha256'],newer['edit_revision_sha256'])
    assert restored['timeline']==saved['timeline'] and restored['unfinished'] is True

@pytest.mark.parametrize('value',[0,5,25,True,'twelve',float('inf')])
def test_margin_must_be_a_bounded_number(media,value):
    from lib.cutting_view import validate_cut
    root,shot,*_=media;value_cut=cut(shot);value_cut['mix']={'speech_margin_db':value}
    with pytest.raises(ValueError,match='speech.*six.*twenty-four'):
        validate_cut(root,value_cut)
