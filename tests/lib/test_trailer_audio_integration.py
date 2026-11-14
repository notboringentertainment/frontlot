import json
import pytest
from lib.trailer_edit import create_trailer
from lib.project_config import read_project_config,load_current_verified_project_config,ProjectConfigError,VerifiedProjectConfig
from tests.lib.look_lock_helpers import approve_request


def test_new_trailer_has_valid_but_unapproved_nonconsenting_settings(tmp_path,monkeypatch):
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    root=create_trailer(tmp_path,'An invented combined trailer',28,50)
    data,digest=read_project_config(root)
    assert data['version']=='1.3' and data['budget_usd_cap']==50
    assert data['provider_egress']==[] and 'qc' not in data
    with pytest.raises(ProjectConfigError):load_current_verified_project_config(root)
    approve_request(json.loads(next((root/'.gate-requests').glob('*.json')).read_text()),root)
    with pytest.raises(ProjectConfigError):load_current_verified_project_config(root)
    from tools.audio._governed import prepare
    monkeypatch.setattr('lib.paths.PROJECTS_DIR', tmp_path)
    with pytest.raises(ProjectConfigError):
        prepare(root, kind='speech', model='invented', voice='invented',
                prompt='The gallery opens tonight.', duration_seconds=3,
                max_cash_usd=.4, max_credits_usd=.6, price_expires_at=4102444800)
    config=VerifiedProjectConfig(root,digest,data)
    for provider,kind in [('elevenlabs','speech_text'),('elevenlabs','music_prompt'),('fal','prompts')]:
        with pytest.raises(ProjectConfigError):config.require_egress(provider,kind)
    assert not list(root.glob('checkpoint*')) and not list(root.glob('generation*'))


def test_real_provider_settings_still_require_judging_settings():
    from schemas.artifacts import validate_project_config
    from jsonschema import ValidationError
    data={'version':'1.3','budget_usd_cap':50,'wall_time_minutes':60,'cast_cap':{'characters':1,'locations':1},'default_video_endpoint':'not-selected','provider_egress':[{'provider':'elevenlabs','content_classes':['speech_text']}]}
    with pytest.raises(ValidationError, match='qc'):validate_project_config(data)
