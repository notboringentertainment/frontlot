"""Run existing governed headshot cases under old and trailer definitions.

Fixtures sign invented stage approvals with isolated test keys, standing in
for the future human stage signing screen. No production signing route is added.
"""
import json
import pytest
from tests.lib.test_authored_film_contract import project, gates_dir
from tests.lib.test_headshot_run import _world
from tests.lib.test_headshot_batch_override import (
    TestBatchGateFires as _BatchGateFires,
    TestBatchSigningAndCasting as _BatchSigningAndCasting,
    TestStaleAuthority as _StaleAuthority,
)
from tests.lib.test_trailer_round2 import sign_stage


@pytest.fixture(params=['1.5','1.6'])
def bworld(project, monkeypatch, request):
    import tests.lib.test_headshot_run as helpers
    original = helpers.write
    if request.param == '1.6':
        root=project[1]
        from lib.trailer_contract import make_contract
        marker=json.loads((root/'project.json').read_text())
        marker['trailer']=make_contract(28);marker['production_kind']='trailer'
        (root/'project.json').write_text(json.dumps(marker))
        def write(base, stage, artifacts, *, status='completed'):
            if status=='completed':
                original(base,stage,artifacts,status='awaiting_human')
                sign_stage(root, stage, artifacts)
            return original(base,stage,artifacts,status=status)
        monkeypatch.setattr(helpers,'write',write)
    return _world(project,monkeypatch,version=request.param)


class TestInheritedBatchGate(_BatchGateFires):
    pass


class TestInheritedBatchSigning(_BatchSigningAndCasting):
    pass


class TestInheritedStaleAuthority(_StaleAuthority):
    pass

@pytest.mark.parametrize('defect,match', [
    ('missing_headshot','approved current headshot'), ('missing_ref','does not name the approved hero'),
    ('wrong_prompt','rendered_sha256'), ('old_recipe','prompt_recipe.look_hash'),
    ('rejected_portrait','does not name the approved hero'), ('wrong_hero','not the approved headshot'),
    ('tampered_look','not the active look'), ('casting_lineage','casting_inspiration'),
])
def test_existing_reference_rules_also_hold_for_trailers(bworld, defect, match):
    """Same signed look/headshot/generation fixtures and shared verifier as the older rule tests."""
    import copy
    from lib.canon_enforcement import _check_visual_bible_v12, _active_looks, _active_headshots, _is_qc_manifest
    from lib.checkpoint import CheckpointValidationError, write_checkpoint
    from lib.headshots import active_headshots
    from lib.pipeline_pin import pinned_pipeline
    from tests.lib.test_headshot_run import _run, _req, _cp
    from tests.lib.look_lock_helpers import CHAR, image, approve_request, look_refs_for
    from tests.lib.test_authored_film_v12 import TestImportedAndTaint
    from tools import prompt_builder as pb
    w=bworld;root=w['project']
    r=_run(w,candidates=2); packet=_cp(w)['artifacts']['headshot_packet']
    entry=packet['characters'][0]
    approve_request(_req(w,r['request_id']),root,selection=1)
    _run(w)
    active=active_headshots(root)[CHAR]
    hero={k:v for k,v in entry['candidates'][0].items() if k in {'asset_id','path','role','provenance'}}
    href={'entity_id':CHAR,'asset_id':active.asset_id,'approval_receipt_id':active.receipt_id}
    look=_active_looks(root)[('character',CHAR)]
    recipe=pb.build_prompt(w['c'],role='turnaround',palette=['moss'])
    def make(role, **over):
        built=pb.build_prompt(w['c'],role=role,palette=['moss'])
        rec=copy.deepcopy(built['prompt_recipe'])
        if defect=='wrong_prompt': rec['rendered_sha256']='0'*64
        if defect=='old_recipe': rec['look_hash']='0'*64
        ref=copy.deepcopy(href)
        if defect=='missing_ref': ref=None
        if defect=='rejected_portrait': ref['asset_id']=entry['candidates'][1]['asset_id']
        parents=None
        if defect=='casting_lineage':
            from lib.reference_import import import_record
            from tests.lib.look_lock_helpers import _approve
            prior=image(root,'later-tainted-'+role,role=role,look_refs=look_refs_for(w['c']))
            casting_record=import_record('casting_inspiration',prior['asset_id'])
            _approve(root,'look_lock','reference:'+prior['asset_id'],casting_record,'reference_import','ref-'+role,
                     {'origin_class':'casting_inspiration','normalized_pixel_hash':prior['asset_id']})
            parents=[prior['asset_id']]
        return image(root,'sheet-'+role,role=role,look_refs=look_refs_for(w['c']),headshot_ref=ref,
                     receipt_prompt_recipe=rec,prompt=built['prompt'],input_asset_ids=parents)
    from tests.lib.test_authored_film_contract import PALETTE, GENERATOR_DEFAULTS
    bible={'version':'1.1','project_slug':'night-window','palette':PALETTE,'generator_defaults':GENERATOR_DEFAULTS,
        'characters':[{'id':CHAR,'status':'draft','sheet_revision':1,'wardrobe_negative':'no hat',
        'look_ref':{'entity_kind':'character','entity_id':CHAR,'look_hash':look.look_hash,'receipt_id':look.receipt_id},
        'hero':hero,'sheet':{'turnaround':make('turnaround'),'expressions':make('expressions')},
        'prompt_recipe':recipe['prompt_recipe']}], 'locations':[]}
    if defect=='wrong_hero': bible['characters'][0]['hero']={k:v for k,v in entry['candidates'][1].items() if k in {'asset_id','path','role','provenance'}}
    if defect=='tampered_look': bible['characters'][0]['look_ref']['receipt_id']='forged'
    if defect=='missing_headshot':
        from tests.lib.look_lock_helpers import retire_headshot
        retire_headshot(root,w['c'],active.receipt_id)
    # The real writer selects the full inherited profile, including hero and sheet QC.
    with pytest.raises(CheckpointValidationError,match=match):
        write_checkpoint(root.parent,'p','visual_bible','in_progress',{'visual_bible':bible})
