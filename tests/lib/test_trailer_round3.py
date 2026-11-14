"""Narrow third-round regressions, using invented productions and isolated keys."""
import copy
import hashlib
import json
import pytest
from lib import checkpoint
from lib.pipeline_pin import prepare_migration_request, PipelinePinError
from lib.trailer_contract import require_stage_approval, edit_digest
from tests.lib.test_trailer_round2 import trailer, sign_stage, isolate_other_contracts, edit_for
from tests.lib.look_lock_helpers import pin_project


def test_downgrade_refused_at_request_and_signing_screen(trailer):
    from scripts.gate_approve import _construct_pipeline_migration, GateHandlerError
    with pytest.raises(PipelinePinError, match="cannot leave"):
        prepare_migration_request(trailer, 'p', 'authored-film', '1.5')
    req={'project_id':'p','entity_id':'authored-film','pipeline_version':'1.5'}
    with pytest.raises(GateHandlerError, match="cannot leave"):
        _construct_pipeline_migration(trailer, req)


def test_signing_screen_rechecks_downgrade_after_display(tmp_path):
    from scripts.gate_approve import _construct_pipeline_migration, GateHandlerError
    root=checkpoint.init_project('p',title='Night Window',pipeline_type='authored-film',pipeline_dir=tmp_path)
    old=pin_project(root,'1.5')
    built=_construct_pipeline_migration(root,{'project_id':'p','entity_id':'authored-film','pipeline_version':'1.5'})
    pin_project(root,'1.6',supersedes=old['receipt_id'])
    with pytest.raises(GateHandlerError,match="cannot leave"):
        built.pre_commit_check()


def test_historical_trailer_signature_still_blocks_bare_flag(trailer,monkeypatch):
    isolate_other_contracts(monkeypatch)
    from lib.receipts import verified_approvals
    old=verified_approvals(trailer,'pipeline_migration')[-1]
    pin_project(trailer,'1.5',supersedes=old['receipt_id'])  # Simulate a previously signed downgrade, bypassing request handling.
    with pytest.raises(checkpoint.CheckpointValidationError,match="PIPELINE PIN VIOLATION:.*signed pin"):
        checkpoint.write_checkpoint(trailer.parent,'p','canon_ingest','completed',{},human_approved=True)
    from lib.trailer_contract import signed_trailer_pin
    assert signed_trailer_pin(trailer).version == '1.6'
    from backlot.state import load_board_state
    s=load_board_state(trailer)
    assert s['is_trailer']
    assert all(x['status']!='completed' for x in s['stages'])


def test_unsigned_marker_cannot_establish_creation(tmp_path):
    root=checkpoint.init_project('p',title='Night Window',pipeline_type='authored-film',pipeline_dir=tmp_path)
    from lib.trailer_contract import make_contract
    m=json.loads((root/'project.json').read_text())
    m.update(production_kind='trailer',pipeline_type='authored-film@1.6',trailer=make_contract(28))
    (root/'project.json').write_text(json.dumps(m))
    with pytest.raises(PipelinePinError,match='signed trailer creation'):
        prepare_migration_request(root,'p','authored-film','1.6')


def test_damaged_history_has_its_own_message_and_is_not_working(trailer):
    from backlot.state import load_board_state
    path=trailer/'approvals.jsonl'
    path.write_text(path.read_text()+'{"forged":true}\n')
    s=load_board_state(trailer)
    assert "approval history can't be verified" in s['trailer_damaged']
    assert not s['live']
    assert all(x['status']!='completed' for x in s['stages'])


def test_signed_treatment_survives_writer_normalization(trailer,monkeypatch):
    isolate_other_contracts(monkeypatch)
    a={'proposal_packet':{'production_plan':{}},'decision_log':{'decisions':[]}}
    # Sign the submitted material. The writer must not change signed artifacts.
    sign_stage(trailer,'proposal',a)
    checkpoint.write_checkpoint(trailer.parent,'p','proposal','completed',a,human_approved=True)
    assert checkpoint.read_checkpoint(trailer.parent,'p','proposal')['status']=='completed'


def final_material(root):
    e=edit_for(root);e['edit_revision_sha256']=edit_digest(e)
    (root/'artifacts/edit_decisions.json').write_text(json.dumps(e))
    f=root/'renders/invented.mp4';f.write_bytes(b'invented file')
    common={'trailer':e['trailer'],'edit_revision_sha256':e['edit_revision_sha256'],
            'export_sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
    return {'render_report':dict(common,outputs=[{'path':'renders/invented.mp4','duration_seconds':28}]),
            'final_review':dict(common,status='pass',output_path='renders/invented.mp4',checks={'technical_probe':{'duration_seconds':28}})}


@pytest.mark.parametrize('measured',[3,27.9,28.1])
def test_measured_export_must_agree_in_both_directions(trailer,monkeypatch,measured):
    from lib.pipeline_pin import pinned_pipeline
    a=final_material(trailer)
    monkeypatch.setattr('lib.trailer_contract._export_duration',lambda p:measured)
    with pytest.raises(ValueError):
        from lib.trailer_contract import approval_record
        approval_record(trailer,pinned_pipeline(trailer,'authored-film'),'compose',a)


@pytest.mark.parametrize('stage',['edit','compose'])
def test_only_latest_stage_signature_counts(trailer,monkeypatch,stage):
    isolate_other_contracts(monkeypatch)
    monkeypatch.setattr('lib.trailer_contract._export_duration',lambda p:28)
    reports=final_material(trailer)
    a=reports if stage=='compose' else {'edit_decisions':json.loads((trailer/'artifacts/edit_decisions.json').read_text())}
    sign_stage(trailer,stage,a)
    old=copy.deepcopy(a)
    checkpoint.write_checkpoint(trailer.parent,'p',stage,'completed',a,human_approved=True)
    assert checkpoint.read_checkpoint(trailer.parent,'p',stage)['status']=='completed'
    a=copy.deepcopy(a);a['render_report' if stage=='compose' else 'edit_decisions']['note']='New review'
    if stage=='edit':
        a['edit_decisions']['edit_revision_sha256']=edit_digest(a['edit_decisions'])
        (trailer/'artifacts/edit_decisions.json').write_text(json.dumps(a['edit_decisions']))
    if stage=='compose':
        (trailer/'renders/invented.mp4').write_bytes(b'newly exported file')
        sha=hashlib.sha256((trailer/'renders/invented.mp4').read_bytes()).hexdigest()
        a['render_report']['export_sha256']=sha
        a['final_review']['export_sha256']=sha
    sign_stage(trailer,stage,a)
    if stage=='compose':
        (trailer/'renders/invented.mp4').write_bytes(b'invented file')
    if stage=='edit':
        (trailer/'artifacts/edit_decisions.json').write_text(json.dumps(old['edit_decisions']))
    assert checkpoint.read_checkpoint(trailer.parent,'p',stage)['status']!='completed'
    with pytest.raises(checkpoint.CheckpointValidationError,match='fresh review'):
        checkpoint.write_checkpoint(trailer.parent,'p',stage,'completed',old,human_approved=True)


def test_signing_screen_entrypoint_rejects_handwritten_downgrade(trailer,capsys):
    from scripts import gate_approve
    request={'request_id':'downgrade','project_id':'p','stage':'pipeline','scope':'pipeline:authored-film',
             'kind':'pipeline_migration','entity_id':'authored-film','pipeline_version':'1.5',
             'artifact':None,'approval_record':{},'envelope':{},'source_checkpoint_digest':None,
             'summary':'Invented downgrade request','preview_paths':[]}
    folder=trailer/'.gate-requests';folder.mkdir(exist_ok=True)
    (folder/'downgrade.json').write_text(json.dumps(request))
    before=(trailer/'approvals.jsonl').read_bytes()
    result=gate_approve.main(['--project','p','--projects-dir',str(trailer.parent),'--request','downgrade','--dry-run'])
    assert result!=0
    output=capsys.readouterr()
    assert 'cannot leave' in output.err+output.out
    assert (trailer/'approvals.jsonl').read_bytes()==before
