"""Paid bytes survive failures in every affected filer, not just Kling."""
import hashlib
import io
from pathlib import Path
from unittest.mock import patch
import pytest
from lib import pathsafe
from tools.cost_tracker import load_reservations
from tests.tools.test_seedream_image import env
from tests.tools._authored_film_helpers import tiny_png_bytes,project_tracker
from tests.tools.test_seedance_v25 import env as seedance_env

def test_requested_sound_missing_keeps_the_actual_silent_clip(seedance_env,monkeypatch):
    from tools.video.seedance_video import SeedanceVideo
    from tools.video import _shared
    from tests.tools.test_seedance_v25 import _inputs
    from tests.tools.test_signed_footage import clip_bytes
    root=seedance_env;raw=clip_bytes();calls=[]
    monkeypatch.setattr(_shared,'fal_queue_submit',lambda *a,**k:calls.append('submit') or {'request_id':'invented'})
    monkeypatch.setattr(_shared,'fal_queue_wait',lambda *a,**k:{'video':{'url':'https://simulation.invalid/clip'}})
    monkeypatch.setattr(_shared,'fal_download',lambda url,dest,**kw:calls.append('download') or Path(dest).write_bytes(raw))
    result=SeedanceVideo().execute(_inputs(root,generate_audio=True))
    assert not result.success and 'no audio stream' in result.error
    assert any(p.read_bytes()==raw for p in (root/'.staging').iterdir())
    assert calls==['submit','download']
    tracker=project_tracker(root)
    assert tracker.budget_spent_usd==0 and tracker.budget_reserved_usd>0

@pytest.mark.parametrize('failure',['reencode','second-download','journal'])
def test_seedream_retains_each_paid_download(env,monkeypatch,failure):
    from tools.graphics.seedream_image import SeedreamImage
    from tools.video import _shared
    calls=[];raw=tiny_png_bytes()
    monkeypatch.setattr(_shared,'fal_queue_submit',lambda *a,**k:calls.append('submit') or {'request_id':'invented'})
    monkeypatch.setattr(_shared,'fal_queue_wait',lambda *a,**k:{'images':[{'url':'https://simulation.invalid/one'},{'url':'https://simulation.invalid/two'}]})
    def download(url,dest,**kw):
        calls.append('download')
        if failure=='second-download' and url.endswith('two'):raise OSError('download interrupted')
        Path(dest).write_bytes(raw)
    monkeypatch.setattr(_shared,'fal_download',download)
    def broken(*a,**k):raise OSError('local filing failed')
    if failure=='reencode':monkeypatch.setattr(pathsafe,'reencode_png_bytes',broken)
    if failure=='journal':monkeypatch.setattr('lib.receipts.stage_generation_wal',broken)
    result=SeedreamImage().execute({'prompt':'An invented empty hall','project_dir':str(env),'num_images':2})
    assert not result.success
    saved=[p for p in (env/'.staging').iterdir() if p.read_bytes()==raw]
    assert len(saved)==(2 if failure=='journal' else 1)
    reservation=next(iter(load_reservations(env).values()))
    assert all(p.relative_to(env).as_posix() in reservation['retained_downloads'] for p in saved)
    tracker=project_tracker(env)
    assert tracker.budget_reserved_usd>0 and tracker.budget_spent_usd==0
    assert calls.count('submit')==1

@pytest.mark.parametrize('failure',['move','corrupted-destination'])
def test_content_store_retains_source_until_verified(env,monkeypatch,failure):
    raw=b'invented paid bytes';staged=pathsafe.staging_file(env,'.bin');staged.write_bytes(raw)
    objects=env/'objects';objects.mkdir()
    if failure=='move':
        def broken(*a,**k):raise OSError('disk full')
        monkeypatch.setattr(pathsafe,'atomic_move',broken)
        monkeypatch.setattr(pathsafe,'atomic_write_bytes',broken)
    else:(objects/(hashlib.sha256(raw).hexdigest()+'.bin')).write_bytes(b'damaged')
    with pytest.raises((OSError,pathsafe.PathSafetyError)):
        pathsafe.store_content_addressed(staged,objects,'.bin')
    assert staged.read_bytes()==raw

@pytest.mark.parametrize('kind',['video','image'])
def test_reconciler_keeps_download_after_local_failure(env,monkeypatch,kind):
    from scripts import reconcile_paid_calls as rc
    from tests.tools.test_reconcile_paid_calls import _reserve
    from tools.video import _shared
    hint={'kind':kind,'output_path':str(env/'output.mp4'),'objects_dir':str(env/'objects')}
    identity=_reserve(env,request_id='invented',hint=hint)
    reservation=load_reservations(env)[identity]
    raw=b'invented paid bytes'
    monkeypatch.setattr(_shared,'fal_queue_wait',lambda *a,**k:{'video':{'url':'https://simulation.invalid/clip'},'images':[{'url':'https://simulation.invalid/image'}]})
    monkeypatch.setattr(_shared,'fal_download',lambda url,dest,**kw:Path(dest).write_bytes(raw))
    def broken(*a,**k):raise OSError('local inspection failed')
    monkeypatch.setattr(_shared,'verify_video_file',broken)
    monkeypatch.setattr(pathsafe,'store_content_addressed',broken)
    with pytest.raises(OSError):rc.recover_output(env,reservation,api_key='invented')
    assert any(p.read_bytes()==raw for p in (env/'.staging').iterdir())
    assert load_reservations(env)[identity]['retained_downloads']
    assert project_tracker(env).budget_reserved_usd>0
