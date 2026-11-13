"""Every generation entry refuses trailers before its provider body or IO."""
import json
from pathlib import Path
import pytest
from lib import supervised_production as sp
from lib.pathsafe import sha256_file
from tests.lib.look_lock_helpers import PROJECT, CHAR, pin_project, character_look, activate_look, look_refs_for
from tests.tools._authored_film_helpers import make_verified_project, write_receipted_png, make_tracker
from tests.tools.test_trailer_footage_notebook import shot  # noqa: F401

from tools._generation_guard import CLOSED


@pytest.fixture
def trailer(tmp_path,monkeypatch):
    from lib.trailer_contract import make_contract
    root=make_verified_project(tmp_path,monkeypatch,PROJECT)
    pin_project(root,'1.6')
    (root/'project.json').write_text(json.dumps({'title':'An invented closed trailer','production_kind':'trailer','trailer':make_contract(5)}))
    look=character_look();activate_look(root,look)
    ref=write_receipted_png(root,'assets/reference.png')
    inputs={'project_dir':str(root),'prompt':'A keeper looks towards a doorway.',
        'output_path':str(root/'assets/test.mp4'),'duration':'5',
        'look_refs':look_refs_for(look),
        'reference_image_paths':[str(ref['path'])],
        'reference_manifest':[{'path':'assets/reference.png','asset_id':ref['sha256'],
            'role':'hero','visual_bible_entity_id':CHAR}]}
    return root,inputs


@pytest.mark.parametrize('tool_name',['kling_reference_video','seedance_video'])
@pytest.mark.parametrize('shape',['unprepared','no-shot','planned'])
def test_refusal_precedes_upload_reservation_and_storyboard(trailer,monkeypatch,tool_name,shape):
    from tools.video import _shared
    from tools.cost_tracker import load_reservations
    from tools.video.kling_reference_video import KlingReferenceVideo
    from tools.video.seedance_video import SeedanceVideo
    root,inputs=trailer
    inputs=dict(inputs)
    if shape!='no-shot':inputs['shot_id']='unprepared-shot'
    if shape=='planned':inputs['asset_class']='shot_visual'
    calls=[]
    monkeypatch.setenv('FAL_KEY','invented-only')
    monkeypatch.setattr(_shared,'upload_image_fal',lambda *a,**k:calls.append('upload') or 'https://simulation.invalid/ref.png')
    monkeypatch.setattr(_shared,'fal_queue_submit',lambda *a,**k:calls.append('post') or {'request_id':'invented-job'})
    monkeypatch.setattr(_shared,'fal_queue_wait',lambda *a,**k:calls.append('wait') or {'video':{'url':'https://simulation.invalid/video.mp4'}})
    monkeypatch.setattr(_shared,'fal_download',lambda url,path,**k:calls.append('download') or Path(path).write_bytes(b'invented video'))
    monkeypatch.setattr(_shared,'verify_video_file',lambda *a,**k:{'duration_seconds':5})
    tool=KlingReferenceVideo() if tool_name=='kling_reference_video' else SeedanceVideo()
    result=tool.execute(inputs)
    assert not result.success and CLOSED in result.error
    assert calls==[]
    assert load_reservations(root)=={}


@pytest.mark.parametrize('inputs',[None,{}, {'shot_id':'unprepared-shot'}, {'asset_class':'supervised_shot'}])
def test_even_an_unattributed_video_reservation_is_closed(trailer,inputs):
    from tools.cost_tracker import reserve_paid_call,load_reservations
    from lib.shot_allowance import ShotAllowanceError
    root,_=trailer
    with pytest.raises(ShotAllowanceError,match='signed footage request'):
        reserve_paid_call(make_tracker(root),root,tool='kling_reference_video',endpoint='invented/video',
            normalized_inputs_hash='a'*64,reserved_usd=.56,inputs=inputs,kind='video')
    assert load_reservations(root)=={}


def test_unverifiable_older_shots_are_still_displayed(tmp_path,monkeypatch):
    from tests.backlot.test_shots_state import _film,_brief,_take
    from backlot.state import load_board_state
    root=_film(tmp_path)
    first=_brief(root,'shot-a');_brief(root,'shot-b')
    _take(root,first,'take-a','a'*64,at='2026-01-01T00:00:00+00:00')
    (root/'approvals.jsonl').write_text(json.dumps({'kind':'look_lock','project_id':root.name,'record':{'legacy':True}})+'\n')
    assert not sp.is_trailer(root)
    assert len(load_board_state(root)['shots'])==2


@pytest.mark.parametrize('case',['stopped','unknown-cost','otherwise-allowed'])
def test_unverifiable_older_shot_keeps_plain_stops_without_granting_spending(shot,case):
    from lib.shot_allowance import check_call,ShotAllowanceError
    from tools.cost_tracker import reserve_paid_call,load_reservations
    root,spec=shot;sid=spec['shot_id']
    inputs=sp.request(root,sid,prompt=spec['direction'],output_path='assets/guard-probe.mp4')
    with (root/'approvals.jsonl').open('a') as f:f.write('{"legacy":true}\n')
    if case=='stopped':
        sp.stop(root,sid,user_note='Stop this invented shot.')
        expected='Supervised shot is missing or stopped'
    elif case=='unknown-cost':
        sp.attach(root,sid,root/'reference.png',user_note='An invented unknown charge.')
        expected='External take cost is unknown; resolve it before spending'
    else:
        expected='Generation needs one registered production with verifiable signed settings and history. Nothing was sent.'
    for operation in [lambda:check_call(root,inputs,kind='video',estimated_usd=.56),
        lambda:reserve_paid_call(make_tracker(root),root,tool='kling_reference_video',endpoint='invented/video',
            normalized_inputs_hash='a'*64,reserved_usd=.56,inputs=inputs,kind='video')]:
        with pytest.raises(ShotAllowanceError) as error:operation()
        assert str(error.value)==expected
    assert load_reservations(root)=={}
