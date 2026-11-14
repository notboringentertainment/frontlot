"""Examine real encoded frames, including faults between sampled boundaries."""
import subprocess
import pytest

@pytest.fixture
def picture(tmp_path,monkeypatch):
    contacts=[];monkeypatch.setattr('requests.post',lambda *a,**k:contacts.append(1))
    source=tmp_path/'moving.mp4'
    subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','testsrc2=size=160x90:rate=24:duration=3','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
    timeline={'fps':24,'picture':[{'path':source.name,'start_frame':i*24,'source_in_frame':i*24,'source_out_frame':(i+1)*24,'duration_frames':24} for i in range(3)],'cards':[]}
    yield tmp_path,source,timeline
    assert contacts==[]

def encode(source,target,kind):
    filters={'black-cut':"drawbox=color=black:t=fill:enable='between(n,20,30)'",
             'all-black':'drawbox=color=black:t=fill',
             'freeze-cut':"split[base][hold];[hold]trim=start_frame=19:end_frame=20,setpts=PTS-STARTPTS,loop=loop=-1:size=1:start=0[held];[base][held]overlay=enable='between(n,20,30)':shortest=1",
             'all-frozen':'trim=end_frame=1,loop=loop=-1:size=1:start=0,setpts=N/24/TB'}
    subprocess.run(['ffmpeg','-v','error','-n','-i',str(source),'-filter_complex',filters[kind],'-frames:v','72','-an','-c:v','libx264','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-color_range','tv',str(target)],check=True)
    return target

@pytest.mark.parametrize('kind',['black-cut','freeze-cut','all-black','all-frozen'])
def test_unplanned_black_or_freeze_blocks_with_the_actual_time(picture,kind):
    from lib.trailer_delivery_visual import review_picture
    root,source,timeline=picture;target=encode(source,root/(kind+'.mp4'),kind)
    review=review_picture(root,target,timeline,[0,23,24,47,48,71])
    assert review['status']=='revise' and review['frames_examined']==72
    assert review['issues'] and any('0:00.' in m or '0:01.' in m for m in review['issues'])
    assert any(('black' if 'black' in kind else 'frozen') in m.lower() for m in review['issues'])

def test_ordinary_moving_picture_really_is_examined(picture):
    from lib.trailer_delivery_visual import review_picture
    root,source,timeline=picture;review=review_picture(root,source,timeline,[0,23,24,47,48,71])
    assert review['status']=='pass' and review['frames_examined']==72
    assert len(review['boundary_checks'])==6 and all(r['examined'] for r in review['boundary_checks'])

def test_a_deliberate_saved_picture_hold_is_not_called_a_fault(picture):
    from lib.trailer_delivery_visual import review_picture
    root,source,timeline=picture
    held=encode(source,root/'saved-hold.mp4','freeze-cut')
    for row in timeline['picture']:row['path']=held.name
    review=review_picture(root,held,timeline,[0,23,24,47,48,71])
    assert review['status']=='pass',review['issues']

def test_a_deliberate_black_card_and_its_hold_are_not_faults(picture):
    from lib.trailer_delivery_visual import review_picture
    from lib.trailer_titles import card_png
    from tests.lib.test_trailer_titles import card
    root,source,timeline=picture
    value=card('One lamp.');value.update(start_frame=18,duration_frames=18)
    timeline['cards']=[value];png=root/'literal-card.png';png.write_bytes(card_png(value))
    target=root/'card-on-black.mp4'
    subprocess.run(['ffmpeg','-v','error','-n','-i',str(source),'-i',str(png),'-filter_complex',
        "[0:v]scale=640:360[v];color=black:s=640x360:r=24[black];[black][1:v]overlay[card];[v][card]overlay=enable='between(n,18,35)':shortest=1",'-frames:v','72','-c:v','libx264','-pix_fmt','yuv420p',str(target)],check=True)
    review=review_picture(root,target,timeline,[0,17,18,23,24,35,36,71])
    assert review['status']=='pass',review['issues']
