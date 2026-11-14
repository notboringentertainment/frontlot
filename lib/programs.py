"""Shared executable discovery for icon-launched boards and command-line tools."""
import os
from pathlib import Path
import shutil
import subprocess

STANDARD_DIRS = ('/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin')

def which(name, *, path=None):
    name=os.fspath(name)
    setting='OPENMONTAGE_'+Path(name).name.upper().replace('-','_')
    def find(candidate):
        return shutil.which(candidate) if path is None else shutil.which(candidate,path=path)
    explicit=os.environ.get(setting,'').strip()
    if explicit:
        return find(os.path.expanduser(explicit))
    found=find(name)
    if found:return found
    if Path(name).name!=name:return None
    return next((found for directory in STANDARD_DIRS
                 if (found:=shutil.which(str(Path(directory)/name)))),None)

def require(name):
    result=which(name)
    if result is None:
        raise ValueError(f'{name} is missing or its explicit setting cannot run. Ask the person who starts Front Lot to install or configure it and restart the board. Nothing was reserved or sent.')
    return result

def preflight(*names):
    for name in names:
        executable=require(name)
        try:
            subprocess.run([executable,'-version'],stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,check=True,timeout=10)
        except (OSError,subprocess.SubprocessError):
            raise ValueError(f'{name} could not run. Ask the person who starts Front Lot to repair its installation and restart the board. Nothing was reserved or sent.') from None

def _command(args,kwargs):
    lookup_path=(kwargs.get('env') or {}).get('PATH')
    # Scripts such as npx also need their interpreter to be discoverable.
    env=dict(kwargs.get('env') or os.environ)
    env['PATH']=os.pathsep.join(dict.fromkeys([*env.get('PATH','').split(os.pathsep),*STANDARD_DIRS]))
    kwargs['env']=env
    if isinstance(args,(list,tuple)) and args:
        cmd=list(args)
        executable=os.fspath(cmd[0])
        if Path(executable).name==executable:
            found=which(executable,path=lookup_path)
            if found:cmd[0]=found
            elif os.environ.get('OPENMONTAGE_'+executable.upper().replace('-','_'),'').strip():
                require(executable)
        return cmd
    return args

def run(args,*a,**kw):return subprocess.run(_command(args,kw),*a,**kw)
def check_output(args,*a,**kw):return subprocess.check_output(_command(args,kw),*a,**kw)
def Popen(args,*a,**kw):return subprocess.Popen(_command(args,kw),*a,**kw)
