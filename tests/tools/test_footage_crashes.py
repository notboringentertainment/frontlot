"""Real killed processes at each durable boundary; every provider call is fake."""
import json,os,signal,subprocess,sys,time
from pathlib import Path
import pytest
from tests.tools.test_signed_footage import film,prepare,approve,workspace,clip_bytes  # noqa: F401


CHILD=r'''
import json,os,sys,threading
from pathlib import Path
from tests import conftest as isolation
assert not isolation._unsafe_locations(os.environ), 'The child needs a temporary home and signing folder.'
sys.addaudithook(isolation._audit_test_locations)
from lib import footage_requests as footage,receipts,supervised_production as sp,state_io
from tools.video import _shared
from tools import cost_tracker
root=Path(sys.argv[1]);identity=sys.argv[2];point=sys.argv[3];work=Path(sys.argv[4]);mode=sys.argv[5]
def log(name):
    with (work/'calls.jsonl').open('a') as stream:
        stream.write(json.dumps(name)+'\n');stream.flush();os.fsync(stream.fileno())
def barrier(name):
    if name!=point:return
    with (work/'ready').open('x') as stream:
        stream.write(name);stream.flush();os.fsync(stream.fileno())
    threading.Event().wait()
def wrap(module,name,point_name):
    original=getattr(module,name)
    def run(*args,**kwargs):
        result=original(*args,**kwargs);barrier(point_name);return result
    setattr(module,name,run)
def upload(path):log('upload');barrier('uploaded');return 'https://simulation.invalid/ref.png'
def submit(*args,**kwargs):log('post');barrier('submitted');return {'request_id':'invented-killed-job'}
def wait(*args,**kwargs):
    log('wait');barrier('received');return {'video':{'url':'https://simulation.invalid/clip.mp4'},'cash_usd':.5,'credits_usd':0}
def download(url,dest,**kwargs):
    log('download');Path(dest).write_bytes((work/'pattern.mp4').read_bytes());barrier('downloaded')
_shared.upload_image_fal=upload;_shared.fal_queue_submit=submit;_shared.fal_queue_wait=wait;_shared.fal_download=download
wrap(footage,'approval_material','request-verified')
original_reserve=cost_tracker.CostTracker.reserve
def hold(self,*args,**kwargs):
    barrier('before-hold');result=original_reserve(self,*args,**kwargs);barrier('held');return result
cost_tracker.CostTracker.reserve=hold
wrap(footage,'reserve_before_upload','reserved')
wrap(cost_tracker,'attach_request_id','attached')
wrap(receipts,'stage_generation_wal','wal')
original_move=state_io.atomic_move
def publish(src,dst):
    result=original_move(src,dst)
    if Path(dst)==Path(footage.tool_inputs(root,footage._read(root,identity))['output_path']):barrier('published')
    return result
state_io.atomic_move=publish
wrap(receipts,'_commit_generation','receipted')
wrap(cost_tracker.CostTracker,'reconcile','charged')
wrap(receipts,'complete_generation','completed')
original_append=sp._append
def append(*args,**kwargs):
    result=original_append(*args,**kwargs)
    if args[2].get('kind')=='take':barrier('take-saved')
    return result
sp._append=append
try:
    take=footage.execute(root,identity);result={'status':'complete','take_id':take['take_id']}
except Exception as exc:result={'status':'blocked','type':type(exc).__name__}
assert not isolation._VIOLATIONS,'The child attempted to use real folders or an outside service.'
with (work/(mode+'.json')).open('x') as stream:json.dump(result,stream)
'''


POINTS=['request-verified','before-hold','held','reserved','uploaded','submitted','attached',
        'received','downloaded','wal','published','receipted','charged','completed','take-saved']
RECOVERABLE={'request-verified','wal','published','receipted','charged','completed','take-saved'}


@pytest.mark.parametrize('point',POINTS)
def test_killed_request_never_buys_twice(film,tmp_path,point):
    from tests import conftest as isolation
    from tools.cost_tracker import nonterminal_reservations
    from tools.audio._governed import _tracker
    from lib.project_config import load_current_verified_project_config
    root,shot=film;record=prepare(film);approve(root,record)
    work=tmp_path/'crash-proof';work.mkdir();(work/'pattern.mp4').write_bytes(clip_bytes())
    env=dict(os.environ,OPENMONTAGE_PROJECTS_DIR=str(root.parent))
    assert not isolation._unsafe_locations(env)
    def launch(where,mode):
        return subprocess.Popen([sys.executable,'-c',CHILD,str(root),record['request_id'],where,str(work),mode],
            cwd=Path(__file__).resolve().parents[2],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    process=launch(point,'first')
    try:
        deadline=time.monotonic()+45
        while not (work/'ready').exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.05)
        assert (work/'ready').exists(),process.communicate(timeout=5)[1].decode() if process.poll() is not None else 'The crash boundary was not reached.'
        os.kill(process.pid,signal.SIGKILL);process.wait(timeout=10)
        assert process.returncode==-signal.SIGKILL
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
        if process.stderr:process.stderr.close()
    def calls():
        path=work/'calls.jsonl'
        return [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []
    before=calls()
    for mode in ['restarted','repeated']:
        process=launch('never',mode)
        try:
            _,error=process.communicate(timeout=45);assert process.returncode==0,error.decode()
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
        result=json.loads((work/(mode+'.json')).read_text())
        if point in RECOVERABLE:assert result['status']=='complete'
        else:assert result['status']=='blocked' and nonterminal_reservations(root)
    if point!='request-verified':assert calls()==before
    assert calls().count('post')<=1
    rows=[r for r in workspace().sp.history(root,shot) if r.get('kind')=='take']
    if point in RECOVERABLE:
        assert calls().count('post')==1 and len(rows)==1
        tracker=_tracker(root,load_current_verified_project_config(root))
        assert tracker.budget_spent_usd==pytest.approx(.5) and tracker.budget_reserved_usd==0
    else:assert rows==[]
    assert not (root/'checkpoint_assets.json').exists()
