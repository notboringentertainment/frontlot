"""Run the real signer with a last-resort deadline only in disposable tests."""
import os,sys,threading

if __name__=='__main__':
    role,project,identity=sys.argv[1:]
    if role=='approve':
        deadline=threading.Timer(20,lambda:os._exit(88));deadline.daemon=True;deadline.start()
        from scripts import gate_approve
        barrier=os.environ.get('FRONTLOT_TEST_RECEIPT_BARRIER')
        if barrier:
            from pathlib import Path
            path=Path(barrier);assert path.resolve().is_relative_to(Path(os.environ['TMPDIR']).resolve())
            original=gate_approve.record_human_approval
            def slow_receipt(*args,**kwargs):
                import time
                with path.open('x') as f:f.write('Recording the exact signed receipt.')
                time.sleep(7)
                return original(*args,**kwargs)
            gate_approve.record_human_approval=slow_receipt
        raise SystemExit(gate_approve.main(['--project',project,'--request',identity]))
    from scripts import gate_sign
    gate_sign._gate_approve_argv=lambda p,r:[sys.executable,'-m','tests.backlot.signing_test_child','approve',p,r]
    raise SystemExit(gate_sign.main(['--project',project,'--request',identity,'--broker']))
