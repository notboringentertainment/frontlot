"""Signed audio decisions are a history, never a replaceable last row."""
import json
import pytest
from lib import audio_workspace as ws
from tests.tools.test_audio_governance import project,sign,no_real_network_contact
from tests.tools.test_audio_half_transport import priced,http,music_request,speech_request,wav_bytes


@pytest.fixture(params=['music','speech'])
def generated(request,priced,http):
    from tools.audio.music_gen import MusicGen
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    record=music_request(priced)[0] if request.param=='music' else speech_request(priced)
    sign(priced,'audio_spend',record,record['request_id'])
    tool=MusicGen() if request.param=='music' else ElevenLabsTTS()
    result=tool.execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
    assert result.success,result.error
    assert len(http)==1
    yield priced,result.data['take'],record
    assert len(http)==1


def test_decline_is_final_at_operation_and_signing_boundary(generated):
    from scripts.gate_approve import construct
    root,take,_=generated
    pending=ws.request_decision(root,take['id'],'accept')
    built=construct(root,pending)
    decline=ws.decision_record(root,take['id'],'decline')
    sign(root,'audio_take',decline,take['id'])
    for operation in [lambda:ws.decision_record(root,take['id'],'accept'),
                      lambda:ws.request_decision(root,take['id'],'accept'),
                      lambda:construct(root,pending),built.pre_commit_check]:
        with pytest.raises(ValueError,match='declined.*new take'):operation()
    assert ws.take_state(root,take)=='Declined'
    with pytest.raises(ValueError,match='accepted, unchanged'):ws.require_accepted(root,take['id'])


def test_decline_dominates_even_a_later_signed_accept(generated):
    root,take,_=generated
    accept=ws.decision_record(root,take['id'],'accept')
    decline=ws.decision_record(root,take['id'],'decline')
    sign(root,'audio_take',decline,take['id'])
    # Genuine signatures reproducing a history written by the former implementation.
    sign(root,'audio_take',accept,take['id'])
    assert ws.take_state(root,take)=='Declined'
    with pytest.raises(ValueError,match='accepted, unchanged'):ws.require_accepted(root,take['id'])


def test_decline_remains_declined_after_source_changes(generated):
    from lib.trailer_contract import edit_digest
    root,take,record=generated
    sign(root,'audio_take',ws.decision_record(root,take['id'],'decline'),take['id'])
    if take['kind']=='music':ws.save_music_brief(root,'A new bell cue.',3,record['binding']['brief_id'])
    else:
        path=root/'artifacts/edit_decisions.json';edit=json.loads(path.read_text())
        edit['paper_cut']['lines'][0]['text']='The doors reopen tomorrow.'
        edit['edit_revision_sha256']=edit_digest(edit);path.write_text(json.dumps(edit))
    assert ws.take_state(root,take)=='Declined'
    with pytest.raises(ValueError,match='accepted, unchanged'):ws.require_accepted(root,take['id'])


def test_acceptance_cannot_be_signed_twice_and_can_later_be_declined(generated):
    from scripts.gate_approve import construct
    root,take,_=generated
    request=ws.request_decision(root,take['id'],'accept');built=construct(root,request)
    sign(root,'audio_take',built.record,take['id'])
    assert ws.require_accepted(root,take['id'])
    for operation in [lambda:ws.request_decision(root,take['id'],'accept'),built.pre_commit_check]:
        with pytest.raises(ValueError,match='already accepted'):operation()
    sign(root,'audio_take',ws.decision_record(root,take['id'],'decline'),take['id'])
    assert ws.take_state(root,take)=='Declined'


def test_decision_filed_under_a_different_take_never_applies(priced,tmp_path,http):
    source=tmp_path/'owned.wav';source.write_bytes(wav_bytes())
    takes=[ws.import_music(priced,source,'Invented recording','Owned by me.') for _ in range(2)]
    sign(priced,'audio_take',ws.decision_record(priced,takes[0]['id'],'accept'),takes[1]['id'])
    assert ws.take_state(priced,takes[0])=='Waiting for your decision'
    assert ws.take_state(priced,takes[1])=='Needs a fresh look'
    for take in takes:
        with pytest.raises(ValueError,match='accepted, unchanged'):ws.require_accepted(priced,take['id'])
    with pytest.raises(ValueError,match='decision history cannot be verified'):
        ws.request_decision(priced,takes[1]['id'],'accept')
    assert http==[]


def test_imported_decline_survives_a_later_misfiled_signature(priced,tmp_path,http):
    source=tmp_path/'owned.wav';source.write_bytes(wav_bytes())
    first,other=[ws.import_music(priced,source,'Invented recording','Owned by me.') for _ in range(2)]
    sign(priced,'audio_take',ws.decision_record(priced,first['id'],'decline'),first['id'])
    sign(priced,'audio_take',ws.decision_record(priced,other['id'],'accept'),first['id'])
    assert ws.take_state(priced,first)=='Declined'
    with pytest.raises(ValueError,match='declined.*new take'):ws.request_decision(priced,first['id'],'accept')
    with pytest.raises(ValueError,match='accepted, unchanged'):ws.require_accepted(priced,first['id'])
    assert http==[]


def test_resolution_cannot_be_replaced_after_signing(priced,http,monkeypatch):
    import requests
    from tools.audio.music_gen import MusicGen
    from tools.cost_tracker import load_reservations
    from scripts.gate_approve import construct
    record,_=music_request(priced);sign(priced,'audio_spend',record,record['request_id'])
    def timeout(*a,**kw):http.append(('timeout',{}));raise TimeoutError('Unknown billing')
    monkeypatch.setattr(requests,'post',timeout)
    assert not MusicGen().execute({'project_dir':str(priced),'audio_request_id':record['request_id']}).success
    identity=next(iter(load_reservations(priced)))
    note='Checked the invented account.'
    first=ws.request_resolution(priced,identity,False,0,0,note)
    other=ws.request_resolution(priced,identity,True,0,.001,note)
    built=construct(priced,other)
    sign(priced,'audio_resolution',first['approval_record'],identity)
    for operation in [lambda:ws.request_resolution(priced,identity,True,0,.001,note),
                      lambda:construct(priced,other),built.pre_commit_check]:
        with pytest.raises(ValueError,match='already has a signed account outcome'):operation()
    # A conflicting historical signature cannot replace the first outcome either.
    sign(priced,'audio_resolution',other['approval_record'],identity)
    with pytest.raises(ValueError,match='conflicting signed account outcomes'):
        ws.resolve_charge(priced,identity,True,0,.001,note)
    assert load_reservations(priced)[identity]['state']=='pending_billing'
    assert len(http)==1
