"""The follow-up records must remain complete and refuse conflicting fields."""
import json
import pytest
from tests.backlot.signing_outcome_helpers import project,priced,http,voice_world,signing_folder


def test_recovery_refuses_an_extra_field_in_a_complete_paid_record(voice_world,monkeypatch):
    from tests.backlot.test_followups_second_batch import paid_without_record
    from backlot import voice_panel as voice
    from lib import audio_workspace as audio
    root,posts,gets=voice_world;record=paid_without_record(root,monkeypatch)
    voice.recover(root,record['request_id'],kind='music')
    path=audio._directory(root)/(record['request_id']+'.json')
    value=json.loads(path.read_text());value['unverified_extra']='An invented conflicting annotation.'
    raw=json.dumps(value).encode();path.write_bytes(raw)
    with pytest.raises(ValueError):voice.recover(root,record['request_id'],kind='music')
    assert path.read_bytes()==raw and len(posts)==1


def test_unrecognised_import_origin_is_an_incomplete_record_warning(voice_world):
    from tests.backlot.test_followups_browser import own_take
    from backlot import voice_panel as voice
    from lib import audio_workspace as audio
    root,posts,gets=voice_world;take=own_take(root)
    take['origin']='unrecognised';take.pop('licence_note')
    (audio._directory(root)/(take['id']+'.json')).write_text(json.dumps(take))
    state=voice.snapshot(root)
    assert not state['takes'] and state['record_warnings'] and not posts and not gets
