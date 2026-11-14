"""The nonspending first half of the footage workspace, on invented material."""
import copy
import pytest
from lib import supervised_production as sp
from lib.trailer_edit import save_revision
from tests.lib.test_trailer_edit import trailer, paper
from tests.tools._authored_film_helpers import make_verified_project, tiny_png_bytes


@pytest.fixture
def shot(tmp_path, monkeypatch):
    from lib.pathsafe import sha256_file
    root = make_verified_project(tmp_path, monkeypatch, 'invented-gallery')
    ref = root / 'reference.png'
    ref.write_bytes(tiny_png_bytes())
    (root / 'canon-note.md').write_text('An invented gallery keeper opens a door.')
    spec = {'shot_id':'gallery-turn', 'direction':'Look towards the doorway.',
            'spend_allowance_usd':2, 'max_video_takes':2,
            'allowed_tools':['kling_reference_video'],
            'look_refs':[{'entity_kind':'character','entity_id':'keeper-one','look_hash':'a'*64}],
            'reference_manifest':[{'path':'reference.png','asset_id':sha256_file(ref),
                'role':'hero','visual_bible_entity_id':'keeper-one'}],
            'source_paths':['canon-note.md']}
    sp.prepare(root, spec, user_note='An invented older shot.')
    return root, spec


@pytest.fixture
def linked(tmp_path, monkeypatch, shot):
    monkeypatch.setattr('scripts.gate_approve.require_tty', lambda: None)
    root = trailer(tmp_path / 'trailers')
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',root.parent)
    monkeypatch.setattr('lib.events.PROJECTS_DIR',root.parent)
    value = paper()
    value['shots'][0]['references'] = ['reference.png']
    revision = save_revision(root, value, None)
    source, spec = shot
    (root / 'reference.png').write_bytes((source / 'reference.png').read_bytes())
    (root / 'canon-note.md').write_text('An invented story source.')
    spec = copy.deepcopy(spec)
    spec['shot_id'] = revision['paper_cut']['shots'][0]['id']
    spec['paper_shot_id'] = spec['shot_id']
    spec['direction'] = value['shots'][0]['purpose']
    return root, spec, revision


def test_trailer_brief_requires_exact_paper_shot(linked):
    root, spec, _ = linked
    bad = dict(spec, direction='A different description.')
    with pytest.raises(ValueError, match='paper cut'):
        sp.prepare(root, bad, user_note='Prepare an invented shot.')
    assert sp.history(root, spec['shot_id']) == []


def test_binding_is_saved_without_approving_or_spending(linked):
    root, spec, revision = linked
    event = sp.prepare(root, spec, user_note='Prepare an invented shot.')
    assert event['brief']['paper_shot'] == revision['paper_cut']['shots'][0]
    assert not sp.inspect_shot(root, spec['shot_id'])['paper_binding']['needs_review']
    assert not (root / 'generation_receipts.jsonl').exists()
    assert not (root / 'checkpoint_assets.json').exists()
    assert all(p['kind'] == 'placeholder' for p in revision['timeline']['picture'])


@pytest.mark.parametrize('field,value', [('purpose','A changed action.'), ('references',[]),
                                        ('duration_frames',72), ('difficulty','Demanding')])
def test_upstream_change_requires_review_and_refuses_request(linked, field, value):
    root, spec, revision = linked
    sp.prepare(root, spec, user_note='Prepare an invented shot.')
    revised = copy.deepcopy(revision['paper_cut'])
    revised['shots'][0][field] = value
    save_revision(root, revised, revision['edit_revision_sha256'])
    assert sp.inspect_shot(root, spec['shot_id'])['paper_binding']['needs_review']
    with pytest.raises(ValueError, match='paper cut'):
        sp.request(root, spec['shot_id'], prompt=spec['direction'], output_path='assets/test.mp4')


def test_no_trailer_paid_call_before_signed_footage_lifecycle(linked):
    from lib.shot_allowance import check_call, ShotAllowanceError
    root, spec, _ = linked
    sp.prepare(root, spec, user_note='Prepare an invented shot.')
    with pytest.raises(ShotAllowanceError, match='signed footage request'):
        check_call(root, {'shot_id':spec['shot_id'], 'asset_class':'supervised_shot'},
                   kind='video', estimated_usd=.56)


def test_real_reservation_boundary_refuses_before_recording_money(linked):
    from tools.cost_tracker import reserve_paid_call, load_reservations
    from tests.tools._authored_film_helpers import make_tracker
    from lib.shot_allowance import ShotAllowanceError
    root, spec, _ = linked
    sp.prepare(root, spec, user_note='Prepare an invented shot.')
    # Reopen and repeat: neither a restart nor a double press opens spending.
    for _ in range(2):
        with pytest.raises(ShotAllowanceError, match='signed footage request'):
            reserve_paid_call(make_tracker(root), root, tool='kling_reference_video',
                endpoint='invented/video', normalized_inputs_hash='a'*64,
                reserved_usd=.56, inputs={'asset_class':'supervised_shot',
                'shot_id':spec['shot_id']}, kind='video')
    assert not load_reservations(root)


def test_unsigned_decline_and_later_choice_are_both_ignored(linked):
    root, spec, _ = linked
    sp.prepare(root, spec, user_note='Prepare an invented shot.')
    take = sp.attach(root, spec['shot_id'], root/'reference.png', user_note='An external reference.')
    with pytest.raises(ValueError,match='signature'):
        sp.decline(root, spec['shot_id'], take['take_id'], user_note='Decline this reference.')
    sp._append(root,spec['shot_id'],{'kind':'decline','take_id':take['take_id']})
    with pytest.raises(ValueError, match='signature'):
        sp.select(root, spec['shot_id'], take['take_id'], user_note='Attempt to choose it.')
    sp._append(root, spec['shot_id'], {'kind':'selection','take_id':take['take_id']})
    assert sp.inspect_shot(root, spec['shot_id'])['takes'][0]['standing'] == 'waiting'


def test_invalid_choice_of_declined_take_does_not_settle_other_takes():
    rows = [{'kind':'take','take_id':'a'}, {'kind':'take','take_id':'b'},
            {'kind':'decline','take_id':'a'}, {'kind':'selection','take_id':'a'}]
    assert sp.take_decisions(rows, final_declines=True) == {'a':'waiting', 'b':'waiting'}


def test_older_notebook_still_allows_its_existing_selection_rule(shot):
    root, spec = shot
    take = sp.attach(root, spec['shot_id'], root/'reference.png', user_note='An older reference.')
    sp.decline(root, spec['shot_id'], take['take_id'], user_note='Set aside.')
    sp.select(root, spec['shot_id'], take['take_id'], user_note='Existing older behaviour.')
    assert sp.inspect_shot(root, spec['shot_id'])['takes'][0]['standing'] == 'chosen'
