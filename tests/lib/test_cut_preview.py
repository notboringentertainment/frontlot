import json,subprocess
from tests.lib.test_cutting_view import media,cut
from tests.tools.test_signed_footage import film,provider,looks,workspace

def test_real_preview_has_picture_sound_and_source_offset(media):
    from lib.cutting_view import render_preview,save_cut
    from lib.trailer_edit import read_revision
    from lib.pathsafe import sha256_file
    root,shot,video,sound=media
    video['path']='assets/pattern.mp4';sound['path']='assets/tone.wav'
    subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','testsrc2=s=160x90:r=24:d=5','-an',str(root/video['path'])],check=True)
    video['sha256']=sha256_file(root/video['path'])
    subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','sine=frequency=440:duration=2',str(root/sound['path'])],check=True)
    sound['sha256']=sha256_file(root/sound['path'])
    saved=save_cut(root,cut(shot),read_revision(root)['edit_revision_sha256'])
    path=render_preview(root,saved['edit_revision_sha256'])
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(path)]))
    assert {s['codec_type'] for s in data['streams']}=={'video','audio'}
    assert data['streams'][0]['avg_frame_rate']=='24/1'
    def frame(file,seconds):
        return subprocess.check_output(['ffmpeg','-v','error','-ss',str(seconds),'-i',str(file),'-vf','scale=640:360','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
    actual=frame(path,0);wanted=frame(root/video['path'],.5);wrong=frame(root/video['path'],0)
    distance=lambda a,b:sum(abs(x-y) for x,y in zip(a,b))/len(a)
    assert distance(actual,wanted)<distance(actual,wrong)/2
    import struct
    samples=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:a','-f','f32le','pipe:1'])
    values=struct.unpack('<'+'f'*(len(samples)//4),samples)
    assert sum(x*x for x in values)/len(values)>.0001
