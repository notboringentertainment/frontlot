"""Use the merged app's real creation and paper-cut records, with counted audio."""
import hashlib,json
import pytest,yaml
from lib import audio_workspace as ws
from lib.canonical_json import record_sha256
from lib import trailer_edit as edit
from tests.tools.test_audio_governance import project,sign,no_real_network_contact
from tests.tools.test_audio_half_transport import priced,http
from tests.lib.test_trailer_edit import paper,signed_story
from tests.lib.look_lock_helpers import approve_request


@pytest.mark.parametrize('creation_signed',[False,True])
@pytest.mark.parametrize('kind',['speech','music'])
def test_app_created_trailer_explains_absent_signed_price_basis(project,http,monkeypatch,creation_signed,kind):
    from lib.audio_prices import quote
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    root=edit.create_trailer(project.parent,'A quiet observatory',4,25)
    if creation_signed:
        approve_request(json.loads(next((root/'.gate-requests').glob('*.json')).read_text()),root)
    with pytest.raises(ValueError,match='No audio price basis has been signed'):
        quote(root,kind,'A quiet bell.',2)
    assert http==[]


@pytest.mark.parametrize('field,value',[('text','A visitor reaches the tower.'),('start_frame',1),('duration_frames',47),('source_ref','')])
def test_take_binds_to_a_line_saved_by_the_merged_paper_cut(priced,http,monkeypatch,field,value):
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    root=edit.create_trailer(priced.parent,'A quiet observatory',4,25)
    approve_request(json.loads(next((root/'.gate-requests').glob('*.json')).read_text()),root)
    data=yaml.safe_load((priced/'project.yaml').read_text());data['budget_usd_cap']=25
    raw=yaml.safe_dump(data).encode();(root/'project.yaml').write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    signed_story(root)
    draft=paper();draft['lines']=[{'text':'A light waits in the tower.','start_frame':0,'duration_frames':48,'source_ref':'opening'}]
    saved=edit.save_revision(root,draft,None)
    assert ws.line(root,0)==saved['paper_cut']['lines'][0]
    request=ws.prepare_request(root,'speech','eleven_multilingual_v2',line_index=0,voice='invented-voice')
    assert request['binding']['source_sha256']==record_sha256(saved['paper_cut']['lines'][0])
    assert request['duration_seconds']==2 and request['prompt']=='A light waits in the tower.'
    sign(root,'audio_spend',request,request['request_id'])
    result=ElevenLabsTTS().execute({'project_dir':str(root),'audio_request_id':request['request_id']})
    assert result.success,result.error
    assert len(http)==1 and http[0][1]['json']['text']==draft['lines'][0]['text']
    take=result.data['take'];sign(root,'audio_take',ws.decision_record(root,take['id'],'accept'),take['id'])
    assert ws.require_accepted(root,take['id'])
    draft['lines'][0][field]=value
    changed=edit.save_revision(root,draft,saved['edit_revision_sha256'])
    assert changed['edit_revision_sha256']!=saved['edit_revision_sha256']
    assert ws.take_state(root,take)=='Needs a fresh look'
    with pytest.raises(ValueError,match='accepted, unchanged'):ws.require_accepted(root,take['id'])
    assert len(http)==1
