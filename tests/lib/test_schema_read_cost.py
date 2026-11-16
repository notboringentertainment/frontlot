"""Reuse schema compilation, never a previous verdict about saved data."""
import copy
import jsonschema
import pytest
from tests.lib.test_trimmed_trailer_contract import no_service_contact


def count_compilation(monkeypatch):
    calls=[];original=jsonschema.Draft202012Validator.check_schema
    def counted(cls,*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(jsonschema.Draft202012Validator,'check_schema',classmethod(counted))
    return calls


def test_repeated_checkpoint_reads_reuse_the_schema_but_reject_changed_data(monkeypatch):
    from lib.checkpoint import validate_checkpoint,CheckpointValidationError
    data={'version':'1.0','project_id':'invented-read-cost','pipeline_type':'cinematic',
          'stage':'research','status':'in_progress','timestamp':'2026-11-14T12:00:00Z','artifacts':{}}
    validate_checkpoint(data);calls=count_compilation(monkeypatch)
    for _ in range(4):validate_checkpoint(data)
    assert calls==[], 'Repeated saved-state reads must not revalidate the schema itself.'
    changed=copy.deepcopy(data);changed['timestamp']=None
    with pytest.raises(CheckpointValidationError,match='Checkpoint failed schema validation'):
        validate_checkpoint(changed)


def test_artifact_reads_recheck_each_data_and_each_changed_schema(monkeypatch):
    import schemas.artifacts as artifacts
    data={'version':'1.0','title':'Invented schema read','total_duration_seconds':1,
          'sections':[{'id':'invented-line','text':'A lamp waits.','start_seconds':0,'end_seconds':1}]}
    artifacts.validate_artifact('script',data);calls=count_compilation(monkeypatch)
    for _ in range(4):artifacts.validate_artifact('script',data)
    assert calls==[], 'Reusing a checked schema must not mean reusing a data verdict.'
    with pytest.raises(jsonschema.ValidationError,match='array'):
        artifacts.validate_artifact('script',{**data,'sections':'not a list'})
    schema=artifacts.load_schema('script');schema['properties']['title']['minLength']=100
    monkeypatch.setattr(artifacts,'load_schema',lambda name:schema)
    with pytest.raises(jsonschema.ValidationError,match='too short'):
        artifacts.validate_artifact('script',data)
    assert calls, 'A changed schema must be checked and compiled before use.'
