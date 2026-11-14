"""Closing review regressions, with invented media and isolated signatures."""
import json
from pathlib import Path
import pytest
import jsonschema
from lib import checkpoint
from lib.trailer_contract import make_contract,validate_stage_contract,approval_record,edit_digest
from lib.pipeline_pin import pinned_pipeline
from tests.lib.test_trailer_round2 import trailer,sign_stage,isolate_other_contracts
from tests.lib.test_trailer_round3 import final_material


@pytest.mark.parametrize('seconds',[3,28.1])
def test_export_must_follow_saved_edit_timing(trailer,monkeypatch,seconds):
    marker=json.loads((trailer/'project.json').read_text());marker['trailer']=make_contract(60)
    (trailer/'project.json').write_text(json.dumps(marker))
    a=final_material(trailer)
    a['render_report']['outputs'][0]['duration_seconds']=seconds
    a['final_review']['checks']['technical_probe']['duration_seconds']=seconds
    monkeypatch.setattr('lib.trailer_contract._export_duration',lambda p:seconds)
    with pytest.raises(ValueError,match='saved edit timeline'):
        approval_record(trailer,pinned_pipeline(trailer,'authored-film'),'compose',a)


@pytest.mark.parametrize('status',['revise','fail'])
def test_failed_final_review_can_wait_but_cannot_complete(trailer,monkeypatch,status):
    isolate_other_contracts(monkeypatch)
    monkeypatch.setattr('lib.trailer_contract._export_duration',lambda p:28)
    a=final_material(trailer);a['final_review']['status']=status
    sign_stage(trailer,'compose',a)
    checkpoint.write_checkpoint(trailer.parent,'p','compose','awaiting_human',a)
    with pytest.raises(checkpoint.CheckpointValidationError,match='final review must pass'):
        checkpoint.write_checkpoint(trailer.parent,'p','compose','completed',a,human_approved=True)


@pytest.mark.parametrize('stage',['script','scene_plan'])
@pytest.mark.parametrize('field',['total_duration_seconds','start_seconds','end_seconds'])
def test_script_and_shot_times_use_whole_frames(stage,field):
    c=make_contract(28)
    section={'start_seconds':0,'end_seconds':28}
    a={'trailer':c,'total_duration_seconds':28,'sections' if stage=='script' else 'scenes':[section]}
    if field=='total_duration_seconds': a[field]=28.01
    else: section[field]=0.01 if field=='start_seconds' else 28.01
    with pytest.raises(ValueError,match='whole frames'):
        validate_stage_contract(c,stage,{stage:a})


@pytest.mark.parametrize('runtime',['hyperframes','ffmpeg','unknown'])
def test_trailer_edit_uses_remotion(runtime):
    c=make_contract(28)
    with pytest.raises(ValueError,match='Remotion'):
        validate_stage_contract(c,'edit',{'edit_decisions':{'trailer':c,'cuts':[],'render_runtime':runtime}})


def contract_copies(value):
    if isinstance(value,dict):
        if 'target_duration_seconds' in value.get('properties',{}): yield value
        for v in value.values(): yield from contract_copies(v)
    elif isinstance(value,list):
        for v in value: yield from contract_copies(v)


def test_every_schema_contract_copy_refuses_under_one_second():
    files=[Path('schemas/trailer_contract.schema.json'),*Path('schemas/artifacts').glob('*.schema.json')]
    checked=0
    c=make_contract(1);c['target_duration_seconds']=0.5
    for file in files:
        for schema in contract_copies(json.loads(file.read_text())):
            checked+=1
            with pytest.raises(jsonschema.ValidationError): jsonschema.validate(c,schema)
    assert checked>=6


def test_edit_schema_also_enforces_trailer_runtime():
    from tests.lib.test_trailer_round2 import edit_for
    # Validate the conditional alone: the older shared schema retains other renderer choices.
    schema=json.loads(Path('schemas/artifacts/edit_decisions.schema.json').read_text())
    for runtime in ['ffmpeg','hyperframes']:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({'trailer':make_contract(28),'edit_revision_sha256':'a'*64,'render_runtime':runtime}, {'allOf':schema['allOf']})


def test_historical_regression_does_not_swallow_unrelated_errors(trailer,monkeypatch):
    from tests.lib.test_trailer_round3 import test_historical_trailer_signature_still_blocks_bare_flag
    def unrelated(*args,**kwargs): raise ValueError('unrelated writer problem')
    monkeypatch.setattr(checkpoint,'write_checkpoint',unrelated)
    with pytest.raises(ValueError,match='unrelated writer problem'):
        test_historical_trailer_signature_still_blocks_bare_flag(trailer,monkeypatch)


@pytest.mark.parametrize('nested',[{'speech_direction':{'pause_before_seconds':0.01}},
                                   {'enhancement_cues':[{'timestamp_seconds':0.01}]}])
def test_nested_script_timing_also_uses_frames(nested):
    c=make_contract(28)
    a={'trailer':c,'sections':[dict(start_seconds=0,end_seconds=28,**nested)]}
    with pytest.raises(ValueError,match='whole frames'):
        validate_stage_contract(c,'script',{'script':a})
