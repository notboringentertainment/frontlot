"""Closing proofs use invented productions and counting service replacements."""
import json
import pytest
import yaml
from pathlib import Path
from tests.tools.test_audio_governance import project, sign, prepare, approve, run, Transport, no_real_network_contact
from lib import receipts
from lib.project_config import load_verified_project_config
from tools.audio import _governed as audio

@pytest.mark.parametrize('tool_name,route',[(t,r) for t in ['elevenlabs_tts','music_gen'] for r in ['direct','generate','registry']]+[('elevenlabs_tts','selector'),('music_gen','stage')])
@pytest.mark.parametrize('identity',['trailer','audio_settings'])
def test_old_audio_refuses_every_dispatch_before_post(project,monkeypatch,tool_name,route,identity):
    import requests
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    from tools.audio.music_gen import MusicGen
    from tools.audio.tts_selector import TTSSelector
    from tools.tool_registry import ToolRegistry
    import tools.tool_registry
    from lib.pipeline_loader import load_pipeline
    if identity=='trailer':
        (project/'project.json').write_text('{"production_kind":"trailer","pipeline_type":"authored-film@1.6"}')
        data=yaml.safe_load((project/'project.yaml').read_text())
        data['version']='1.2';data['provider_egress']=[{'provider':'fal','content_classes':['prompts']}]
        (project/'project.yaml').write_text(yaml.safe_dump(data))
    monkeypatch.setenv('ELEVENLABS_API_KEY','invented-test-only')
    calls=[]
    class Response:
        content=b'invented audio'
        def raise_for_status(self):pass
    def post(*args,**kwargs):calls.append(1);return Response()
    monkeypatch.setattr(requests,'post',post)
    registry=ToolRegistry();registry.register(ElevenLabsTTS());registry.register(MusicGen())
    monkeypatch.setattr(tools.tool_registry,'registry',registry)
    inputs={'project_dir':str(project),'text':'The museum is closed.','prompt':'A distant lock.','duration_seconds':3,
            'output_path':str(project/(route+tool_name+'.mp3')),'preferred_provider':'elevenlabs'}
    tool=registry.get(tool_name)
    if route=='generate':result=tool._generate(inputs,'invented-test-only')
    elif route=='selector' and tool_name=='elevenlabs_tts':result=TTSSelector().execute(inputs)
    elif route=='registry':result=registry.get(tool_name).execute(inputs)
    elif route=='stage':
        stage=next(s for s in load_pipeline('authored-film@1.6')['stages'] if s['name']=='assets')
        if tool_name=='music_gen':assert tool_name in stage['tools_available']
        result=registry.get(tool_name).execute(inputs)
    else:result=tool.execute(inputs)
    assert not result.success and 'only through an approved request' in result.error
    assert calls==[]

@pytest.mark.parametrize('link_kind',['folder','file','copied_foreign'])
def test_foreign_or_linked_audio_request_is_not_displayed(project,tmp_path,link_kind):
    from backlot.state import _render_audio_spend
    r=prepare(project)
    other=tmp_path/'other';other.mkdir()
    directory=other/'.audio-requests'
    original=project/'.audio-requests'
    if link_kind=='folder':directory.symlink_to(original,target_is_directory=True)
    else:
        directory.mkdir()
        path=directory/(r['request_id']+'.json')
        if link_kind=='file':path.symlink_to(original/path.name)
        else:path.write_bytes((original/path.name).read_bytes())
    packet=_render_audio_spend(other,{'entity_id':r['request_id']})
    assert packet.get('packet_error') and 'record' not in packet

def test_recovery_record_precedes_final_publication(project,monkeypatch):
    r=prepare(project);approve(project,r);t=Transport()
    original=audio.stage_generation_wal
    def inspect(root,**kwargs):
        output=kwargs['outputs'][0]
        assert not Path(output['output_path']).exists()
        assert Path(output['staging_path']).read_bytes()==b'invented audio bytes'
        return original(root,**kwargs)
    monkeypatch.setattr(audio,'stage_generation_wal',inspect)
    run(project,r,t)
    assert (t.calls,t.downloads)==(1,1)

def test_identical_content_with_separate_approvals_completes_twice(project):
    t=Transport()
    # Half-dollar bounds leave room for both independently approved calls.
    for _ in range(2):
        r=prepare(project,max_cash_usd=0.2,max_credits_usd=0.3);approve(project,r);run(project,r,t)
    rows=receipts.verified_generation_receipts(project)
    assert len(rows)==2 and rows[0]['parameters_hash']==rows[1]['parameters_hash']
    assert rows[0]['audio_approval_receipt_id']!=rows[1]['audio_approval_receipt_id']
    assert sum(row['cost_usd'] for row in rows)==1
    assert (t.calls,t.downloads)==(2,2)

def test_zero_total_bound_is_always_refused(project):
    t=Transport()
    with pytest.raises(ValueError,match='positive'):
        prepare(project,max_cash_usd=0,max_credits_usd=0)
    assert (t.calls,t.downloads)==(0,0)

def test_human_readable_expiry_keeps_signed_epoch(project):
    from scripts.gate_approve import construct
    r=prepare(project)
    b=construct(project,{'kind':'audio_spend','entity_id':r['request_id']})
    assert any('2100-01-01 00:00:00 UTC' in line for line in b.evidence)
    assert b.record['price_expires_at']==4102444800

def test_settings_change_without_budget_change_is_refused(project):
    r=prepare(project);approve(project,r);t=Transport()
    data=yaml.safe_load((project/'project.yaml').read_text());data['wall_time_minutes']=61
    (project/'project.yaml').write_text(yaml.safe_dump(data))
    import hashlib
    sign(project,'config',{'config_sha256':hashlib.sha256((project/'project.yaml').read_bytes()).hexdigest()},'project_config')
    with pytest.raises(ValueError,match='settings changed'):run(project,r,t)
    assert (t.calls,t.downloads)==(0,0)

@pytest.mark.parametrize('when',['construct','commit'])
def test_signer_checks_remaining_budget(project,when):
    from scripts.gate_approve import construct,GateHandlerError
    r=prepare(project);t=Transport();req={'kind':'audio_spend','entity_id':r['request_id']}
    built=construct(project,req)
    tracker=audio._tracker(project,load_verified_project_config(project))
    entry=tracker.estimate('footage','invented',0.1);tracker.reserve(entry);tracker.reconcile(entry,0.1)
    with pytest.raises(GateHandlerError,match='remaining production budget'):
        construct(project,req) if when=='construct' else built.pre_commit_check()
    assert (t.calls,t.downloads)==(0,0)

@pytest.mark.parametrize('part',['cash','credits'])
def test_overcharge_cannot_complete_or_download(project,part):
    from tools.cost_tracker import load_reservations
    r=prepare(project);approve(project,r);t=Transport()
    def submit(payload,key):
        result=t.submit(payload,key)
        result[part+'_usd']=0.7 if part=='credits' else 0.5
        return result
    with pytest.raises(ValueError,match='exceeds the approved bound'):
        audio.execute(project,r,submit=submit,download=t.download)
    assert (t.calls,t.downloads)==(1,0)
    assert receipts.verified_generation_receipts(project)==[]
    assert list(load_reservations(project).values())[0]['state']=='pending_billing'

def test_receipt_refuses_cash_credit_sum_mismatch(project):
    t=Transport()
    with pytest.raises(ValueError,match='exactly once'):
        receipts._build_generation_receipt(project,execution_id='invented',tool='elevenlabs_audio',
            normalized_inputs_hash='a'*64,output_sha256='b'*64,cost_usd=0.2,model_endpoint='invented',
            started_at='invented',finished_at='invented',
            audio_charge={'cash_usd':0.2,'credits_usd':0.3,'audio_approval_receipt_id':'invented'})
    assert (t.calls,t.downloads)==(0,0)

def test_last_fraction_of_a_cent_is_usable(project):
    t=Transport()
    tracker=audio._tracker(project,load_verified_project_config(project))
    entry=tracker.estimate('footage','invented',0.9999);tracker.reserve(entry);tracker.reconcile(entry,0.9999)
    r=prepare(project,max_cash_usd=0.0001,max_credits_usd=0);approve(project,r)
    def submit(payload,key):
        result=t.submit(payload,key);result.update(cash_usd=0.0001,credits_usd=0);return result
    audio.execute(project,r,submit=submit,download=t.download)
    assert (t.calls,t.downloads)==(1,1)

def test_true_legacy_production_retains_three_posts(project,monkeypatch):
    import requests
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    from tools.audio.music_gen import MusicGen
    from tools.audio.tts_selector import TTSSelector
    from tools.tool_registry import ToolRegistry
    import tools.tool_registry
    data=yaml.safe_load((project/'project.yaml').read_text());data['version']='1.2'
    data['provider_egress']=[{'provider':'fal','content_classes':['prompts']}]
    (project/'project.yaml').write_text(yaml.safe_dump(data))
    import hashlib
    sign(project,'config',{'config_sha256':hashlib.sha256((project/'project.yaml').read_bytes()).hexdigest()},'project_config')
    monkeypatch.setenv('ELEVENLABS_API_KEY','invented-test-only')
    calls=[]
    class Response:
        def __init__(self):self.content=('invented audio '+str(len(calls))).encode()
        def raise_for_status(self):pass
    def post(*args,**kwargs):calls.append(1);return Response()
    monkeypatch.setattr(requests,'post',post)
    registry=ToolRegistry();registry.register(ElevenLabsTTS());registry.register(MusicGen())
    monkeypatch.setattr(tools.tool_registry,'registry',registry)
    for i,tool in enumerate([ElevenLabsTTS(),MusicGen(),TTSSelector()]):
        result=tool.execute({'project_dir':str(project),'text':'Closed tonight.','prompt':'A quiet lock.',
            'duration_seconds':3,'preferred_provider':'elevenlabs','output_path':str(project/(str(i)+'.mp3'))})
        assert result.success
    assert len(calls)==3

@pytest.mark.parametrize('attribution',['project','output','cwd'])
def test_historical_signed_trailer_without_marker_still_refuses(project,monkeypatch,attribution):
    import requests
    from tools.audio.music_gen import MusicGen
    from tests.backlot.test_trailer_closing_board import pin
    pin(project)
    data=yaml.safe_load((project/'project.yaml').read_text());data['version']='1.2'
    data['provider_egress']=[{'provider':'fal','content_classes':['prompts']}]
    (project/'project.yaml').write_text(yaml.safe_dump(data))
    calls=[]
    def post(*args,**kwargs):calls.append(1);raise AssertionError('Not approved')
    monkeypatch.setattr(requests,'post',post);monkeypatch.setenv('ELEVENLABS_API_KEY','invented-test-only')
    inputs={'prompt':'A quiet lock.','duration_seconds':3}
    if attribution=='project':inputs['project_dir']=str(project)
    elif attribution=='output':inputs['output_path']=str(project/'music.mp3')
    else:monkeypatch.chdir(project)
    result=MusicGen().execute(inputs)
    assert not result.success and 'only through an approved request' in result.error and calls==[]

def test_failure_above_last_cent_has_four_place_wording(project):
    from tools.cost_tracker import BudgetExceededError
    tracker=audio._tracker(project,load_verified_project_config(project))
    e=tracker.estimate('footage','invented',0.9999);tracker.reserve(e);tracker.reconcile(e,0.9999)
    r=prepare(project,max_cash_usd=0.0002,max_credits_usd=0);approve(project,r);t=Transport()
    with pytest.raises(BudgetExceededError,match=r'needs \$0.0002; only \$0.0001 remains'):
        run(project,r,t)
    assert (t.calls,t.downloads)==(0,0)
