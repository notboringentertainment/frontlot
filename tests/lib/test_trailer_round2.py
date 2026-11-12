"""Invented trailers: signed completion, exact content and safe duration changes."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import checkpoint, gates, receipts
from lib.canonical_json import record_sha256
from lib.pipeline_loader import load_pipeline
from tests.lib.look_lock_helpers import pin_project


@pytest.fixture
def trailer(tmp_path):
    root = checkpoint.init_project('p', title='Night Window', pipeline_type='authored-film@1.6',
                                   pipeline_dir=tmp_path, target_duration_seconds=28)
    pin_project(root, '1.6')
    return root


def sign_stage(root, stage, artifacts):
    from lib.trailer_contract import approval_record
    from lib.pipeline_pin import pinned_pipeline
    record = approval_record(root, pinned_pipeline(root, 'authored-film'), stage, artifacts)
    digest = record_sha256(record)
    with gates.handler_context():
        token = gates.mint_gate_token('p', stage, 'trailer-stage', digest, user_response={'answer':'approved','note':None,'selection':None})
    return receipts.record_human_approval(root, 'p', stage, 'trailer-stage', record, token,
        'artifact_review', artifact={'artifact_type':'trailer_stage','artifact_version':'1.0',
                                    'artifact_digest':digest,'migration_status':'reviewed'})


def isolate_other_contracts(monkeypatch):
    # Keep the real writer, schemas, pin, signed receipt verification and disk persistence.
    monkeypatch.setattr(checkpoint, '_enforce_stage_prerequisites', lambda *a: None)
    monkeypatch.setattr(checkpoint, '_enforce_manifest_artifact_contract', lambda *a: None)
    monkeypatch.setattr(checkpoint, 'validate_checkpoint', lambda *a: None)
    monkeypatch.setattr('lib.canon_enforcement.enforce_authored_canon', lambda *a, **k: None)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), 0.0001, 28.01, [], {}, True, '28'])
def test_bad_length_before_writes(tmp_path, value):
    with pytest.raises(ValueError):
        checkpoint.init_project('p', title='Night Window', pipeline_type='authored-film@1.6',
                                pipeline_dir=tmp_path, target_duration_seconds=value)
    assert not (tmp_path/'p').exists()


@pytest.mark.parametrize('version', ['1.1','1.2','1.3','1.4','1.5','1.6','1.7','2.0'])
def test_protections_inherit_without_old_behaviour_change(version):
    from lib.canon_enforcement import (_is_look_lock_manifest, _is_qc_manifest,
                                      _is_hero_qc_manifest, _is_hero_batch_manifest)
    pin = SimpleNamespace(name='authored-film', version=version)
    actual = [_is_look_lock_manifest(pin),_is_qc_manifest(pin),_is_hero_qc_manifest(pin),_is_hero_batch_manifest(pin)]
    assert actual == [tuple(map(int,version.split('.'))) >= (1,n) for n in [2,3,4,5]]
    # Patch releases and unfamiliar future names must not silently drop protections.
    pin.version=version+'.1'
    assert [_is_look_lock_manifest(pin),_is_qc_manifest(pin),_is_hero_qc_manifest(pin),_is_hero_batch_manifest(pin)] == actual
    pin.version='future'
    assert all(f(pin) for f in [_is_look_lock_manifest,_is_qc_manifest,_is_hero_qc_manifest,_is_hero_batch_manifest])
    pin.name='another-pipeline'
    assert not any(f(pin) for f in [_is_look_lock_manifest,_is_qc_manifest,_is_hero_qc_manifest,_is_hero_batch_manifest])


@pytest.mark.parametrize('stage', [s['name'] for s in load_pipeline('authored-film@1.6')['stages']])
def test_terminal_flag_cannot_complete_any_stage(trailer, stage):
    with pytest.raises(checkpoint.CheckpointValidationError, match='signed approval'):
        checkpoint.write_checkpoint(trailer.parent, 'p', stage, 'completed', {}, human_approved=True)
    assert not (trailer/f'checkpoint_{stage}.json').exists()


def test_writer_checks_contract_not_just_helper(trailer, monkeypatch):
    isolate_other_contracts(monkeypatch)
    from lib.trailer_contract import make_contract
    artifacts={'script': {'trailer':make_contract(29)}}
    with pytest.raises(checkpoint.CheckpointValidationError, match='saved trailer contract'):
        checkpoint.write_checkpoint(trailer.parent,'p','script','awaiting_human',artifacts)


def test_valid_signed_stage_is_accepted_but_disk_edit_is_not(trailer, monkeypatch):
    isolate_other_contracts(monkeypatch)
    artifacts={'canon_packet':{'invented':True}}
    sign_stage(trailer,'canon_ingest',artifacts)
    path=checkpoint.write_checkpoint(trailer.parent,'p','canon_ingest','completed',artifacts,human_approved=True)
    assert checkpoint.read_checkpoint(trailer.parent,'p','canon_ingest')['status']=='completed'
    cp=json.loads(path.read_text()); cp['artifacts']['canon_packet']['invented']=False
    path.write_text(json.dumps(cp))
    assert checkpoint.read_checkpoint(trailer.parent,'p','canon_ingest')['status']!='completed'
    from backlot.state import load_board_state
    assert load_board_state(trailer)['stages'][0]['status']!='completed'


def test_handwritten_file_cannot_satisfy_prerequisite(trailer):
    from lib.pipeline_pin import pinned_pipeline
    pin=pinned_pipeline(trailer,'authored-film')
    (trailer/'checkpoint_canon_ingest.json').write_text(json.dumps({
        'version':'1.0','project_id':'p','pipeline_type':'authored-film','pipeline':pin.to_dict(),'predecessors':[],
        'stage':'canon_ingest','status':'completed','timestamp':'2026-01-01T00:00:00Z',
        'checkpoint_policy':'guided','human_approved':True,'human_approval_required':True,'artifacts':{}}))
    assert 'canon_ingest' not in checkpoint.get_completed_stages(trailer.parent,'p','authored-film')
    with pytest.raises(checkpoint.CheckpointValidationError):
        checkpoint.write_checkpoint(trailer.parent,'p','proposal','awaiting_human',{})


@pytest.mark.parametrize('stage', ['canon_ingest','proposal','look_lock','headshots','visual_bible','script','scene_plan','assets','edit','compose'])
def test_missing_trailer_facts_block_every_stage(trailer, stage):
    marker=json.loads((trailer/'project.json').read_text()); marker.pop('trailer')
    (trailer/'project.json').write_text(json.dumps(marker))
    with pytest.raises(checkpoint.CheckpointValidationError, match='damaged'):
        checkpoint.write_checkpoint(trailer.parent,'p',stage,'in_progress',{})
    from backlot.state import load_board_state
    state=load_board_state(trailer)
    assert state['trailer_damaged']
    assert all(s.get('availability_message') for s in state['stages'])


def test_existing_production_cannot_request_trailer_pin(tmp_path):
    from lib.pipeline_pin import prepare_migration_request, PipelinePinError
    p=checkpoint.init_project('p',title='Night Window',pipeline_type='authored-film',pipeline_dir=tmp_path)
    with pytest.raises(PipelinePinError,match='created as a trailer'):
        prepare_migration_request(p,'p','authored-film','1.6')
    assert not (p/'.gate-requests').exists()


def test_target_change_requires_explicit_review_and_invalidates_signed_stage(trailer, monkeypatch):
    isolate_other_contracts(monkeypatch)
    artifacts={'canon_packet':{'invented':True}}
    sign_stage(trailer,'canon_ingest',artifacts)
    checkpoint.write_checkpoint(trailer.parent,'p','canon_ingest','completed',artifacts,human_approved=True)
    before=(trailer/'checkpoint_canon_ingest.json').read_bytes()
    with pytest.raises(ValueError,match='fresh review'):
        checkpoint.init_project('p',title='Night Window',pipeline_type='authored-film@1.6',
                                pipeline_dir=trailer.parent,target_duration_seconds=30)
    checkpoint.init_project('p',title='Night Window',pipeline_type='authored-film@1.6',
                            pipeline_dir=trailer.parent,target_duration_seconds=30,review_target_change=True)
    assert checkpoint.read_checkpoint(trailer.parent,'p','canon_ingest')['status']!='completed'
    assert (trailer/'checkpoint_canon_ingest.json').read_bytes()==before


def edit_for(root):
    contract=json.loads((root/'project.json').read_text())['trailer']
    return {'version':'1.0','trailer':contract,'render_runtime':'remotion',
            'cuts':[{'id':'c1','source':'invented.mp4','in_seconds':0,'out_seconds':28}]}


def test_exact_edit_and_export_are_bound_to_signed_receipt(trailer, monkeypatch):
    isolate_other_contracts(monkeypatch)
    monkeypatch.setattr('lib.trailer_contract._export_duration', lambda path: 28)
    from lib.trailer_contract import edit_digest, make_contract
    edit=edit_for(trailer); edit['edit_revision_sha256']=edit_digest(edit)
    (trailer/'artifacts'/'edit_decisions.json').write_text(json.dumps(edit))
    artifacts={'edit_decisions':edit}
    sign_stage(trailer,'edit',artifacts)
    checkpoint.write_checkpoint(trailer.parent,'p','edit','completed',artifacts,human_approved=True)
    export=trailer/'renders'/'invented.mp4';export.write_bytes(b'invented local file bytes')
    import hashlib
    sha=hashlib.sha256(export.read_bytes()).hexdigest()
    common={'trailer':make_contract(28),'edit_revision_sha256':edit['edit_revision_sha256'],'export_sha256':sha}
    reports={'render_report':dict(common,outputs=[{'path':'renders/invented.mp4','duration_seconds':28}]),
             'final_review':dict(common,status='pass',output_path='renders/invented.mp4',checks={'technical_probe':{'duration_seconds':28}})}
    sign_stage(trailer,'compose',reports)
    checkpoint.write_checkpoint(trailer.parent,'p','compose','completed',reports,human_approved=True)
    export.write_bytes(b'changed bytes')
    assert checkpoint.read_checkpoint(trailer.parent,'p','compose')['status']!='completed'
    with pytest.raises(checkpoint.CheckpointValidationError):
        checkpoint.write_checkpoint(trailer.parent,'p','compose','completed',reports,human_approved=True)


@pytest.mark.parametrize('stage', ['script','edit','compose'])
def test_writer_rejects_artifact_longer_than_own_target(trailer, monkeypatch, stage):
    isolate_other_contracts(monkeypatch)
    c=json.loads((trailer/'project.json').read_text())['trailer']
    artifact={'trailer':c,'total_duration_seconds':29,'cuts':[{'in_seconds':0,'out_seconds':29}],
              'outputs':[{'duration_seconds':29}], 'checks':{'technical_probe':{'duration_seconds':29}}}
    names={'script':['script'],'edit':['edit_decisions'],'compose':['render_report','final_review']}[stage]
    with pytest.raises(checkpoint.CheckpointValidationError,match='target'):
        checkpoint.write_checkpoint(trailer.parent,'p',stage,'awaiting_human',{n:artifact for n in names})

@pytest.mark.parametrize('script', ['backlot_simulate_run', 'backlot_screenshot_stage'])
def test_developer_scripts_cannot_approve_or_overwrite_trailer(trailer, monkeypatch, script):
    import importlib, sys
    before={p:p.read_bytes() for p in trailer.rglob('*') if p.is_file()}
    # Importing the screenshot helper changes a demo path in its own environment.
    original=__import__('os').environ.copy()
    module=importlib.import_module('scripts.'+script)
    monkeypatch.setattr(module.shutil, 'rmtree', lambda *a,**kw: pytest.fail('No deletion is allowed'))
    if script=='backlot_simulate_run':
        monkeypatch.setattr(module,'PROJECTS_DIR',trailer.parent)
        monkeypatch.setattr(sys,'argv',['demo','--project','p','--fast'])
        call=module.main
    else:
        monkeypatch.setattr(module,'STAGE_DIR',trailer.parent)
        call=lambda: module.stage_project('p','Night Window','static',[],state='complete',hero='s1')
    try:
        with pytest.raises(ValueError): call()
        assert before=={p:p.read_bytes() for p in trailer.rglob('*') if p.is_file()}
    finally:
        __import__('os').environ.clear(); __import__('os').environ.update(original)


def test_caller_cannot_disguise_trailer_as_cinematic(trailer):
    with pytest.raises(checkpoint.CheckpointValidationError,match='signed approval'):
        checkpoint.write_checkpoint(trailer.parent,'p','script','completed',{},pipeline_type='cinematic',
                                    style_playbook='clean-professional',human_approved=True)


@pytest.mark.parametrize('version', ['1.1','1.2','1.3','1.4','1.5'])
def test_old_signed_version_with_trailer_facts_is_not_trailer(tmp_path, version):
    from backlot.state import load_board_state
    p=checkpoint.init_project('p',title='Night Window',pipeline_type='authored-film',pipeline_dir=tmp_path)
    pin_project(p,version)
    m=json.loads((p/'project.json').read_text())
    from lib.trailer_contract import make_contract
    m['trailer']=make_contract(28);(p/'project.json').write_text(json.dumps(m))
    state=load_board_state(p)
    assert not state.get('is_trailer') and 'trailer' not in state


def test_length_cannot_be_given_to_non_trailer(tmp_path):
    with pytest.raises(ValueError):
        checkpoint.init_project('p',title='Night Window',pipeline_type='cinematic',pipeline_dir=tmp_path,
                                target_duration_seconds=28)
    assert not (tmp_path/'p').exists()


def test_signed_stage_is_not_unavailable_and_waiting_is_explicit(trailer, monkeypatch):
    from backlot.state import load_board_state, summarize_project
    isolate_other_contracts(monkeypatch)
    artifacts={'canon_packet':{'invented':True}}
    sign_stage(trailer,'canon_ingest',artifacts)
    checkpoint.write_checkpoint(trailer.parent,'p','canon_ingest','completed',artifacts,human_approved=True)
    state=load_board_state(trailer)
    assert state['stages'][0]['status']=='completed'
    assert not state['stages'][0]['availability_message']
    assert summarize_project(trailer)['is_trailer']


def test_approval_fails_after_marker_edited_by_hand(trailer, monkeypatch):
    isolate_other_contracts(monkeypatch)
    artifacts={'canon_packet':{'invented':True}}
    sign_stage(trailer,'canon_ingest',artifacts)
    checkpoint.write_checkpoint(trailer.parent,'p','canon_ingest','completed',artifacts,human_approved=True)
    m=json.loads((trailer/'project.json').read_text());m['trailer']['target_duration_seconds']=30
    (trailer/'project.json').write_text(json.dumps(m))
    assert checkpoint.read_checkpoint(trailer.parent,'p','canon_ingest')['status']=='awaiting_human'


def test_unsigned_new_trailer_is_a_draft_not_a_ready_production(tmp_path):
    from backlot.state import load_board_state, summarize_project
    from lib.pipeline_pin import pinned_pipeline, PipelinePinError
    p=checkpoint.init_project('p',title='Night Window',pipeline_type='authored-film@1.6',
                              pipeline_dir=tmp_path,target_duration_seconds=28)
    with pytest.raises(PipelinePinError): pinned_pipeline(p,'authored-film@1.6')
    state=load_board_state(p)
    assert state['trailer_setup_pending']
    assert state['trailer']['target_duration_seconds']==28
    assert all(s.get('label') and s.get('availability_message') for s in state['stages'])
    assert state['trailer_creation']['cannot_complete']
    assert 'another name' in state['stages'][0]['availability_message']
    assert summarize_project(p)['trailer_setup_pending']
    for ref in ['authored-film','cinematic']:
        with pytest.raises(checkpoint.CheckpointValidationError):
            checkpoint.write_checkpoint(tmp_path,'p','script','in_progress',{},pipeline_type=ref,style_playbook='clean-professional')


def test_trailer_checkpoint_schema_demands_predecessors(trailer):
    from lib.pipeline_pin import pinned_pipeline
    cp={'version':'1.0','project_id':'p','pipeline_type':'authored-film','stage':'canon_ingest',
        'status':'in_progress','timestamp':'2026-01-01T00:00:00Z','artifacts':{},
        'pipeline':pinned_pipeline(trailer,'authored-film').to_dict()}
    with pytest.raises(checkpoint.CheckpointValidationError,match='predecessors'):
        checkpoint.validate_checkpoint(cp)


def test_in_progress_artifacts_cannot_carry_wrong_trailer_facts(trailer, monkeypatch):
    isolate_other_contracts(monkeypatch)
    from lib.trailer_contract import make_contract
    with pytest.raises(checkpoint.CheckpointValidationError,match='saved trailer contract'):
        checkpoint.write_checkpoint(trailer.parent,'p','script','in_progress',{'script':{'trailer':make_contract(29)}})


@pytest.mark.parametrize('field',['edit_revision_sha256','export_sha256'])
def test_final_records_cannot_disagree(trailer, monkeypatch, field):
    isolate_other_contracts(monkeypatch)
    from lib.trailer_contract import edit_digest
    edit=edit_for(trailer);edit['edit_revision_sha256']=edit_digest(edit)
    (trailer/'artifacts'/'edit_decisions.json').write_text(json.dumps(edit))
    export=trailer/'renders'/'invented.mp4';export.write_bytes(b'invented bytes')
    import hashlib
    common={'trailer':edit['trailer'],'edit_revision_sha256':edit['edit_revision_sha256'],
            'export_sha256':hashlib.sha256(export.read_bytes()).hexdigest()}
    reports={'render_report':dict(common,outputs=[{'path':'renders/invented.mp4','duration_seconds':28}]),
             'final_review':dict(common,status='pass',output_path='renders/invented.mp4')}
    reports['final_review'][field]='0'*64
    with pytest.raises(checkpoint.CheckpointValidationError,match='fingerprints'):
        checkpoint.write_checkpoint(trailer.parent,'p','compose','awaiting_human',reports)
