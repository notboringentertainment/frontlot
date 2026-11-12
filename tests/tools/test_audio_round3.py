"""Positive legacy eligibility and atomic, counted audio recovery races."""
import json,os,subprocess,sys,threading,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
import yaml
from tests.tools.test_audio_governance import project,sign,prepare,approve,run,Transport,no_real_network_contact
from tools.audio import _governed as audio
from lib import receipts

@pytest.mark.parametrize('tool_name',['elevenlabs_tts','music_gen'])
@pytest.mark.parametrize('case',['none','outside','linked','subfolder','edited','downgraded','bad_history'])
def test_legacy_cannot_guess_or_ignore_verification(project,tmp_path,monkeypatch,tool_name,case):
    import requests,lib.paths,lib.events
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    from tools.audio.music_gen import MusicGen
    calls=[]
    class Response:
        content=b'invented bytes'
        def raise_for_status(self):pass
    def post(*args,**kwargs):calls.append(1);return Response()
    monkeypatch.setattr(requests,'post',post);monkeypatch.setenv('ELEVENLABS_API_KEY','invented-only')
    monkeypatch.chdir(tmp_path)
    inputs={'text':'Doors stay closed.','prompt':'A quiet lock.','duration_seconds':3,'output_path':str(tmp_path/'outside.mp3')}
    if case in ['outside','subfolder']:
        # A real signed production outside the repository's canonical registry.
        monkeypatch.setattr(lib.paths,'PROJECTS_DIR',tmp_path/'unrelated-registry')
        monkeypatch.setattr(lib.events,'PROJECTS_DIR',tmp_path/'unrelated-registry')
        (project/'project.json').write_text('{"production_kind":"trailer"}')
        if case=='outside':monkeypatch.chdir(project)
        else:
            (project/'assets').mkdir();inputs['project_dir']=str(project/'assets')
    elif case=='linked':
        (project/'escape').symlink_to(tmp_path,target_is_directory=True)
        inputs['output_path']=str(project/'escape'/'outside.mp3')
    elif case!='none':
        inputs.update(project_dir=str(project),output_path=str(project/'audio.mp3'))
        if case=='bad_history':(project/'approvals.jsonl').write_text((project/'approvals.jsonl').read_text()+'{"forged":true}\n')
        else:
            data=yaml.safe_load((project/'project.yaml').read_text())
            if case=='edited':data['wall_time_minutes']=99
            else:
                data['version']='1.2';data['provider_egress']=[{'provider':'fal','content_classes':['prompts']}]
            (project/'project.yaml').write_text(yaml.safe_dump(data))
    result=({'elevenlabs_tts':ElevenLabsTTS,'music_gen':MusicGen}[tool_name])().execute(inputs)
    assert not result.success
    assert calls==[]

def test_subprocess_inside_trailer_outside_registry_refuses_all_routes(project,tmp_path):
    repo=Path.cwd()
    (project/'project.json').write_text('{"production_kind":"trailer"}')
    code="""import json,requests
from tools.audio.elevenlabs_tts import ElevenLabsTTS
from tools.audio.music_gen import MusicGen
from tools.audio.tts_selector import TTSSelector
from tools.tool_registry import ToolRegistry
import tools.tool_registry
calls=[]
class Response:
 content=b'invented audio'
 def raise_for_status(self):pass
def post(*a,**k):calls.append(1);return Response()
requests.post=post
registry=ToolRegistry();registry.register(ElevenLabsTTS());registry.register(MusicGen())
tools.tool_registry.registry=registry
results=[]
for tool in [ElevenLabsTTS(),MusicGen(),TTSSelector(),registry.get('music_gen')]:
 r=tool.execute({'text':'Closed tonight.','prompt':'A quiet lock.','duration_seconds':3,'preferred_provider':'elevenlabs','output_path':'audio.mp3'})
 results.append(r.success)
print(json.dumps({'results':results,'posts':len(calls)}))
"""
    env=dict(os.environ,ELEVENLABS_API_KEY='invented-only',PYTHONPATH=os.environ.get('PYTHONPATH','')+os.pathsep+str(repo))
    r=subprocess.run([sys.executable,'-c',code],cwd=project,env=env,text=True,capture_output=True,check=True)
    result=json.loads(r.stdout)
    assert result=={'results':[False]*4,'posts':0}

def test_recovery_does_not_take_live_wal(project,monkeypatch):
    from tools.cost_tracker import resume_check
    r=prepare(project);approve(project,r);t=Transport()
    written=threading.Event();release=threading.Event();attempt=threading.Event();recovered=threading.Event()
    original=audio.stage_generation_wal;errors=[]
    def pause(*args,**kwargs):
        value=original(*args,**kwargs);written.set()
        assert release.wait(10)
        return value
    monkeypatch.setattr(audio,'stage_generation_wal',pause)
    def owner():
        try:run(project,r,t)
        except Exception as e:errors.append(e)
    def reader():
        attempt.set()
        try:resume_check(project);recovered.set()
        except Exception as e:errors.append(e)
    a=threading.Thread(target=owner);a.start()
    assert written.wait(10)
    b=threading.Thread(target=reader);b.start();assert attempt.wait(10)
    try:
        assert not recovered.wait(0.15), 'Recovery consumed a live call\'s WAL.'
    finally:
        release.set();a.join(10);b.join(10)
    assert not a.is_alive() and not b.is_alive() and errors==[]
    assert (t.calls,t.downloads)==(1,1)
    assert len(receipts.verified_generation_receipts(project))==1

_RACE_BARRIER = None

def _race_initialize(barrier):
    global _RACE_BARRIER
    _RACE_BARRIER = barrier

def _race_worker(arguments):
    import lib.paths,lib.events
    from tools.cost_tracker import BudgetExceededError,IndeterminatePaidCallError
    root,record=arguments;root=Path(root)
    lib.paths.PROJECTS_DIR=root.parent;lib.events.PROJECTS_DIR=root.parent
    t=Transport()
    _RACE_BARRIER.wait(timeout=30)
    try:
        run(root,record,t)
        outcome='completed'
    except (BudgetExceededError,IndeterminatePaidCallError):
        outcome='blocked'
    return outcome,t.calls,t.downloads

@pytest.mark.parametrize('mode',['threads','processes'])
def test_ceiling_race_three_hundred_times(project,mode):
    import hashlib,multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    from tools.cost_tracker import load_reservations
    template=(project/'project.yaml').read_bytes()
    if mode=='processes':
        context=multiprocessing.get_context('spawn')
        barrier=context.Barrier(2)
        pool=ProcessPoolExecutor(max_workers=2,mp_context=context,initializer=_race_initialize,initargs=(barrier,))
    else:
        barrier=threading.Barrier(2)
        pool=ThreadPoolExecutor(max_workers=2,initializer=_race_initialize,initargs=(barrier,))
    totals=[0,0]
    with pool:
        for i in range(300):
            root=project.parent/f'race-{mode}-{i}';root.mkdir()
            (root/'project.yaml').write_bytes(template)
            sign(root,'config',{'config_sha256':hashlib.sha256(template).hexdigest()},'project_config')
            records=[prepare(root),prepare(root)]
            for record in records:approve(root,record)
            results=list(pool.map(_race_worker,[(str(root),r) for r in records]))
            assert sorted(row[0] for row in results)==['blocked','completed'],(i,results)
            assert sum(row[1] for row in results)==1
            assert sum(row[2] for row in results)==1
            rows=list(load_reservations(root).values())
            assert len(rows)==1 and rows[0]['state']=='completed'
            assert len(receipts.verified_generation_receipts(root))==1
            totals[0]+=sum(row[1] for row in results);totals[1]+=sum(row[2] for row in results)
    assert totals==[300,300]

def _older_settings(project,version='1.2'):
    import hashlib
    data={'version':version,'budget_usd_cap':1,'wall_time_minutes':60,
          'cast_cap':{'characters':1,'locations':1},'default_video_endpoint':'invented-video',
          'provider_egress':{'provider':'fal','content_classes':['prompts']}}
    if version!='1.0':
        data['provider_egress']=[data['provider_egress']]
        data['qc']={'judge_provider':'openai','judge_model':'invented','policy_bundle_sha256':'a'*64,'max_attempts_per_series':1}
    if version=='1.2':data['qc'].update(hero_policy_sha256='b'*64,max_hero_attempts=1)
    raw=yaml.safe_dump(data).encode();(project/'project.yaml').write_bytes(raw)
    sign(project,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')

@pytest.mark.parametrize('version',['1.0','1.1','1.2'])
def test_positively_verified_older_audio_still_posts(project,monkeypatch,version):
    import requests
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    from tools.audio.music_gen import MusicGen
    _older_settings(project,version)
    calls=[]
    class Response:
        def __init__(self):self.content=('invented audio '+str(len(calls))).encode()
        def raise_for_status(self):pass
    def post(*args,**kwargs):calls.append(kwargs['json']);return Response()
    monkeypatch.setattr(requests,'post',post);monkeypatch.setenv('ELEVENLABS_API_KEY','invented-only')
    for index,tool in enumerate([ElevenLabsTTS(),MusicGen()]):
        r=tool.execute({'project_dir':str(project),'text':'Closed tonight.','prompt':'A quiet lock.',
            'duration_seconds':3,'output_path':str(project/(str(index)+'.mp3'))})
        assert r.success
    assert len(calls)==2
    assert calls[0]['text']=='Closed tonight.' and calls[1]['prompt']=='A quiet lock.'

@pytest.mark.parametrize('kind',['outside','linked','root_link'])
def test_verified_older_production_cannot_authorize_an_escaping_path(project,tmp_path,monkeypatch,kind):
    import requests
    from tools.audio.music_gen import MusicGen
    _older_settings(project)
    calls=[]
    def post(*args,**kwargs):calls.append(1);raise AssertionError('Path not approved')
    monkeypatch.setattr(requests,'post',post);monkeypatch.setenv('ELEVENLABS_API_KEY','invented-only')
    args={'project_dir':str(project),'prompt':'A quiet lock.','duration_seconds':3}
    if kind=='outside':args['output_path']=str(tmp_path/'outside.mp3')
    elif kind=='linked':
        (project/'escape').symlink_to(tmp_path,target_is_directory=True)
        args['output_path']=str(project/'escape'/'outside.mp3')
    else:
        alias=tmp_path/'alias';alias.symlink_to(project,target_is_directory=True)
        args.update(project_dir=str(alias),output_path=str(alias/'audio.mp3'))
    r=MusicGen().execute(args)
    assert not r.success and 'must not use links' in r.error and calls==[]

def test_recovery_reentrant_lock_accepts_relative_project_path(project,monkeypatch):
    r=prepare(project);approve(project,r);t=Transport()
    def crash(*args,**kwargs):raise OSError('invented interruption before completion')
    monkeypatch.setattr(audio,'complete_generation',crash)
    with pytest.raises(OSError,match='invented interruption'):run(project,r,t)
    repo=Path.cwd()
    code="from pathlib import Path; from tools.cost_tracker import resume_check; resume_check(Path('.')); print('recovered without a submission')"
    env=dict(os.environ,PYTHONPATH=os.environ.get('PYTHONPATH','')+os.pathsep+str(repo))
    result=subprocess.run([sys.executable,'-c',code],cwd=project,env=env,text=True,capture_output=True,timeout=5)
    assert result.returncode==0 and 'recovered without a submission' in result.stdout
    assert (t.calls,t.downloads)==(1,1)
    assert len(receipts.verified_generation_receipts(project))==1

def test_restoring_an_older_signed_settings_file_does_not_reactivate_legacy_audio(project,monkeypatch):
    import hashlib,requests
    from tools.audio.music_gen import MusicGen
    _older_settings(project)
    older=(project/'project.yaml').read_bytes()
    data=yaml.safe_load(older);data['version']='1.3'
    data['provider_egress']=[{'provider':'elevenlabs','content_classes':['speech_text','music_prompt','effect_prompt']}]
    newer=yaml.safe_dump(data).encode();(project/'project.yaml').write_bytes(newer)
    sign(project,'config',{'config_sha256':hashlib.sha256(newer).hexdigest()},'project_config')
    (project/'project.yaml').write_bytes(older)
    calls=[]
    class Response:
        content=b'invented audio'
        def raise_for_status(self):pass
    def post(*args,**kwargs):calls.append(1);return Response()
    monkeypatch.setattr(requests,'post',post);monkeypatch.setenv('ELEVENLABS_API_KEY','invented-only')
    r=MusicGen().execute({'project_dir':str(project),'output_path':str(project/'audio.mp3'),
                         'prompt':'A quiet lock.','duration_seconds':3})
    assert not r.success and calls==[]

@pytest.mark.parametrize('route',['prepare','execute','signer'])
def test_protected_audio_requires_the_latest_settings_approval(project,route):
    import hashlib
    from lib.project_config import ProjectConfigError
    original=(project/'project.yaml').read_bytes()
    r=prepare(project);approve(project,r);t=Transport()
    data=yaml.safe_load(original);data['wall_time_minutes']=61
    changed=yaml.safe_dump(data).encode();(project/'project.yaml').write_bytes(changed)
    sign(project,'config',{'config_sha256':hashlib.sha256(changed).hexdigest()},'project_config')
    (project/'project.yaml').write_bytes(original)
    with pytest.raises(ProjectConfigError,match='latest signed'):
        if route=='prepare':prepare(project)
        elif route=='execute':run(project,r,t)
        else:
            from scripts.gate_approve import construct
            construct(project,{'kind':'audio_spend','entity_id':r['request_id']})
    assert (t.calls,t.downloads)==(0,0)
