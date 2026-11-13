"""The cut consumes verified takes and saves through the existing edit writer."""
import copy
import pytest
from tests.tools.test_signed_footage import film,provider,looks,workspace

def test_cutting_boundary_exists():
    from lib.cutting_view import validate_cut
    assert callable(validate_cut)

@pytest.fixture
def media(film,monkeypatch):
    root,shot=film
    from lib import footage_requests,audio_workspace
    video={'path':'assets/clip.mp4','sha256':'a'*64,'duration_seconds':5}
    sound={'path':'assets/sound.wav','sha256':'b'*64,'duration_seconds':2,'kind':'speech'}
    import hashlib
    for take in (video,sound):
        p=root/take['path'];p.parent.mkdir(exist_ok=True);p.write_bytes(b'invented fixture')
        take['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    monkeypatch.setattr(footage_requests,'require_accepted',lambda *a:video)
    monkeypatch.setattr(audio_workspace,'require_accepted',lambda *a:sound)
    return root,shot,video,sound

def cut(shot):
    return {'fps':24,'picture':[{'shot_id':shot,'take_id':'kept-video','start_frame':0,'source_in_frame':12,'source_out_frame':36}],
      'audio':[{'take_id':'kept-sound','start_frame':0,'source_in_frame':0,'source_out_frame':12,'level_db':-6,'fade_in_frames':2,'fade_out_frames':2}]}

def test_sources_adjacent_subsecond_split_sound_and_reopen(media):
    from lib.cutting_view import validate_cut,save_cut
    from lib.trailer_edit import read_revision
    root,shot,*_=media;value=cut(shot)
    value['picture'].append(dict(value['picture'][0],start_frame=24,source_in_frame=36,source_out_frame=48))
    value['audio'].append(dict(value['audio'][0],start_frame=6,source_in_frame=12,source_out_frame=24))
    result=validate_cut(root,value);assert result['picture'][0]['duration_frames']==24
    old=read_revision(root);saved=save_cut(root,value,old['edit_revision_sha256'])
    assert saved['revision_number']==old['revision_number']+1
    assert read_revision(root)['timeline']==result
    assert read_revision(root,old['edit_revision_sha256'])==old
    assert not (root/'checkpoint_edit.json').exists()

@pytest.mark.parametrize('case,reason',[('overlap','overlap'),('range','source'),('fraction','whole frames'),('long','two minutes'),('fade','fade'),('level','level'),('missing','kept'),('declined','kept')])
def test_refusals(media,monkeypatch,case,reason):
    from lib.cutting_view import validate_cut
    root,shot,*_=media;value=cut(shot)
    if case=='overlap':value['picture'].append(dict(value['picture'][0],start_frame=1))
    if case=='range':value['picture'][0]['source_out_frame']=1000
    if case=='fraction':value['picture'][0]['start_frame']=.5
    if case=='long':value['picture'][0]['start_frame']=2880
    if case=='fade':value['audio'][0]['fade_in_frames']=99
    if case=='level':value['audio'][0]['level_db']=float('nan')
    if case in ('missing','declined'):
        def refuse(*a):raise ValueError('Only kept, unchanged takes may enter the cut.')
        monkeypatch.setattr('lib.footage_requests.require_accepted',refuse)
    with pytest.raises(ValueError,match=reason):validate_cut(root,value)

def test_interrupted_save_keeps_last_revision(media,monkeypatch):
    from lib.cutting_view import save_cut
    from lib import trailer_edit
    root,shot,*_=media;old=trailer_edit.read_revision(root)
    def fail(*a):raise OSError('interrupted')
    monkeypatch.setattr(trailer_edit.os,'replace',fail)
    with pytest.raises(OSError):save_cut(root,cut(shot),old['edit_revision_sha256'])
    assert trailer_edit.read_revision(root)==old

def test_generated_music_is_only_for_a_test_preview(media):
    from lib.cutting_view import validate_cut
    root,shot,_,sound=media;sound.update(kind='music',release_note='For the test trailer only.')
    assert validate_cut(root,cut(shot))['audio'][0]['test_only']
    with pytest.raises(ValueError,match='test trailer only'):validate_cut(root,cut(shot),release=True)
