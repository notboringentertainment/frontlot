"""An icon-environment board, restricted to invented counted services."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import wave


def main():
    parent,projects,ready,counter,port=Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),Path(sys.argv[4]),int(sys.argv[5])
    parent=parent.resolve()
    for key in ('HOME','OPENMONTAGE_HOME','OPENMONTAGE_GATES_DIR','OPENMONTAGE_METADATA_PATH','TMPDIR'):
        assert Path(os.environ[key]).resolve().is_relative_to(parent),key
    assert projects.resolve().is_relative_to(parent) and ready.resolve().is_relative_to(parent) and counter.resolve().is_relative_to(parent)
    assert port!=4750 and os.environ['PATH']=='/usr/bin:/bin:/usr/sbin:/sbin'
    from tests import conftest as guard
    assert not guard._unsafe_locations(os.environ)
    sys.addaudithook(guard._audit_test_locations)
    repo=Path(__file__).resolve().parents[2]
    assert not (repo/'.env').exists() and not (repo/'.env').is_symlink()
    os.environ['ELEVENLABS_API_KEY']='invented-first-hands-only'
    os.environ['FAL_KEY']='invented-first-hands-only'
    os.environ['FRONTLOT_CLAUDE']='/nonexistent/invented-no-assistant'
    os.environ['OPENMONTAGE_PROJECTS_DIR']=str(projects)
    def count(kind):
        with counter.open('a') as stream:stream.write(json.dumps({'kind':kind})+'\n')
    import requests
    def catalogue(url,**kw):
        assert url=='https://api.elevenlabs.io/v1/voices';count('catalogue')
        class Reply:
            def raise_for_status(self):pass
            def json(self):return {'voices':[{'voice_id':'invented_voice','name':'Invented gallery narrator'}]}
        return Reply()
    def make(url,**kw):
        assert url=='https://api.elevenlabs.io/v1/text-to-speech/invented_voice';count('speech')
        audio=io.BytesIO()
        with wave.open(audio,'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(b'\0\0'*10400)
        class Reply:
            status_code=200;headers={'character-cost':'1','request-id':'invented-first-hands-speech'};content=audio.getvalue()
            def raise_for_status(self):pass
        return Reply()
    requests.get=catalogue;requests.post=make
    if os.environ.get('FIRST_HANDS_FAIL_LOCAL_FILING')=='1':
        from lib import audio_workspace
        probe=audio_workspace.probe_audio
        marker=counter.parent/'one-invented-filing-failure'
        def fail_once(*args,**kwargs):
            if not marker.exists():
                with marker.open('x') as stream:stream.write('The counted service replied; local filing failed once.')
                raise OSError('Invented local filing failure after the paid reply.')
            return probe(*args,**kwargs)
        audio_workspace.probe_audio=fail_once
    from tools.video import _shared
    def forbidden(*a,**kw):
        count('unexpected-footage-boundary');raise AssertionError('The speech rehearsal must not contact footage.')
    for name in ('upload_image_fal','fal_queue_submit','fal_queue_wait','fal_download'):setattr(_shared,name,forbidden)
    from lib import paths,events
    paths.PROJECTS_DIR=projects;events.PROJECTS_DIR=projects
    from backlot import server,state,tty
    server.PROJECTS_DIR=projects;server._PROJECTS_ROOT_STR=os.path.normcase(str(projects.resolve()));state.PROJECTS_DIR=projects
    original=server.create_app;apps=[];brokers=[]
    def create(*args,**kw):
        app=original(*args,**kw);apps.append(app)
        with ready.open('x') as stream:json.dump({'url':f'http://127.0.0.1:{port}/','token':kw['capability_token']},stream)
        return app
    def broker(registry,root,identity):
        process=subprocess.Popen([sys.executable,'-m','tests.backlot.signing_test_child','broker',root.name,identity],
            cwd=repo,env=dict(os.environ),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        brokers.append(process);registry.track_broker(process);return process
    server.create_app=create;tty._spawn_broker=broker
    from backlot.__main__ import cmd_serve
    try:return cmd_serve(port)
    finally:
        from tests.backlot.tty_helpers import finish_board_signers
        for app in apps:finish_board_signers(app)
        for process in brokers:process.wait(timeout=30)
        assert not guard._VIOLATIONS


if __name__=='__main__':raise SystemExit(main())
