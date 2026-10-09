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


def test_a_changed_brief_is_refused_early_and_at_the_paid_boundary(shot):
    from lib.run_common import InputChanged
    from lib.shot_allowance import ShotAllowanceError
    from lib.supervised_production import prepare, read_brief, request
    root, spec = shot
    rev = read_brief(root, spec['shot_id'])['revision_id']
    inputs = request(root, spec['shot_id'], prompt='p', output_path='renders/t-new.mp4', expect_revision=rev)
    assert inputs['brief_revision_id'] == rev
    with pytest.raises(InputChanged, match='changed after it was approved'):
        request(root, spec['shot_id'], prompt='p', output_path='renders/t-new.mp4', expect_revision='another-revision')
    from tools.cost_tracker import load_reservations
    prepare(root, dict(spec, direction='Hold four seconds.'), user_note='Ben revised the shot.')  # revised after Go
    before = len(load_reservations(root))
    with pytest.raises(ShotAllowanceError, match='changed after it was approved'):
        reserve_paid_call(make_tracker(root), root, tool='kling_reference_video', endpoint='test/video',
                          normalized_inputs_hash='b' * 64, reserved_usd=1.0, kind='video',
                          inputs=dict(call(spec), brief_revision_id=rev))   # the paid boundary, under reservation_lock
    assert len(load_reservations(root)) == before                          # nothing reserved, nothing submitted


def _run_cli(root, tmp_path, monkeypatch, *extra):
    import sys
    from scripts import supervised_shot
    settings = tmp_path / 's.json'
    settings.write_text(json.dumps({'prompt': 'p', 'output_path': 'renders/t-cli.mp4'}))
    monkeypatch.setattr(sys, 'argv', ['supervised_shot', str(root), 'generate', 'ace-turn', str(settings),
                                      '--tool', 'kling_reference_video', *extra])
    return supervised_shot.main()


def test_cli_changed_brief_has_its_own_exit_code_and_no_traceback(shot, tmp_path, monkeypatch, capsys):
    from lib.run_common import EXIT_INPUT_CHANGED
    root, _ = shot
    rc = _run_cli(root, tmp_path, monkeypatch, '--expect-brief-revision', 'another-revision')
    err = capsys.readouterr().err
    assert rc == EXIT_INPUT_CHANGED and rc not in (1, 2)
    assert 'changed after it was approved; nothing was spent' in err and 'Traceback' not in err


def test_cli_allowance_refusal_is_plain_with_the_generic_failure_code(shot, tmp_path, monkeypatch, capsys):
    import lib.shot_allowance as sa
    root, _ = shot

    def refuse(*a, **k):
        raise sa.ShotAllowanceError('Tool exceeds the shot\'s agreed scope')
    monkeypatch.setattr(sa, 'resolve', refuse)
    rc = _run_cli(root, tmp_path, monkeypatch)
    err = capsys.readouterr().err
    assert rc == 1 and 'agreed scope' in err and 'Traceback' not in err


def test_cli_brief_changed_at_the_paid_boundary_uses_the_refusal_code(shot, tmp_path, monkeypatch, capsys):
    import lib.shot_allowance as sa
    from lib.run_common import EXIT_INPUT_CHANGED
    root, _ = shot

    def refuse(*a, **k):
        raise sa.BriefChanged('the shot brief changed after it was approved; nothing was spent')
    monkeypatch.setattr(sa, 'resolve', refuse)
    assert _run_cli(root, tmp_path, monkeypatch) == EXIT_INPUT_CHANGED


def test_request_makes_the_take_folder_so_the_paid_step_never_stops_on_it(shot):
    # First paid run 2026-10-09: the take's folder did not exist, so the provider check refused the Go.
    from lib.pathsafe import validate_output_parent
    from lib.supervised_production import request
    root, spec = shot
    inputs = request(root, spec['shot_id'], prompt='p', output_path=f"production/shots/{spec['shot_id']}/takes/take-01.mp4")
    assert Path(inputs['output_path']).parent.is_dir()
    validate_output_parent(inputs['output_path'], root)    # the check that stopped the real run now passes
