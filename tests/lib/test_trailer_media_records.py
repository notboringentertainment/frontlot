import json
from lib import audio_workspace as audio,footage_requests as footage,receipts
from tests.tools.test_signed_footage import film,provider,looks,workspace
from tests.tools.test_audio_half_transport import wav_bytes


def test_signed_keeps_and_declines_update_media_facts_without_stage_approval(film,provider,monkeypatch):
    root,shot=film;calls=[]
    def no_audio_network(*args,**kwargs):calls.append(1);raise AssertionError('No outside audio contact belongs to an imported-take decision.')
    monkeypatch.setattr('requests.get',no_audio_network);monkeypatch.setattr('requests.post',no_audio_network)
    brief=audio.save_effect_brief(root,'An invented latch.',1,False,0,0)
    path=root/'assets/invented-latch.wav';path.write_bytes(wav_bytes())
    take=audio.import_music(root,path,'A local invented recording','I made this recording.',kind='effect',brief_id=brief)
    assert not (root/'artifacts/asset_manifest.json').exists()
    kept=looks.approve_request(audio.request_decision(root,take['id'],'accept'),root)
    artifact=json.loads((root/'artifacts/asset_manifest.json').read_text())
    assert len(artifact['assets'])==1 and artifact['assets'][0]['path']==take['path']
    assert artifact['metadata']['take_records'][0]['take']==take
    assert artifact['metadata']['take_records'][0]['approval_receipt_id']==kept['receipt_id']
    request=footage.prepare_request(root,shot,model=workspace().MODEL,duration_seconds=5)
    looks.approve_request(footage.request_approval(root,request['request_id']),root)
    picture=footage.execute(root,request['request_id'])
    looks.approve_request(footage.request_decision(root,shot,picture['take_id'],'accept'),root)
    artifact=json.loads((root/'artifacts/asset_manifest.json').read_text())
    assert {r['path'] for r in artifact['assets']}=={take['path'],picture['path']}
    looks.approve_request(audio.request_decision(root,take['id'],'decline'),root)
    artifact=json.loads((root/'artifacts/asset_manifest.json').read_text())
    assert [r['path'] for r in artifact['assets']]==[picture['path']]
    assert any(r['take_id']==take['id'] and r['state']=='Declined' for r in artifact['metadata']['unusable_takes'])
    cp=json.loads((root/'checkpoint_assets.json').read_text())
    assert cp['status']=='in_progress' and cp['human_approved'] is False
    assert not [r for r in receipts.verified_approvals(root,'artifact_review') if r.get('stage')=='assets']
    assert calls==[] and all(len(provider[k])==1 for k in ('posts','uploads','waits','downloads'))
