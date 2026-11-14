"""Invented held charges, real signers, and bounded ownership for these checks."""
import json,os,subprocess,sys,uuid,tempfile
from pathlib import Path
import pytest
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http
from tests.tools.test_signed_footage import film,provider,prepare,approve,workspace


@pytest.fixture(autouse=True)
def signing_folder(monkeypatch):
    parent=Path(os.environ.get('FRONTLOT_FOLLOWUPS_PARENT') or os.environ.get('OPENMONTAGE_HOME') or tempfile.gettempdir())
    folder=parent/('g'+uuid.uuid4().hex[:6]);folder.mkdir()
    monkeypatch.setenv('OPENMONTAGE_GATES_DIR',str(folder))
    monkeypatch.setenv('FRONTLOT_CLAUDE','/nonexistent/invented-assistant')


@pytest.fixture
def bounded_signers(monkeypatch):
    from backlot import tty
    owned=[]
    def spawn(registry,root,identity):
        process=subprocess.Popen([sys.executable,'-m','tests.backlot.signing_test_child','broker',root.name,identity],
            cwd=Path(__file__).resolve().parents[2],env=dict(os.environ),stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        owned.append(process);registry.track_broker(process);return process
    monkeypatch.setattr(tty,'_spawn_broker',spawn)
    yield owned
    # The safety deadline belongs to the disposable test child, not the app.
    # Failed red tests also leave no detached signer or wrapper behind.
    for process in owned:process.wait(timeout=30)


def held(request,medium,monkeypatch):
    from tools.cost_tracker import nonterminal_reservations
    if medium=='footage':
        root,shot=request.getfixturevalue('film');calls=request.getfixturevalue('provider')
        record=prepare((root,shot));approve(root,record)
        def timeout(*a,**k):calls['waits'].append(1);raise TimeoutError('Invented unknown result.')
        monkeypatch.setattr('tools.video._shared.fal_queue_wait',timeout)
        with pytest.raises(ValueError,match='money is held'):workspace().execute(root,record['request_id'])
        counts=lambda:{key:len(value) for key,value in calls.items()}
        ceiling=50
    else:
        from backlot import voice_panel as voice
        from tests.tools.test_audio_governance import sign
        root,posts,gets=request.getfixturevalue('voice_world');voice.catalogue(root)
        record=voice.prepare_voice(root,{'line_index':0,'voice':'invented_voice','rights_confirmed':True,'notes':''})['request']
        sign(root,'audio_spend',record,record['request_id'])
        def timeout(*a,**k):posts.append(1);raise TimeoutError('Invented unknown result.')
        monkeypatch.setattr('requests.post',timeout)
        assert not voice.send(root,record['request_id'])['success']
        counts=lambda:{'posts':len(posts),'gets':len(gets)}
        ceiling=1
    reservation,=nonterminal_reservations(root)
    return root,reservation,counts,ceiling


def figures(root,ceiling,fresh=False):
    if fresh:
        code='''import json,sys
from tools.cost_tracker import CostTracker
t=CostTracker(budget_total_usd=float(sys.argv[2]),reserve_pct=0,cost_log_path=__import__('pathlib').Path(sys.argv[1])/'cost_log.json')
print(json.dumps([t.budget_spent_usd,t.budget_reserved_usd,t.budget_remaining_usd]))'''
        return json.loads(subprocess.check_output([sys.executable,'-c',code,str(root),str(ceiling)],env=dict(os.environ),text=True))
    from tools.cost_tracker import CostTracker
    t=CostTracker(budget_total_usd=ceiling,reserve_pct=0,cost_log_path=root/'cost_log.json')
    return [t.budget_spent_usd,t.budget_reserved_usd,t.budget_remaining_usd]


def signed_review(root,reservation,charged=False):
    from lib.media_spending import request_resolution
    from lib import gates,receipts
    from lib.canonical_json import record_sha256
    gate=request_resolution(root,reservation['reservation_id'],charged,.001 if charged else 0,.002 if charged else 0,'I checked the invented service account.')
    with gates.handler_context():
        token=gates.mint_gate_token(root.name,gate['stage'],gate['scope'],record_sha256(gate['approval_record']),user_response='yes')
        receipts.record_human_approval(root,root.name,gate['stage'],gate['scope'],gate['approval_record'],token,
            'audio_resolution',entity_id=reservation['reservation_id'])
    return gate
