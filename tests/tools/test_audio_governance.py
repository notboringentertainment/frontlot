"""Audio requests use real signed gates and a fake transport, never a service."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import pytest
import yaml
from lib import gates, receipts
from lib.canonical_json import record_sha256
from tools.cost_tracker import load_reservations, BudgetExceededError, IndeterminatePaidCallError

@pytest.fixture(autouse=True)
def no_real_network_contact(monkeypatch):
    """Count and refuse real socket contact in every test in this file."""
    import socket
    attempts = []
    def refuse(*args, **kwargs):
        attempts.append(1)
        raise AssertionError("Audio governance tests must use simulated transports.")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    yield attempts
    assert attempts == []


def sign(root, kind, record, entity):
    with gates.handler_context():
        token=gates.mint_gate_token(root.name,'assets','audio',record_sha256(record),user_response='yes')
        return receipts.record_human_approval(root,root.name,'assets','audio',record,token,kind,entity_id=entity)

@pytest.fixture
def project(tmp_path,monkeypatch):
    import lib.paths, lib.events
    monkeypatch.setattr(lib.paths,'PROJECTS_DIR',tmp_path)
    monkeypatch.setattr(lib.events,'PROJECTS_DIR',tmp_path)
    root=tmp_path/'invented';root.mkdir()
    data={'version':'1.3','budget_usd_cap':1,'wall_time_minutes':60,'cast_cap':{'characters':1,'locations':1},
          'default_video_endpoint':'invented-video','provider_egress':[{'provider':'elevenlabs','content_classes':['speech_text','music_prompt','effect_prompt']}],
          'qc':{'judge_provider':'openai','judge_model':'invented','policy_bundle_sha256':'a'*64,'max_attempts_per_series':1,'hero_policy_sha256':'b'*64,'max_hero_attempts':1}}
    (root/'project.yaml').write_text(yaml.safe_dump(data))
    sign(root,'config',{'config_sha256':hashlib.sha256((root/'project.yaml').read_bytes()).hexdigest()},'project_config')
    return root

class Transport:
    def __init__(self,mode='ok'):self.calls=0;self.downloads=0;self.mode=mode
    def submit(self, payload, key):
        self.calls+=1
        if self.mode=='timeout':raise TimeoutError('unknown billing')
        return {'request_id':'fake-job','cash_usd':0.2,'credits_usd':0.3}
    def download(self, result):
        self.downloads+=1
        if self.mode=='download':raise OSError('download failed')
        return b'invented audio bytes'

def prepare(root,**changes):
    from tools.audio import _governed as audio
    args=dict(kind='speech',model='invented-model',voice='invented-voice',prompt='The gallery opens tonight.',duration_seconds=3,
              max_cash_usd=0.4,max_credits_usd=0.6,price_expires_at=4102444800)
    args.update(changes)
    return audio.prepare(root,**args)

def approve(root,r):return sign(root,'audio_spend',r,r['request_id'])

def run(root,r,t):
    from tools.audio import _governed as audio
    return audio.execute(root,r,submit=t.submit,download=t.download)

def test_no_consent_means_zero_contact(project):
    r=prepare(project);t=Transport()
    with pytest.raises(ValueError,match='signed audio approval'):run(project,r,t)
    assert (t.calls,t.downloads)==(0,0)

@pytest.mark.parametrize('field,value',[('prompt','Changed words'),('voice','different'),('duration_seconds',4),('model','other'),('max_cash_usd',0.3)])
def test_changed_request_is_refused_before_contact(project,field,value):
    r=prepare(project);approve(project,r);r[field]=value;t=Transport()
    with pytest.raises(ValueError,match='changed'):run(project,r,t)
    assert (t.calls,t.downloads)==(0,0)

def test_duplicate_confirmation_buys_once(project):
    r=prepare(project);approve(project,r);t=Transport();run(project,r,t)
    with pytest.raises(ValueError,match='already used'):run(project,r,t)
    assert (t.calls,t.downloads)==(1,1)
    rows=receipts.verified_generation_receipts(project)
    assert len(rows)==1 and rows[0]['cost_usd']==0.5
    assert rows[0]['cash_usd']==0.2 and rows[0]['credits_usd']==0.3

def test_race_at_ceiling_only_one_proceeds(project):
    rs=[prepare(project),prepare(project)]
    for r in rs:approve(project,r)
    t=Transport()
    def attempt(r):
        try:run(project,r,t);return True
        except (BudgetExceededError, IndeterminatePaidCallError):return False
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,rs))
    assert sum(results)==1 and t.calls==1 and t.downloads==1

@pytest.mark.parametrize('mode',['timeout','download'])
def test_uncertain_outcome_stays_reserved_after_restart(project,mode):
    r=prepare(project);approve(project,r);t=Transport(mode)
    with pytest.raises((TimeoutError,OSError)):run(project,r,t)
    reservation=list(load_reservations(project).values())[0]
    assert reservation['state']=='pending_billing' and reservation['reserved_usd']==1
    other=prepare(project);approve(project,other)
    from tools.cost_tracker import IndeterminatePaidCallError
    with pytest.raises(IndeterminatePaidCallError):run(project,other,t)
    assert t.calls==1 and t.downloads==(mode=='download')

def test_expired_quote_and_non_audio_approval_refused(project):
    r=prepare(project,price_expires_at=1);t=Transport()
    with pytest.raises(ValueError,match='expired'):approve(project,r)
    r=prepare(project);sign(project,'poster',r,r['request_id'])
    with pytest.raises(ValueError,match='signed audio approval'):run(project,r,t)
    assert (t.calls,t.downloads)==(0,0)

def test_config_consent_and_unbounded_price_refuse_zero_contact(project):
    from lib.project_config import ProjectConfigError
    r=prepare(project);approve(project,r);t=Transport()
    data=yaml.safe_load((project/'project.yaml').read_text());data['provider_egress']=[]
    (project/'project.yaml').write_text(yaml.safe_dump(data))
    with pytest.raises(ProjectConfigError):run(project,r,t)
    with pytest.raises(ValueError,match='firm'):prepare(project,max_cash_usd=float('inf'))
    assert (t.calls,t.downloads)==(0,0)

def test_signer_rebuilds_saved_request_and_rechecks_before_signing(project):
    from scripts.gate_approve import construct
    r=prepare(project)
    req={'kind':'audio_spend','entity_id':r['request_id'],'project_id':project.name,
         'stage':'assets','scope':'audio','request_id':r['request_id']}
    built=construct(project,req)
    assert built.record==r
    changed=dict(r,prompt='A different announcement.')
    (project/'.audio-requests'/(r['request_id']+'.json')).write_text(__import__('json').dumps(changed))
    with pytest.raises(ValueError,match='Saved audio request changed'):built.pre_commit_check()

def test_quote_expires_after_approval(project,monkeypatch):
    from tools.audio import _governed as audio
    r=prepare(project);approve(project,r);t=Transport()
    monkeypatch.setattr(audio.time,'time',lambda:4102444801)
    with pytest.raises(ValueError,match='expired'):run(project,r,t)
    assert (t.calls,t.downloads)==(0,0)

def test_reduced_remaining_budget_refuses_approved_audio(project):
    from tools.audio._governed import _tracker
    from lib.project_config import load_verified_project_config
    from tools.cost_tracker import BudgetExceededError
    r=prepare(project);approve(project,r);t=Transport()
    tracker=_tracker(project,load_verified_project_config(project))
    entry=tracker.estimate('footage','invented',0.2);tracker.reserve(entry);tracker.reconcile(entry,0.2)
    with pytest.raises(BudgetExceededError):run(project,r,t)
    assert (t.calls,t.downloads)==(0,0)

@pytest.mark.parametrize('version',['1.0','1.1','1.2'])
def test_legacy_signed_settings_keep_exact_base_behavior(tmp_path,version):
    from lib.project_config import load_verified_project_config,ProjectConfigError
    root=tmp_path/'older';root.mkdir()
    data={'version':version,'budget_usd_cap':2,'wall_time_minutes':60,
          'cast_cap':{'characters':1,'locations':1},'default_video_endpoint':'invented-video',
          'provider_egress':{'provider':'fal','content_classes':['prompts','reference_images']}}
    if version!='1.0':
        data['provider_egress']=[data['provider_egress']]
        data['qc']={'judge_provider':'openai','judge_model':'invented','policy_bundle_sha256':'a'*64,'max_attempts_per_series':1}
    if version=='1.2':data['qc'].update(hero_policy_sha256='b'*64,max_hero_attempts=1)
    raw=yaml.safe_dump(data).encode();(root/'project.yaml').write_bytes(raw)
    receipt=sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    config=load_verified_project_config(root)
    assert config.data==data and config.budget_usd_cap==2
    assert config.egress=={'fal':frozenset(['prompts','reference_images'])}
    config.require_egress('fal','prompts','reference_images')
    with pytest.raises(ProjectConfigError):config.require_egress('elevenlabs','speech_text')
    assert (root/'project.yaml').read_bytes()==raw
    assert receipts.find_approval(root,'config')['signature']==receipt['signature']

def test_audio_approval_is_not_footage_consent(project):
    r=prepare(project);approve(project,r);t=Transport()
    assert receipts.find_approval(project,'storyboard_batch',record_sha256=record_sha256(r)) is None
    assert (t.calls,t.downloads)==(0,0)

def test_egress_refused_even_with_individual_approval(project):
    from lib.project_config import ProjectConfigError
    r=prepare(project);approve(project,r);t=Transport()
    data=yaml.safe_load((project/'project.yaml').read_text())
    data['provider_egress']=[{'provider':'fal','content_classes':['prompts']}]
    (project/'project.yaml').write_text(yaml.safe_dump(data))
    sign(project,'config',{'config_sha256':hashlib.sha256((project/'project.yaml').read_bytes()).hexdigest()},'project_config')
    r=prepare(project);approve(project,r)
    with pytest.raises(ProjectConfigError,match='provider egress'):run(project,r,t)
    assert (t.calls,t.downloads)==(0,0)

def test_restart_process_cannot_resubmit_unknown_billing(project):
    import os,subprocess,sys
    r=prepare(project);approve(project,r);t=Transport('timeout')
    with pytest.raises(TimeoutError):run(project,r,t)
    code="""import sys
from pathlib import Path
from tools.cost_tracker import resume_check,IndeterminatePaidCallError
try:resume_check(Path(sys.argv[1]))
except IndeterminatePaidCallError:print('billing pending; no submission');sys.exit(0)
sys.exit(1)
"""
    result=subprocess.run([sys.executable,'-c',code,str(project)],capture_output=True,text=True,env=os.environ.copy())
    assert result.returncode==0 and 'billing pending; no submission' in result.stdout
    assert (t.calls,t.downloads)==(1,0)

def test_duplicate_signed_confirmation_still_buys_once(project):
    r=prepare(project);approve(project,r);t=Transport();run(project,r,t)
    approve(project,r)
    with pytest.raises(ValueError,match='already used'):run(project,r,t)
    assert (t.calls,t.downloads)==(1,1)

def test_music_origin_preserves_generated_or_licensed_import():
    from jsonschema import Draft202012Validator
    from pathlib import Path
    import json
    schema=json.loads(Path('schemas/artifacts/asset_manifest.schema.json').read_text())
    origin=schema['properties']['assets']['items']['properties']['music_origin']
    validate=Draft202012Validator(origin).validate
    validate({'kind':'generated','approval_receipt_id':'invented','generation_receipt_id':'invented'})
    validate({'kind':'imported','licence_note':'Invented demonstration licence only.','file_sha256':'a'*64})
    from jsonschema import ValidationError
    with pytest.raises(ValidationError):validate({'kind':'imported'})

@pytest.mark.parametrize('kind,voice',[('music',None),('effect',None)])
def test_all_audio_classes_use_one_receipted_call(project,kind,voice):
    r=prepare(project,kind=kind,voice=voice);approve(project,r);t=Transport()
    run(project,r,t)
    assert (t.calls,t.downloads)==(1,1)
    row=receipts.verified_generation_receipts(project)[0]
    assert row['normalized_inputs_hash']==r['input_sha256']
    assert row['audio_approval_receipt_id']
    assert row['parameters_hash']==r['input_sha256']

def test_successful_charge_failed_download_keeps_known_breakdown(project):
    r=prepare(project);approve(project,r);t=Transport('download')
    with pytest.raises(OSError,match='download failed'):run(project,r,t)
    row=list(load_reservations(project).values())[0]
    assert row['state']=='pending_billing'
    assert row['cash_usd']==0.2 and row['credits_usd']==0.3
    assert receipts.verified_generation_receipts(project)==[]
    assert (t.calls,t.downloads)==(1,1)

def test_second_confirmation_in_parallel_buys_once(project):
    r=prepare(project);approve(project,r);t=Transport()
    def click():
        try:run(project,r,t);return 'sent'
        except ValueError as exc:
            assert 'already used' in str(exc)
            return 'duplicate'
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:click(),range(2)))
    assert sorted(results)==['duplicate','sent']
    assert (t.calls,t.downloads)==(1,1)

def test_crash_after_output_recovers_without_another_submission(project,monkeypatch):
    from tools.audio import _governed as audio
    from tools.cost_tracker import resume_check
    r=prepare(project);approve(project,r);t=Transport()
    original=audio.complete_generation
    def crash(*args,**kwargs):raise OSError('invented receipt interruption')
    monkeypatch.setattr(audio,'complete_generation',crash)
    with pytest.raises(OSError,match='receipt interruption'):run(project,r,t)
    monkeypatch.setattr(audio,'complete_generation',original)
    resume_check(project)
    assert list(load_reservations(project).values())[0]['state']=='completed'
    assert len(receipts.verified_generation_receipts(project))==1
    with pytest.raises(ValueError,match='already used'):run(project,r,t)
    assert (t.calls,t.downloads)==(1,1)

def test_real_signing_handler_and_no_terminal_shortcut(project,monkeypatch):
    import json
    from scripts import gate_approve as signer
    r=prepare(project)
    req={'kind':'audio_spend','entity_id':r['request_id'],'project_id':project.name,
         'stage':'assets','scope':'audio','request_id':r['request_id'],'summary':'Approve this exact invented audio request.'}
    directory=project/'.gate-requests';directory.mkdir()
    (directory/(r['request_id']+'.json')).write_text(json.dumps(req))
    shown=signer.construct(project,req);t=Transport()
    with pytest.raises(signer.GateHandlerError,match='terminal'):
        signer._decide(req,project,shown=shown,note=None)
    assert (t.calls,t.downloads)==(0,0)
    monkeypatch.setattr(signer,'require_tty',lambda:None)
    with gates.handler_context():row=signer._decide(req,project,shown=shown,note=None)
    assert row['record']==r and row['kind']=='audio_spend'
    run(project,r,t)
    assert (t.calls,t.downloads)==(1,1)

def test_legacy_schema_definitions_are_unchanged():
    import json
    from pathlib import Path
    schema=json.loads(Path('schemas/project_config.schema.json').read_text())
    expected=json.loads(Path('tests/tools/fixtures/audio_legacy_config_hashes.json').read_text())
    assert {k:record_sha256(schema['$defs'][k]) for k in expected}==expected

@pytest.mark.parametrize('mode',['timeout','download'])
def test_existing_reconciler_never_sends_audio_to_footage_service(project,mode,monkeypatch):
    import io
    from scripts import reconcile_paid_calls as reconciler
    r=prepare(project);approve(project,r);t=Transport(mode)
    with pytest.raises((TimeoutError,OSError)):run(project,r,t)
    calls=[]
    def no_footage_poll(*args,**kwargs):
        calls.append(1)
        raise AssertionError('Audio must not be sent to the footage service.')
    monkeypatch.setattr(reconciler,'fal_queue_status',no_footage_poll)
    out=io.StringIO()
    summary=reconciler.reconcile_project(project,api_key=None,out=out)
    assert len(summary['manual'])==1 and 'ElevenLabs' in out.getvalue()
    assert calls==[] and t.calls==1
    assert t.downloads==(mode=='download')
    assert list(load_reservations(project).values())[0]['state']=='pending_billing'

def test_gate_packet_reads_only_saved_audio_material(project):
    from backlot.state import _render_audio_spend, GATE_RENDERERS
    r=prepare(project)
    packet=_render_audio_spend(project,{'entity_id':r['request_id'],'approval_record':{'prompt':'wrong'}})
    assert packet['record']==r and not packet.get('packet_error')
    assert 'audio_spend' in GATE_RENDERERS
