"""Rejected playback is blocked and disposable preview work stays bounded."""
import json,subprocess
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.lib.test_cutting_view import media,cut
from tests.tools.test_signed_footage import film,provider,looks,workspace

def test_over_ceiling_balanced_file_cannot_be_played_by_name(media):
    from lib import cutting_view as c,trailer_edit as e
    from lib.canonical_json import record_sha256
    from lib.pathsafe import sha256_file
    from backlot.cutting import mount
    root,shot,*_=media;saved=c.save_cut(root,cut(shot),e.read_revision(root)['edit_revision_sha256'])
    directory=root/'artifacts/cut-previews';directory.mkdir();path=directory/(saved['edit_revision_sha256']+'-'+'f'*32+'.mp4');path.write_bytes(b'invented rejected preview')
    path.with_suffix('.review.json').write_text(json.dumps({'output_sha256':sha256_file(path),'settings_sha256':record_sha256(saved['timeline']),
        'edit_revision_sha256':saved['edit_revision_sha256'],'comparison':'after','on_target':False,'true_peak_dbtp':5}))
    app=FastAPI();mount(app,lambda r:None,lambda p:root,lambda p:None)
    with TestClient(app) as client:
        response=client.get(f'/api/project/{root.name}/cut/play/{path.name}')
    assert response.status_code==400 and 'peaks above the ceiling' in response.json()['detail']

def real_media(media):
    from lib.pathsafe import sha256_file
    root,shot,video,sound=media
    subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','testsrc2=s=160x90:r=24:d=5',str(root/'assets/local-picture.mp4')],check=True)
    subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','sine=frequency=997:duration=2',str(root/'assets/local-voice.wav')],check=True)
    for take,path in [(video,'assets/local-picture.mp4'),(sound,'assets/local-voice.wav')]:take.update(path=path,sha256=sha256_file(root/path))
    return root,shot,video,sound

def test_successful_preview_prunes_only_disposable_work_and_replaced_files(media):
    from lib import cutting_view as c,trailer_edit as e,gates,receipts
    from lib.pathsafe import sha256_file
    from lib.canonical_json import record_sha256
    root,shot,video,sound=real_media(media)
    before_sources={r['path']:sha256_file(root/r['path']) for r in (video,sound)}
    saved=c.save_cut(root,cut(shot),e.read_revision(root)['edit_revision_sha256'])
    first=c.render_preview(root,saved['edit_revision_sha256'])
    # A later rejected result must not displace the last usable after view.
    failed=first.with_name(saved['edit_revision_sha256']+'-'+'f'*32+'.mp4');failed.write_bytes(first.read_bytes())
    report=json.loads(first.with_suffix('.review.json').read_text());report['true_peak_dbtp']=5
    failed.with_suffix('.review.json').write_text(json.dumps(report))
    earlier=c.render_preview(root,saved['edit_revision_sha256'],'before')
    assert first.exists() and not failed.exists()
    newest=c.render_preview(root,saved['edit_revision_sha256'])
    assert not first.exists() and earlier.exists() and newest.exists()
    assert not list(newest.parent.glob('*.wav')) and not list(newest.parent.glob('*-parts'))
    record={'export_sha256':sha256_file(earlier)};digest=record_sha256(record)
    with gates.handler_context():
        token=gates.mint_gate_token(root.name,'compose','trailer-stage',digest,user_response='approve')
        receipts.record_human_approval(root,root.name,'compose','trailer-stage',record,token,'artifact_review',
            artifact={'artifact_type':'trailer_stage','artifact_version':'1.0','artifact_digest':digest,'migration_status':'reviewed'})
    value=c.editable(saved['timeline']);value['picture'][0]['colour']['warmth']=10
    changed=c.save_cut(root,value,saved['edit_revision_sha256']);current=c.render_preview(root,changed['edit_revision_sha256'])
    assert earlier.exists() and not newest.exists() and current.exists()
    assert e.read_revision(root,saved['edit_revision_sha256'])==saved
    assert all(sha256_file(root/p)==digest for p,digest in before_sources.items())

def test_new_rejected_balanced_file_is_not_kept_as_a_movie(media,monkeypatch):
    from lib import cutting_view as c,trailer_edit as e
    root,shot,*_=real_media(media)
    saved=c.save_cut(root,cut(shot),e.read_revision(root)['edit_revision_sha256']);original=subprocess.run
    def amplify(args,*a,**kw):
        if args[0]=='ffmpeg' and '-c:v' in args and 'libx264' in args:
            args=[*args[:-1],'-af','volume=24dB',args[-1]]
        return original(args,*a,**kw)
    monkeypatch.setattr(subprocess,'run',amplify)
    with pytest.raises(ValueError,match='balanced preview still peaks above'):c.render_preview(root,saved['edit_revision_sha256'])
    assert not list((root/'artifacts/cut-previews').glob('*.mp4'))

def test_untagged_picture_notice_is_combined(media):
    from lib import cutting_view as c,trailer_edit as e
    root,shot,*_=real_media(media);v=cut(shot)
    v['picture']=[dict(v['picture'][0],start_frame=n*24,source_in_frame=0,source_out_frame=24) for n in range(3)]
    saved=c.save_cut(root,v,e.read_revision(root)['edit_revision_sha256']);path=c.render_preview(root,saved['edit_revision_sha256'])
    messages=json.loads(path.with_suffix('.review.json').read_text())['messages']
    notes=[s for s in messages if 'no recognised colour label' in s]
    assert len(notes)==1 and '1, 2 and 3' in notes[0]
