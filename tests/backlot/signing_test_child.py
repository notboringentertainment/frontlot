"""Run the real signer with a last-resort deadline only in disposable tests."""
import os,sys,threading

if __name__=='__main__':
    role,project,identity=sys.argv[1:]
    if role=='approve':
        deadline=threading.Timer(20,lambda:os._exit(88));deadline.daemon=True;deadline.start()
        from scripts import gate_approve
        raise SystemExit(gate_approve.main(['--project',project,'--request',identity]))
    from scripts import gate_sign
    gate_sign._gate_approve_argv=lambda p,r:[sys.executable,'-m','tests.backlot.signing_test_child','approve',p,r]
    raise SystemExit(gate_sign.main(['--project',project,'--request',identity,'--broker']))
