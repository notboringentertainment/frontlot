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

def test_actual_preview_uses_the_saved_colour_and_reports_its_final_bytes(media):
    import subprocess,json,numpy as np
    from lib import cutting_view as c,trailer_edit as e
    from lib.pathsafe import sha256_file
    root,shot,video,sound=media
    video['path']='assets/finish-pattern.mp4';sound['path']='assets/finish-tone.wav'
    subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','testsrc2=s=160x90:r=24:d=5','-an',str(root/video['path'])],check=True)
    subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','sine=frequency=997:duration=2',str(root/sound['path'])],check=True)
    for take in (video,sound):take['sha256']=sha256_file(root/take['path'])
    value=cut(shot);value['picture'][0]['colour']={'brightness':50,'warmth':20,'contrast':0,'colour':0}
    saved=c.save_cut(root,value,e.read_revision(root)['edit_revision_sha256'])
    before=c.render_preview(root,saved['edit_revision_sha256'],comparison='before')
    after=c.render_preview(root,saved['edit_revision_sha256'])
    frame=lambda p:np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1']),dtype=np.uint8)
    assert frame(after).mean()>frame(before).mean()+5
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(after)]))
    v=next(s for s in probe['streams'] if s['codec_type']=='video')
    assert v['avg_frame_rate']=='24/1' and v['color_space']=='bt709' and v['color_transfer']=='bt709' and v['color_primaries']=='bt709'
    review=json.loads(after.with_suffix('.review.json').read_text());assert review['output_sha256']==sha256_file(after)
    assert review['edit_revision_sha256']==saved['edit_revision_sha256'] and review['comparison']=='after'

@pytest.mark.parametrize('change',['colour','mix'])
def test_signed_rough_cut_no_longer_counts_when_finishing_changes(media,monkeypatch,change):
    from lib import cutting_view as c,trailer_edit as e,trailer_contract as contract,gates,receipts
    from lib.pipeline_pin import pinned_pipeline
    from lib.canonical_json import record_sha256
    root,shot,*_=media;saved=c.save_cut(root,cut(shot),e.read_revision(root)['edit_revision_sha256'])
    monkeypatch.setattr(contract,'validate_stage_contract',lambda *a,**kw:None)
    pin=pinned_pipeline(root,'authored-film');record=contract.approval_record(root,pin,'edit',{'edit_decisions':saved})
    artifact={'artifact_type':'trailer_stage','artifact_version':'1.0','artifact_digest':record_sha256(record),'migration_status':'reviewed'}
    with gates.handler_context():
        token=gates.mint_gate_token(root.name,'edit','trailer-stage',record_sha256(record),user_response='approve')
        receipts.record_human_approval(root,root.name,'edit','trailer-stage',record,token,'artifact_review',artifact=artifact)
    contract.require_stage_approval(root,pin,'edit',{'edit_decisions':saved})
    value=c.editable(saved['timeline'])
    if change=='colour':value['picture'][0]['colour']['warmth']=10
    else:value['mix']['speech_margin_db']=18
    changed=c.save_cut(root,value,saved['edit_revision_sha256'])
    with pytest.raises(ValueError,match='signed approval'):
        contract.require_stage_approval(root,pin,'edit',{'edit_decisions':changed})
