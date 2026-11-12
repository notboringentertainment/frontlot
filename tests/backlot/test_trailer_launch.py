"""Trailer launch isolation, using only local fakes and invented productions."""
import hashlib
import json
from pathlib import Path
import pytest
from backlot import claude_settings as cs
from tests.lib.look_lock_helpers import pin_project
from lib.checkpoint import init_project


@pytest.mark.parametrize('resume',[False,True])
def test_trailer_launch_is_strict_and_keeps_frontlot_plugin(resume):
    argv=cs.launch_argv(claude='/fake/claude',mod_dir=Path('/frontlot'),settings_file=Path('/s'),
                       brief_file=Path('/b'),session_id='invented',resume=resume,prompt='hello',trailer=True)
    assert '--strict-mcp-config' in argv
    assert argv[argv.index('--mcp-config')+1]==cs.EMPTY_MCP
    assert argv[argv.index('--mcp-config')+2].startswith('--')
    assert argv[argv.index('--plugin-dir')+1]=='/frontlot'
    assert argv[-1]=='hello'


@pytest.mark.parametrize('version',['1.1','1.2','1.3','1.4','1.5'])
def test_older_brief_settings_and_launch_match_base_bytes(tmp_path,monkeypatch,version):
    root=init_project('p',title='Night Window',pipeline_type='authored-film',pipeline_dir=tmp_path/'projects')
    pin_project(root,version)
    repo=tmp_path/'repo';repo.mkdir()
    monkeypatch.setattr(cs,'secret_paths',lambda *a:[])
    monkeypatch.setattr(cs,'writeros_package',lambda *a:None)
    monkeypatch.setattr(cs,'story_drive_folder',lambda *a:None)
    baseline=json.loads((Path(__file__).parent/'fixtures/claude_base_bytes.json').read_text())
    settings=cs.build_settings(repo_root=repo,film_root=root,meta_root=tmp_path/'meta',environ={})
    normalized=json.dumps(settings,indent=2).replace(str(tmp_path.resolve()),'/invented')
    assert hashlib.sha256(normalized.encode()).hexdigest()==baseline[version]['settings']
    brief=cs.build_brief(film_title='Night Window',film_slug='p')
    assert hashlib.sha256(brief.encode()).hexdigest()==baseline[version]['brief']
    argv=cs.launch_argv(claude='/c',mod_dir=Path('/m'),settings_file=Path('/s'),brief_file=Path('/b'),
                        session_id='p',resume=False,prompt='hello')
    assert hashlib.sha256(json.dumps(argv).encode()).hexdigest()==baseline[version]['argv']


def test_trailer_selfcheck_keeps_plugin_and_requires_its_tool_evidence(tmp_path):
    from tests.backlot.test_claude_settings import stream
    d,a,o=tmp_path/'deny',tmp_path/'allow',tmp_path/'outside'
    args=dict(deny_file=d,allow_file=a,outside_file=o,deny_secret='DENIED',allow_secret='ALLOWED',trailer=True)
    proof=stream(('Bash',{'command':f'cat {d}'},'denied',True),
                 ('Bash',{'command':f'cat {a}'},'ALLOWED',False),
                 ('Write',{'file_path':str(o)},'denied',True))
    assert not cs.selfcheck_passed(proof,**args)
    assert cs.selfcheck_passed(proof+'\n'+stream((cs.TOOL_NAME,{'check':'frontlot-startup-check'},
                                  "Front Lot couldn't confirm receipt",False)),**args)
    argv=cs.selfcheck_argv(claude='/fake',settings_file=Path('/s'),prompt='p',mod_dir=Path('/frontlot'),trailer=True)
    assert '--strict-mcp-config' in argv and argv[argv.index('--mcp-config')+1]==cs.EMPTY_MCP
    assert argv[argv.index('--plugin-dir')+1]=='/frontlot'
    assert 'frontlot_run' in cs.selfcheck_prompt(d,a,o,trailer=True)


def test_trailer_preflight_executes_plugin_probe_and_does_not_reuse_old_cache(tmp_path,monkeypatch):
    from scripts import claude_session as session
    from tests.backlot.test_claude_settings import stream
    meta=tmp_path/'meta';meta.mkdir();work=tmp_path/'work';work.mkdir()
    paths={'sandbox_ok':meta/'ok','canary':meta/'deny'}
    monkeypatch.setattr(session,'paths',lambda slug:paths)
    monkeypatch.setattr(session,'canary_paths',lambda slug,w:(paths['canary'],w/'allow',w/'outside'))
    settings=tmp_path/'settings.json';settings.write_text('{}')
    calls=[]
    class Probe:
        pid=123
        returncode=0
        def __init__(self,argv,**kw):
            calls.append(argv)
            assert '--strict-mcp-config' in argv
            assert '--plugin-dir' in argv
            assert argv[argv.index('--mcp-config')+1]==cs.EMPTY_MCP
        def communicate(self,timeout):
            proof=stream(('Bash',{'command':f"cat {paths['canary']}"},'denied',True),
                         ('Bash',{'command':f'cat {work}/allow'},(work/'allow').read_text(),False),
                         ('Write',{'file_path':str(work/'outside')},'denied',True),
                         (cs.TOOL_NAME,{'check':'frontlot-startup-check'},'Front Lot did not confirm receipt',False))
            return proof,None
    monkeypatch.setattr(session.subprocess,'Popen',Probe)
    import hashlib
    paths['sandbox_ok'].write_text(json.dumps({'version':'fake','settings_sha256':hashlib.sha256(settings.read_bytes()).hexdigest()}))
    assert session.sandbox_selfcheck('p','/fake','fake',settings,work,{},trailer=True)
    assert len(calls)==1
    assert json.loads(paths['sandbox_ok'].read_text())['frontlot_tool_check']==1


from tests.backlot.test_claude_session import world


def test_trailer_broker_keeps_operation_route_with_strict_fake_launch(world,tmp_path):
    from tests.backlot.claude_fakes import FAKE_CLAUDE,hello,live_endpoint,post_live
    from tests.backlot.test_claude_session import start
    # The executable is a local fake. No owner assistant, model or provider is launched.
    fake=tmp_path/'strict-fake'
    fake.write_text('#!/usr/bin/env python3\nimport sys\nassert "--strict-mcp-config" in sys.argv\nassert "--plugin-dir" in sys.argv\n'+FAKE_CLAUDE.split('\n',1)[1])
    fake.chmod(0o755)
    (world['film']/'project.json').write_text(json.dumps({'title':'Night Window','production_kind':'trailer','pipeline_type':'authored-film@1.6'}))
    start(world,env=dict(world['env'],FRONTLOT_CLAUDE=str(fake)))
    sock,token=live_endpoint(world['film'])
    hello(sock,token,'e1')
    result=post_live(sock,token,'/run',{'key':'local-check','op':'test_free','params':{},'epoch':'e1','turnId':''})
    assert result['status']=='running'
    reply=post_live(sock,token,'/inbox',{'epoch':'e1'})
    assert reply['submit'].startswith('[Front Lot]')
