"""Delivery cannot steal another artifact review, present or future."""
import pytest

@pytest.mark.parametrize('stage',['canon_ingest','proposal','look_lock','headshots','visual_bible','script','scene_plan','assets'])
def test_only_delivery_stages_enter_the_delivery_constructor(tmp_path,monkeypatch,stage):
    from scripts.gate_approve import _construct_artifact_review,GateHandlerError
    import importlib.util,json
    if stage=='canon_ingest' and importlib.util.find_spec('lib.story_ingest'):
        from lib import story_ingest,trailer_edit
        from tests.lib.look_lock_helpers import approve_request
        monkeypatch.setattr('lib.paths.PROJECTS_DIR',tmp_path)
        monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
        root=trailer_edit.create_trailer(tmp_path,'An invented review dispatch',4,10)
        approve_request(json.loads(next((root/'.gate-requests').glob('*.json')).read_text()),root)
        request=story_ingest.supply(root,b'A lamp is still lit.')['gate']
        built=_construct_artifact_review(root,request)
        assert built.record==request['approval_record'] and built.entity_id is None
        assert built.artifact['artifact_type']=='trailer_stage'
        return
    request={'scope':'trailer-stage','stage':stage}
    # This is the generic artifact review's specific refusal for this incomplete
    # request. A Delivery interception instead demands its own entity_id.
    with pytest.raises(GateHandlerError,match=r'^artifact_review request needs artifact\.artifact_type$'):
        _construct_artifact_review(tmp_path,request)
