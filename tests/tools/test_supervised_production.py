"""Supervised production uses real disk records and budget arithmetic; no network."""
import json
from pathlib import Path

import pytest

from tests.tools._authored_film_helpers import make_verified_project, make_tracker, tiny_png_bytes
from tools.cost_tracker import reserve_paid_call, attach_request_id, reconcile_paid_call


@pytest.fixture
def shot(tmp_path, monkeypatch):
    from lib.supervised_production import prepare
    root = make_verified_project(tmp_path, monkeypatch)
    ref = root / 'ace.png'
    ref.write_bytes(tiny_png_bytes())
    from lib.pathsafe import sha256_file
    spec = {
        'shot_id': 'ace-turn', 'direction': 'Hold three seconds, then awaken.',
        'spend_allowance_usd': 2.0, 'max_video_takes': 2,
        'allowed_tools': ['kling_reference_video'],
        'look_refs': [{'entity_kind': 'character', 'entity_id': 'ace-handler', 'look_hash': 'a' * 64}],
        'reference_manifest': [{'path': 'ace.png', 'asset_id': sha256_file(ref),
                                'role': 'hero', 'visual_bible_entity_id': 'ace-handler'}],
        'source_paths': ['canon-note.md'],
    }
    (root / 'canon-note.md').write_text('Ace is turned by the established character. Guard the ending.')
    prepare(root, spec, user_note='Use these references; up to $2 and two video takes.')
    return root, spec


def call(spec):
    return {'shot_id': spec['shot_id'], 'asset_class': 'supervised_shot',
            'look_refs': spec['look_refs'], 'reference_manifest': spec['reference_manifest'],
            'reference_image_paths': ['ace.png'], 'prompt': spec['direction']}


def reserve(root, spec, cost=1.0):
    return reserve_paid_call(make_tracker(root), root, tool='kling_reference_video',
                             endpoint='test/video', normalized_inputs_hash='b' * 64,
                             reserved_usd=cost, inputs=call(spec), kind='video')


def settle(root, rid, cost=1.0):
    attach_request_id(root, rid, 'simulated-provider-job')
    reconcile_paid_call(root, rid, cost, state='completed', tracker=make_tracker(root))


def test_allowance_counts_settled_and_outstanding_and_does_not_reset(shot):
    from lib.supervised_production import prepare
    from lib.shot_allowance import ShotAllowanceError
    root, spec = shot
    settle(root, reserve(root, spec))
    reserve(root, spec)
    prepare(root, spec, user_note='Same allowance; choke harder.')
    with pytest.raises(ShotAllowanceError):
        reserve(root, spec, .01)


def test_stop_and_reference_scope_are_checked_at_paid_boundary(shot):
    from lib.supervised_production import stop
    from lib.shot_allowance import check_call, ShotAllowanceError
    root, spec = shot
    changed = call(spec)
    changed['look_refs'] = []
    with pytest.raises(ShotAllowanceError, match='reference'):
        check_call(root, changed, kind='video', estimated_usd=1)
    stop(root, spec['shot_id'], user_note='Stop spending.')
    with pytest.raises(ShotAllowanceError, match='stopped'):
        reserve(root, spec)


def test_selection_preserves_partial_take_and_canon_departure(shot, tmp_path):
    from lib.supervised_production import attach, select, propose_change, inspect_shot
    root, spec = shot
    # Duration probe is tested with actual clips in the replay; this unit fixture
    # supplies an image as an attachable still and selects its entire content.
    take = attach(root, spec['shot_id'], root / 'ace.png', user_note='External reference test.')
    select(root, spec['shot_id'], take['take_id'], user_note='Keep this reference.')
    before = (root / 'canon-note.md').read_bytes()
    proposal = propose_change(root, spec['shot_id'], 'Try a new ritual as an experiment.')
    assert proposal['status'] == 'unratified'
    assert (root / 'canon-note.md').read_bytes() == before
    report = inspect_shot(root, spec['shot_id'])
    assert report['takes'][0]['cost_usd'] is None
    assert report['takes'][0]['provider_job_id'] is None
    assert report['selections'][0]['take_id'] == take['take_id']
    assert Path(root / take['path']).read_bytes() == (root / 'ace.png').read_bytes()
    (root / 'canon-note.md').write_text('A changed source.')
    report = inspect_shot(root, spec['shot_id'])
    assert any('canon-note.md' in warning for warning in report['warnings'])
    assert len(report['takes']) == 1


def test_unknown_external_cost_prevents_further_spend(shot):
    from lib.supervised_production import attach
    from lib.shot_allowance import ShotAllowanceError
    root, spec = shot
    attach(root, spec['shot_id'], root / 'ace.png', user_note='Imported; cost unknown.')
    with pytest.raises(ShotAllowanceError, match='unknown'):
        reserve(root, spec)


def test_existing_output_cannot_be_replaced_by_direct_paid_call(shot):
    from lib.shot_allowance import check_call, ShotAllowanceError
    root, spec = shot
    inputs = {**call(spec), 'output_path': str(root / 'ace.png')}
    with pytest.raises(ShotAllowanceError, match='existing'):
        check_call(root, inputs, kind='video', estimated_usd=1)


def test_corrupt_shot_history_never_falls_back_to_project_only(shot):
    from lib.shot_allowance import check_call
    from lib.supervised_production import shot_dir
    root, spec = shot
    journal = shot_dir(root, spec['shot_id']) / 'history.jsonl'
    with journal.open('a') as out:
        out.write('incomplete record\n')
    inputs = {**call(spec), 'asset_class': 'shot_visual'}
    with pytest.raises(ValueError, match='malformed'):
        check_call(root, inputs, kind='video', estimated_usd=1)


def test_old_take_keeps_source_warning_after_brief_is_updated(shot):
    from lib.supervised_production import attach, prepare, inspect_shot
    root, spec = shot
    take = attach(root, spec['shot_id'], root / 'ace.png', user_note='Historical material.')
    (root / 'canon-note.md').write_text('A revised upstream decision.')
    prepare(root, spec, user_note='Use the newly ratified source for future work.')
    report = inspect_shot(root, spec['shot_id'])
    assert report['takes'][0]['source_warnings']
    assert report['takes'][0]['take_id'] == take['take_id']


def test_concurrent_reservations_cannot_both_spend_the_last_dollar(shot):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from lib.shot_allowance import ShotAllowanceError
    root, spec = shot
    settle(root, reserve(root, spec))
    barrier = Barrier(2)
    def attempt():
        barrier.wait(timeout=5)
        try:
            return reserve(root, spec)
        except ShotAllowanceError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1


@pytest.mark.parametrize('limit', [float('nan'), float('inf'), -1, True])
def test_invalid_allowance_cannot_authorize_spending(shot, limit):
    from lib.supervised_production import prepare
    root, spec = shot
    with pytest.raises(ValueError):
        prepare(root, {**spec, 'spend_allowance_usd': limit}, user_note='Invalid limit.')
