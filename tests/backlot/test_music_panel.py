"""Music controls exercise the existing signed boundary, with counted services."""
import hashlib,json,time
from pathlib import Path
import pytest,yaml
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project,sign
from tests.tools.test_audio_half_transport import priced,http,wav_bytes

def brief():return {'text':'A quiet instrumental pulse with room for speech.','seconds':12,'brief_id':None}

def prepare(root):
    from backlot import music_panel as music
    saved=music.save_brief(root,brief())
    return music.prepare_music(root,{'brief_id':saved['brief_id']})['request']

def test_music_control_reuses_one_signed_call_and_final_take_decisions(voice_world):
    from backlot import music_panel as music,voice_panel as voice
    from lib import audio_workspace as audio
    root,posts,gets=voice_world
    record=prepare(root)
    assert record['kind']=='music' and record['prompt']==brief()['text']
    assert record['duration_seconds']==12 and record['max_credits_usd']==.048
    assert not music.send(root,record['request_id'])['success'] and posts==[]
    sign(root,'audio_spend',record,record['request_id'])
    result=music.send(root,record['request_id']);assert result['success'],result
    take=result['take'];assert take['release_note']==audio.TEST_ONLY
    assert len(posts)==1 and posts[0][1]['json']=={'prompt':brief()['text'],'model_id':'music_v1','music_length_ms':12000,'force_instrumental':True}
    assert music.send(root,record['request_id'])['success'] and len(posts)==1
    assert voice.playback(root,take['id'])[0]==wav_bytes()
    accepted=music.decide(root,take['id'],'accept');sign(root,'audio_take',accepted['approval_record'],take['id'])
    assert audio.require_accepted(root,take['id'])['id']==take['id']
    music.save_brief(root,dict(brief(),text='A new instrumental ending.',brief_id=record['binding']['brief_id']))
    assert voice.snapshot(root)['takes'][0]['status']=='Needs a fresh look'
    declined=music.decide(root,take['id'],'decline');sign(root,'audio_take',declined['approval_record'],take['id'])
    with pytest.raises(ValueError,match='declined'):music.decide(root,take['id'],'accept')
    assert voice.snapshot(root)['takes'][0]['status']=='Declined' and len(posts)==1 and gets==[]

@pytest.mark.parametrize('case,message',[('empty','exact music brief'),('short','three seconds'),('fraction','whole frames'),('extra','music controls'),('unchecked','checked'),('no-basis','No audio price basis'),('expired','expired')])
def test_music_preparation_refuses_before_service_contact(voice_world,case,message):
    from backlot import music_panel as music
    root,posts,gets=voice_world;data=brief()
    if case=='empty':data['text']=' '
    if case=='short':data['seconds']=1
    if case=='fraction':data['seconds']=3.01
    if case=='extra':data['provider']='another'
    if case=='unchecked':
        path=root/'checkpoint_canon_ingest.json';value=json.loads(path.read_text());value['status']='in_progress';path.write_text(json.dumps(value))
    if case in ('no-basis','expired'):
        settings=yaml.safe_load((root/'project.yaml').read_text())
        if case=='no-basis':settings.pop('audio_price_basis')
        else:settings['audio_price_basis']['music_credits_per_minute']['expires_at']=time.time()-1
        raw=yaml.safe_dump(settings).encode();(root/'project.yaml').write_bytes(raw)
        sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    with pytest.raises(ValueError,match=message):
        saved=music.save_brief(root,data);music.prepare_music(root,{'brief_id':saved['brief_id']})
    assert posts==[] and gets==[]

def test_changed_music_and_wrong_kind_cannot_use_voice_or_music_approval(voice_world):
    from backlot import music_panel as music,voice_panel as voice
    root,posts,gets=voice_world;record=prepare(root);sign(root,'audio_spend',record,record['request_id'])
    assert not voice.send(root,record['request_id'])['success'] and posts==[]
    music.save_brief(root,dict(brief(),seconds=13,brief_id=record['binding']['brief_id']))
    assert not music.send(root,record['request_id'])['success'] and posts==[]
    assert voice.snapshot(root)['requests'][0]['error'] and gets==[]

def test_music_chooser_and_import_need_licence_and_signed_decision(voice_world,tmp_path,monkeypatch):
    from backlot import music_panel as music,voice_panel as voice
    from lib import audio_workspace as audio
    root,posts,gets=voice_world;source=tmp_path/'own-score.wav';source.write_bytes(wav_bytes())
    monkeypatch.setattr(music,'choose_file',lambda: str(source))
    chosen=music.choose(root)
    assert chosen['name']=='own-score.wav' and str(source) not in json.dumps(chosen)
    data={'choice_id':chosen['choice_id'],'source':'My own recording','licence_note':''}
    with pytest.raises(ValueError,match='licence note'):music.import_selected(root,data)
    take=music.import_selected(root,dict(data,licence_note='I own the recording and composition.'))['take']
    assert take['origin']=='imported' and take['source']=='My own recording'
    assert voice.snapshot(root)['takes'][0]['status']=='Waiting for your decision'
    gate=music.decide(root,take['id'],'accept');sign(root,'audio_take',gate['approval_record'],take['id'])
    assert audio.require_accepted(root,take['id'])['licence_note']=='I own the recording and composition.'
    assert voice.playback(root,take['id'])[0]==wav_bytes()
    with pytest.raises(ValueError,match='Choose the music file again'):music.import_selected(root,dict(data,licence_note='Owned.'))
    assert posts==[] and gets==[]

@pytest.mark.parametrize('case',['link','parent-link','traversal','missing','other-project','expired','generated'])
def test_import_refuses_unsafe_or_unrelated_selection_without_contact(voice_world,tmp_path,monkeypatch,case):
    from backlot import music_panel as music
    root,posts,gets=voice_world;source=tmp_path/'owned.wav';source.write_bytes(wav_bytes())
    if case=='generated':
        record=prepare(root);sign(root,'audio_spend',record,record['request_id']);assert music.send(root,record['request_id'])['success'];assert len(posts)==1
    if case=='link':
        linked=tmp_path/'linked.wav';linked.symlink_to(source);source=linked
    if case=='parent-link':
        linked=tmp_path/'linked-folder';linked.symlink_to(tmp_path,target_is_directory=True);source=linked/'owned.wav'
    if case=='traversal':source=tmp_path/'invented'/'..'/'owned.wav'
    monkeypatch.setattr(music,'choose_file',lambda:str(source))
    if case in ('link','parent-link','traversal'):
        with pytest.raises(ValueError,match='link|path tricks'):music.choose(root)
    else:
        selection=music.choose(root);data={'choice_id':selection['choice_id'],'source':'My recording','licence_note':'Owned by me.'}
        if case=='missing':data['choice_id']='unselected'
        if case=='other-project':music._choices[data['choice_id']]['root']=str(tmp_path/'elsewhere')
        if case=='expired':music._choices[data['choice_id']]['expires_at']=0
        with pytest.raises(ValueError,match='test-only' if case=='generated' else 'Choose the music file again'):music.import_selected(root,data)
    assert len(posts)==(1 if case=='generated' else 0) and gets==[]

def test_chooser_cancellation_and_native_call_are_safe(voice_world,monkeypatch):
    from backlot import music_panel as music
    import subprocess
    root,posts,gets=voice_world;commands=[]
    monkeypatch.setattr(music.sys,'platform','darwin')
    class Result:returncode=1;stderr='User canceled. (-128)';stdout=''
    def run(args,**kw):commands.append((args,kw));return Result()
    monkeypatch.setattr(subprocess,'run',run)
    assert music.choose(root)=={'cancelled':True}
    assert commands[0][0][:2]==['/usr/bin/osascript','-e'] and not commands[0][1].get('shell')
    assert posts==[] and gets==[]

def test_combined_spending_snapshot_counts_voice_and_music_once(voice_world,monkeypatch):
    from backlot import voice_panel as voice,music_panel as music
    root,posts,gets=voice_world;voice.catalogue(root)
    # Two genuinely different recordings: immutable content provenance refuses
    # relabelling the same bytes as a different model's output.
    import requests
    original=requests.post
    def post(url,**kw):
        result=original(url,**kw)
        if url.endswith('/music'):
            data=bytearray(wav_bytes());data[-1]=1;result.content=bytes(data)
        return result
    monkeypatch.setattr(requests,'post',post)
    speech=voice.prepare_voice(root,{'line_index':0,'voice':'invented_voice','notes':'','rights_confirmed':True})['request']
    score=prepare(root)
    for record,send in ((speech,voice.send),(score,music.send)):
        sign(root,'audio_spend',record,record['request_id']);result=send(root,record['request_id']);assert result['success'],result
    snap=voice.snapshot(root)
    assert {r['record']['kind'] for r in snap['requests']}=={'speech','music'}
    assert {t['kind'] for t in snap['takes']}=={'speech','music'}
    assert snap['budget']['spent']==.0004 and snap['budget']['audio_credits']==.0004 and snap['budget']['remaining']==.9996
    assert len(posts)==2 and len(gets)==1

def test_music_routes_check_board_writer_before_opening_dialog_or_writing(voice_world,monkeypatch):
    from fastapi.testclient import TestClient
    from backlot import music_panel as music
    import backlot.server as server
    root,posts,gets=voice_world;dialogs=[]
    monkeypatch.setattr(music,'choose_file',lambda:dialogs.append(1))
    monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    with TestClient(server.create_app(port=4913,capability_token='invented-browser-proof')) as client:
        for route in ('import/choose','import','brief','prepare','invented/send','invented/decision'):
            url=f'/api/project/{root.name}/music/{route}'
            assert client.post(url,json={}).status_code==403
            assert client.post(url,json={},headers={'origin':'http://127.0.0.1:4913'}).status_code==401
    assert dialogs==[] and posts==[] and gets==[]

def test_price_review_derives_dollars_without_changing_the_signed_content(voice_world):
    from backlot import voice_panel as voice
    from backlot.state import _render_config
    from lib.canonical_json import record_sha256
    root,posts,gets=voice_world
    request=voice.prepare_prices(root,dict(dollars_per_credit=.0002,speech_credits_per_character=1,music_credits_per_minute=1200,voice_multiplier_ceiling=2,expires_at=4102444800))
    raw=(root/'project.yaml').read_bytes();packet=_render_config(root,request)
    summary=packet['audio_price_summary']
    assert summary['longest_line_dollars']==.0088 and summary['music_minute_dollars']==.24
    assert summary['expires_at']==4102444800 and summary['longest_line']=='The gallery is closed.'
    assert packet['record']==request['approval_record'] and request['record_sha256']==record_sha256(packet['record'])
    assert (root/'project.yaml').read_bytes()==raw and posts==[] and gets==[]

def test_music_permission_must_be_signed_before_preparing_a_card(voice_world):
    from backlot import music_panel as music
    root,posts,gets=voice_world
    settings=yaml.safe_load((root/'project.yaml').read_text());settings['provider_egress']=[{'provider':'elevenlabs','content_classes':['speech_text']}]
    raw=yaml.safe_dump(settings).encode();(root/'project.yaml').write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    with pytest.raises(ValueError,match='Sign.*music briefs'):prepare(root)
    assert posts==[] and gets==[]

def test_import_rejects_a_malformed_selection_with_a_plain_refusal(voice_world):
    from backlot import music_panel as music
    root,posts,gets=voice_world
    with pytest.raises(ValueError,match='Choose the music file again'):
        music.import_selected(root,{'choice_id':[],'source':'Owned','licence_note':'Owned'})
    assert posts==[] and gets==[]

def test_unknown_music_charge_blocks_voice_and_music_but_never_submits_again(voice_world,monkeypatch):
    from backlot import voice_panel as voice,music_panel as music
    root,posts,gets=voice_world;voice.catalogue(root)
    speech=voice.prepare_voice(root,{'line_index':0,'voice':'invented_voice','notes':'','rights_confirmed':True})['request'];score=prepare(root)
    for record in (speech,score):sign(root,'audio_spend',record,record['request_id'])
    def timeout(*a,**kw):posts.append(1);raise TimeoutError()
    monkeypatch.setattr('requests.post',timeout)
    assert not music.send(root,score['request_id'])['success']
    snap=voice.snapshot(root);assert len(snap['unresolved_requests'])==1
    assert snap['budget']['held']==.048
    assert all('held audio charge' in r['error'] for r in snap['requests'])
    assert not voice.send(root,speech['request_id'])['success'] and not music.send(root,score['request_id'])['success']
    assert len(posts)==1 and len(gets)==1

def test_unplayable_import_is_a_plain_refusal_with_no_spending(voice_world,tmp_path,monkeypatch):
    from backlot import music_panel as music
    root,posts,gets=voice_world;path=tmp_path/'not-music.wav';path.write_bytes(b'Not an audio recording.')
    monkeypatch.setattr(music,'choose_file',lambda:str(path));chosen=music.choose(root)
    with pytest.raises(ValueError,match='playable music.*Choose'):
        music.import_selected(root,{'choice_id':chosen['choice_id'],'source':'My recording','licence_note':'Owned by me.'})
    assert posts==[] and gets==[]

def test_disconnected_music_cannot_prepare_send_or_reserve_money(voice_world,monkeypatch):
    from backlot import music_panel as music
    from tools.cost_tracker import load_reservations
    root,posts,gets=voice_world;record=prepare(root);sign(root,'audio_spend',record,record['request_id'])
    monkeypatch.delenv('ELEVENLABS_API_KEY')
    with pytest.raises(ValueError,match='not connected'):
        music.prepare_music(root,{'brief_id':record['binding']['brief_id']})
    result=music.send(root,record['request_id']);assert not result['success'] and 'restart the board' in result['message']
    assert load_reservations(root)=={} and posts==[] and gets==[]
