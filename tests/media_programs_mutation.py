"""Explicitly loaded review probe: restore one faulty boundary in memory.

Never loaded by an ordinary run. No repository file is rewritten. Run only
the named regression tests with this plugin and all app folders isolated.
"""
import importlib
import os
import subprocess

def pytest_sessionstart(session):
    mutation=os.environ['FRONTLOT_MEDIA_MUTATION']
    if mutation=='reservation':
        from lib import programs
        programs.preflight=lambda *names:None
        return
    if mutation=='composer':
        from tools.video.video_compose import VideoCompose
        import shutil
        VideoCompose._ffmpeg_available=lambda self:bool(shutil.which('ffmpeg'))
        VideoCompose._remotion_available=lambda self:bool(shutil.which('npx'))
        return
    modules={
        'resolver':'lib.programs',
        'kling':'tools.video.kling_reference_video',
        'seedance':'tools.video.seedance_video',
        'seedream':'tools.graphics.seedream_image',
        'store':'lib.pathsafe',
        'reconciler':'scripts.reconcile_paid_calls',
        'runner':'scripts.frontlot_run',
        'signer':'scripts.gate_sign',
        'assistant':'scripts.claude_session',
    }
    name=modules[mutation]
    module=importlib.import_module(name)
    path=name.replace('.','/')+'.py'
    source=subprocess.check_output(['git','show','1b98763:'+path],text=True)
    exec(compile(source,module.__file__,'exec'),module.__dict__)
