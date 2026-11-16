"""Real signer, paused while its own footage preparation owns the lock."""
import os,sys,time,threading
from pathlib import Path

if __name__=='__main__':
    role,project,identity=sys.argv[1:]
    deadline=threading.Timer(45,lambda:os._exit(88));deadline.daemon=True;deadline.start()
    if role=='approve':
        from lib import footage_requests
        original=footage_requests._approval_material
        directory=Path(os.environ['STUCK_PREPARATION_MARKERS'])
        assert directory.resolve().is_relative_to(Path(os.environ['TMPDIR']).resolve())
        def paused(*args,**kwargs):
            record=original(*args,**kwargs)
            marker=directory/str(os.getpid())
            with marker.with_suffix('.ready').open('x') as stream:stream.write('The real signer owns the purchase lock.')
            until=time.monotonic()+20
            while not marker.with_suffix('.release').exists():
                if time.monotonic()>until:raise AssertionError('The signing packet did not release its test barrier.')
                time.sleep(.01)
            return record
        footage_requests._approval_material=paused
        from scripts import gate_approve
        raise SystemExit(gate_approve.main(['--project',project,'--request',identity]))
    from scripts import gate_sign
    gate_sign._gate_approve_argv=lambda p,r:[sys.executable,'-m','tests.backlot.stuck_preparation_child','approve',p,r]
    raise SystemExit(gate_sign.main(['--project',project,'--request',identity,'--broker']))
