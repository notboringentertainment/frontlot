"""Older paper fingerprints, financial renewals and recorded review order."""
import copy,hashlib,json
import pytest,yaml
from lib import trailer_edit as edit,trailer_delivery as delivery,trailer_records as records
from lib.canonical_json import record_sha256
from tests.lib.test_cutting_signed import signed_cut as original_cut
from tests.lib.test_trimmed_trailer_delivery import trailer_cut,final_answers
from tests.lib.test_delivery_export import approve_rough
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.lib.test_trailer_edit import human_terminal

def earlier_inputs(root):
    # Frozen earlier algorithm: script was an upstream input, not a projection.
    result={'project.json':record_sha256(json.loads((root/'project.json').read_text()))}
    for stage in ('canon_ingest','proposal','script'):
        path=root/f'checkpoint_{stage}.json'
        value=json.loads(path.read_text()) if path.exists() else None
        result[path.name]='ABSENT' if value is None else record_sha256(value.get('artifacts',value) if isinstance(value,dict) else value)
    return result

@pytest.fixture
def older_cut(film,provider,monkeypatch,request):
    with monkeypatch.context() as patch:
        patch.setattr(edit,'_inputs',earlier_inputs)
        patch.setattr(records,'sync_paper',lambda root:None)
        made=original_cut.__wrapped__(film,provider,monkeypatch,request)
        approve_rough(made[0])
        assert edit.paper_state(made[0])['usable_downstream']
        assert delivery.rough_state(made[0])=='Signed by you'
    return made

@pytest.mark.parametrize('change',[None,'canon_ingest','script'])
def test_earlier_paper_and_rough_signature_stay_valid_only_while_untouched(older_cut,change):
    from lib import audio_workspace as audio
    root,calls,posts,*_=older_cut
    before={p:p.read_bytes() for p in root.glob('checkpoint_*.json')}
    before[root/'artifacts/edit_decisions.json']=(root/'artifacts/edit_decisions.json').read_bytes()
    if change:
        path=root/f'checkpoint_{change}.json';value=json.loads(path.read_text())
        value['artifacts']['invented_creative_change']='A different ending.';path.write_text(json.dumps(value))
        assert not edit.paper_state(root)['usable_downstream']
        with pytest.raises(ValueError):delivery.require_rough(root)
        with pytest.raises(ValueError):audio.prepare_request(root,'speech','eleven_multilingual_v2',line_index=0,voice='invented')
    else:
        assert edit.paper_state(root)['usable_downstream']
        assert delivery.rough_state(root)=='Signed by you'
        delivery.require_rough(root)
        value=yaml.safe_load((root/'project.yaml').read_text())
        value['audio_price_basis']['speech_credits_per_character']['expires_at']+=3600
        raw=yaml.safe_dump(value).encode();(root/'project.yaml').write_bytes(raw)
        sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
        records.after_signature(root,'config')
        delivery.require_rough(root)
        request=audio.prepare_request(root,'speech','eleven_multilingual_v2',line_index=0,voice='invented')
        assert request['kind']=='speech'
        from tools.audio.elevenlabs_tts import ElevenLabsTTS
        sign(root,'audio_spend',request,request['request_id'])
        made=ElevenLabsTTS().execute({'project_dir':str(root),'audio_request_id':request['request_id']})
        assert made.success,made.error
        exported=delivery.export(root,edit.read_revision(root)['edit_revision_sha256'])
        assert (root/exported['render_report']['outputs'][0]['path']).is_file()
        assert all(path.read_bytes()==raw for path,raw in before.items())
    assert len(calls['posts'])==3 and len(posts)==(1 if change else 2)

def test_signed_financial_renewal_preserves_creative_checkpoints(trailer_cut):
    from lib import receipts
    from lib.checkpoint import get_completed_stages
    root,calls,posts,*_=trailer_cut
    approve_rough(root);delivery.complete_stage(root,'edit')
    looks.approve_request(delivery.request_colour(root,edit.read_revision(root)['edit_revision_sha256']),root)
    delivery.complete_stage(root,'colour_mix')
    before={p:p.read_bytes() for p in root.glob('checkpoint_*.json')}
    value=yaml.safe_load((root/'project.yaml').read_text());value['audio_price_basis']['speech_credits_per_character']['expires_at']+=3600
    raw=yaml.safe_dump(value).encode();(root/'project.yaml').write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    records.after_signature(root,'config')
    assert all(p.read_bytes()==raw for p,raw in before.items())
    assert get_completed_stages(root.parent,root.name,'authored-film@1.7')==['canon_ingest','script','edit','colour_mix']
    assert delivery.rough_state(root)=='Signed by you'
    delivery.require_colour(root)
    assert len(calls['posts'])==3 and len(posts)==1

def test_colour_signing_waits_for_recorded_rough_cut(trailer_cut):
    root,calls,posts,*_=trailer_cut
    saved=approve_rough(root)
    with pytest.raises(ValueError,match='record|Record'):
        delivery.request_colour(root,saved['edit_revision_sha256'])
    from lib import trailer_contract as contract
    from lib.media_spending import gate_request
    from scripts import gate_approve
    record=contract.approval_record(root,delivery._pin(root),'colour_mix',{'edit_decisions':saved})
    bypass=gate_request(root,'artifact_review','colour_mix',record,'Invented out-of-order request.',scope='trailer-stage',stage='colour_mix')
    with pytest.raises(ValueError,match='record|Record'):gate_approve.construct(root,bypass)
    delivery.complete_stage(root,'edit')
    gate=delivery.request_colour(root,saved['edit_revision_sha256'])
    shown=gate_approve.construct(root,gate)
    path=root/'checkpoint_edit.json';raw=path.read_bytes();damaged=json.loads(raw);damaged['status']='in_progress';path.write_text(json.dumps(damaged))
    with pytest.raises(ValueError,match='record|Record'):shown.pre_commit_check()
    path.write_bytes(raw)
    looks.approve_request(gate,root)
    with pytest.raises(ValueError,match='record|Record'):
        delivery.export(root,saved['edit_revision_sha256'])
    delivery.complete_stage(root,'colour_mix');delivery.require_colour(root)
    assert len(calls['posts'])==3 and len(posts)==1

def test_finished_trailer_survives_price_account_and_ceiling_signatures(trailer_cut,monkeypatch):
    from tests.backlot.test_delivery_browser import delivery_cut
    from lib import audio_workspace as audio
    from lib.checkpoint import get_completed_stages
    from lib.project_config import load_current_verified_project_config
    root,calls,posts=delivery_cut.__wrapped__(trailer_cut)
    saved=approve_rough(root);delivery.complete_stage(root,'edit')
    looks.approve_request(delivery.request_colour(root,saved['edit_revision_sha256']),root)
    delivery.complete_stage(root,'colour_mix')
    item=delivery.export(root,saved['edit_revision_sha256'])
    looks.approve_request(delivery.request_final(root,item['id'],final_answers(delivery.story_questions(root))),root)
    delivery.complete_stage(root,'compose',item['id'])
    before={p:p.read_bytes() for p in root.glob('checkpoint_*.json')}
    for change in ('prices','account','ceiling'):
        value=yaml.safe_load((root/'project.yaml').read_text())
        if change=='prices':
            value['audio_price_basis']['speech_credits_per_character']['value']=2
            value['audio_price_basis']['speech_credits_per_character']['expires_at']+=3600
        elif change=='account':
            entry=next(row for row in value['provider_egress'] if row['provider']=='elevenlabs')
            if 'music_prompt' not in entry['content_classes']:entry['content_classes'].append('music_prompt')
        else:value['budget_usd_cap']+=1
        raw=yaml.safe_dump(value).encode();(root/'project.yaml').write_bytes(raw)
        sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
        records.after_signature(root,'config')
        assert all(p.read_bytes()==raw for p,raw in before.items())
        assert get_completed_stages(root.parent,root.name,'authored-film@1.7')==['canon_ingest','script','edit','colour_mix','compose']
        assert delivery.final_state(root,item['id'])=='Approved by you'
        request=audio.prepare_request(root,'speech','eleven_multilingual_v2',line_index=0,voice='invented')
        assert request['price_basis_sha256']==record_sha256(value['audio_price_basis'])
        assert load_current_verified_project_config(root).budget_usd_cap==value['budget_usd_cap']
    assert len(calls['posts'])==3 and len(posts)==1

def test_paper_projection_does_not_trust_an_invented_settings_receipt(tmp_path):
    from tests.lib.test_trailer_edit import trailer
    from tests.lib.test_whole_paper_review import draft
    root=trailer(tmp_path);draft(root)
    path=root/'checkpoint_script.json';value=json.loads(path.read_text())
    value['artifacts']['script']['metadata']['signed_settings']={'config_sha256':'a'*64,'approval_receipt_id':'invented-receipt'}
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='settings reference'):records.paper_artifacts(root)
