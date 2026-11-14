"""Invented trailer contracts preserve old pins and require human decisions."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from lib.checkpoint import init_project, write_checkpoint, CheckpointValidationError
from lib.pipeline_loader import load_pipeline, manifest_digest
from tests.lib.look_lock_helpers import pin_project

ROOT = Path(__file__).resolve().parents[2]
# Frozen before adding the trailer definition; these are byte contracts, not YAML equivalence.
OLD_DIGESTS = json.loads((Path(__file__).with_name('trailer_legacy_digests.json')).read_text())


def assert_old_definitions(defs):
    for name, digest in OLD_DIGESTS.items():
        assert hashlib.sha256((defs / name).read_bytes()).hexdigest() == digest


def test_old_definitions_unchanged():
    assert_old_definitions(ROOT / 'pipeline_defs')


def test_trailer_manifest_has_same_order_and_all_human_gates():
    old = load_pipeline('authored-film@1.5')
    new = load_pipeline('authored-film@1.6')
    assert [s['name'] for s in new['stages']] == [s['name'] for s in old['stages']]
    assert all(s['human_approval_default'] is True for s in new['stages'])


@pytest.mark.parametrize('seconds', [0, -1, 120.01, 121, True, '28'])
def test_invalid_target_rejected_before_workspace_written(tmp_path, seconds):
    with pytest.raises(ValueError):
        init_project('p', title='Night Window', pipeline_type='authored-film@1.6',
                     pipeline_dir=tmp_path, target_duration_seconds=seconds)
    assert not (tmp_path / 'p').exists()


def test_contract_saved_and_board_is_honest(tmp_path):
    from backlot.state import load_board_state
    p = init_project('p', title='Night Window', pipeline_type='authored-film@1.6',
                     pipeline_dir=tmp_path, target_duration_seconds=28)
    pin_project(p, '1.6')
    marker = json.loads((p / 'project.json').read_text())
    assert marker['trailer']['target_duration_seconds'] == 28
    assert marker['trailer']['approval_points'] == {'edit': 'rough_cut', 'compose': 'final_export'}
    state = load_board_state(p)
    assert state['trailer'] == marker['trailer']
    stages = {s['name']: s for s in state['stages']}
    assert stages['edit']['label'] == 'Rough cut'
    assert stages['edit']['availability_message'] == ''
    assert stages['compose']['availability_message'] == ''
    assert stages['edit']['gated'] is True
    assert stages['compose']['gated'] is True
    assert all(not s['human_approved'] for s in stages.values())


@pytest.mark.parametrize('stage', ['canon_ingest', 'proposal', 'look_lock', 'headshots',
                                    'visual_bible', 'script', 'scene_plan', 'assets', 'edit', 'compose'])
def test_no_stage_can_complete_without_human_approval(tmp_path, stage):
    p = init_project('p', title='Night Window', pipeline_type='authored-film@1.6', pipeline_dir=tmp_path, target_duration_seconds=28)
    pin_project(p, '1.6')
    with pytest.raises(CheckpointValidationError, match='GATE VIOLATION'):
        write_checkpoint(tmp_path, 'p', stage, 'completed', {})


@pytest.mark.parametrize('version', ['1.1', '1.2', '1.3', '1.4', '1.5'])
def test_older_productions_read_without_mutation(tmp_path, version):
    from backlot.state import load_board_state
    from lib.pipeline_pin import pinned_pipeline
    p = init_project('p', title='Night Window', pipeline_type='authored-film', pipeline_dir=tmp_path)
    pin_project(p, version)
    manifest = load_pipeline(f'authored-film@{version}')
    for stage in manifest['stages']:
        (p / f"checkpoint_{stage['name']}.json").write_text(json.dumps({
            'version': '1.0', 'stage': stage['name'], 'status': 'completed',
            'human_approved': False, 'artifacts': {}}))
    before = {f: f.read_bytes() for f in p.rglob('*') if f.is_file()}
    state = load_board_state(p)
    assert pinned_pipeline(p, 'authored-film').version == version
    assert all(s['status'] == 'completed' for s in state['stages'])
    assert 'trailer' not in state or state['trailer'] is None
    assert before == {f: f.read_bytes() for f in p.rglob('*') if f.is_file()}


def test_new_trailer_requires_target(tmp_path):
    with pytest.raises(ValueError, match='two minutes'):
        init_project('p', title='Night Window', pipeline_type='authored-film@1.6', pipeline_dir=tmp_path)


def test_trailer_contract_cannot_disappear_between_stages(tmp_path):
    from lib.trailer_contract import validate_stage_contract
    with pytest.raises(ValueError, match='saved trailer contract'):
        validate_stage_contract(__import__('lib.trailer_contract', fromlist=['make_contract']).make_contract(28), 'script', {'script':{}})
