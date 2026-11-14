"""Delivery approvals are exact existing gate receipts, never bare flags."""
import copy,json
import pytest
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign

def test_export_refuses_before_render_without_rough_signature(signed_cut,monkeypatch):
    from lib import trailer_delivery as d,trailer_edit
    root,p,posts,*_=signed_cut;calls=[]
    monkeypatch.setattr(d,'render_final',lambda *a,**k:calls.append(1))
    with pytest.raises(ValueError,match='Sign the rough cut'):d.export(root,trailer_edit.read_revision(root)['edit_revision_sha256'])
    assert calls==[] and len(p['posts'])==3 and len(posts)==1

def test_rough_gate_signs_exact_saved_revision_and_no_stage_automatically(signed_cut):
    from lib import trailer_delivery as d,trailer_edit
    root,p,posts,*_=signed_cut
    gate=d.request_rough(root,trailer_edit.read_revision(root)['edit_revision_sha256'])
    before=trailer_edit.read_revision(root)
    assert before['unfinished'] is False
    looks.approve_request(gate,root)
    assert d.rough_state(root)=='Signed by you'
    assert not (root/'checkpoint_compose.json').exists()
    assert len(p['posts'])==3 and len(posts)==1

@pytest.mark.parametrize('change',['colour','sound','picture','card'])
def test_changed_material_cannot_use_rough_approval(signed_cut,change):
    from lib import trailer_delivery as d,trailer_edit,cutting_view as cut,audio_workspace as a,footage_requests as f
    from tests.lib.test_trailer_titles import card
    root,p,posts,timeline,take=signed_cut
    gate=d.request_rough(root,trailer_edit.read_revision(root)['edit_revision_sha256']);looks.approve_request(gate,root)
    saved=trailer_edit.read_revision(root)
    if change=='sound':sign(root,'audio_take',a.decision_record(root,take['id'],'decline'),take['id'])
    elif change=='picture':
        row=saved['timeline']['picture'][0];looks.approve_request(f.request_decision(root,row['shot_id'],row['take_id'],'decline'),root)
    else:
        value=cut.editable(saved['timeline'])
        if change=='colour':value['picture'][0]['colour']['warmth']=20
        else:value['cards']=[card('A new night.')]
        cut.save_cut(root,value,saved['edit_revision_sha256'])
    assert d.rough_state(root)=='Needs a fresh look'
    with pytest.raises(ValueError,match='rough cut|no longer usable|signature'):d.export(root,trailer_edit.read_revision(root)['edit_revision_sha256'])
    assert len(p['posts'])==3 and len(posts)==1

def test_signing_rechecks_changed_rough_cut(signed_cut):
    from lib import trailer_delivery as d,trailer_edit,cutting_view as cut
    from scripts import gate_approve as ga
    root,p,posts,*_=signed_cut
    gate=d.request_rough(root,trailer_edit.read_revision(root)['edit_revision_sha256'])
    req=ga.load_request(root/'.gate-requests'/f"{gate['request_id']}.json");shown=ga.construct(root,req)
    saved=trailer_edit.read_revision(root);v=cut.editable(saved['timeline']);v['mix']['speech_margin_db']=18
    cut.save_cut(root,v,saved['edit_revision_sha256'])
    with pytest.raises((ValueError,ga.GateHandlerError),match='changed|fresh|rough'):shown.pre_commit_check()
    assert len(p['posts'])==3 and len(posts)==1

def test_safe_output_level_cannot_hide_a_silent_voice_source():
    from lib import trailer_delivery as d
    measured={'on_target':True,'speech_checks':[],'effect_checks':[],'voice_checks':[],
              'source_failures':[{'kind':'inaudible_voice','number':1}],
              'source_warnings':['Sound 1 has no audible speech in its chosen trim.']}
    assert d._sound_ok(measured) is False

@pytest.mark.parametrize('status,allowed',[('clear',True),('quiet',True),('partial',True),('unmeasured',False),('unheard',False),('uncertain',True),('hard_to_hear',True),('gap-warning',True)])
def test_delivery_checks_every_voice_and_keeps_quiet_as_a_listening_warning(status,allowed):
    from lib import trailer_delivery as d
    presence={'sustained':status not in ('unheard','unmeasured')}
    measured={'on_target':True,'source_warnings':[],'source_failures':[],'effect_checks':[],
              'voice_checks':[{'number':1,'status':'unheard' if status=='gap-warning' else status,'presence':presence,'reference_presence':presence}],
              'speech_checks':[] if status=='unmeasured' else [{'status':status}]}
    assert d._sound_ok(measured) is allowed

def test_delivery_stage_controls_no_longer_claim_they_are_unavailable():
    from lib.trailer_contract import STAGE_LIMITS
    assert STAGE_LIMITS['edit']=='' and STAGE_LIMITS['compose']==''

@pytest.mark.parametrize('identity',['../other','/another/production','x'*32,'0'*64])
def test_delivery_paths_cannot_choose_another_file(signed_cut,identity):
    from lib import trailer_delivery as d
    root,provider,posts,*_=signed_cut
    with pytest.raises(ValueError,match='Choose a final file'):d.read_export(root,identity)
    assert len(provider['posts'])==3 and len(posts)==1
