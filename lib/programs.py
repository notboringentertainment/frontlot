"""Shared executable discovery for icon-launched boards and command-line tools."""
import os
from pathlib import Path
import shutil
import subprocess

STANDARD_DIRS = ('/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin')

def _search_path(path):
    entries=(os.environ.get('PATH','') if path is None else path).split(os.pathsep)
    return os.pathsep.join(dict.fromkeys(p for p in [*entries,*STANDARD_DIRS]
        if p and Path(p).is_absolute() and Path(p).resolve()!=Path.cwd().resolve()))

def _candidate(name):
    name=os.fspath(name)
    if Path(name).name!=name:
        if not Path(name).is_absolute():
            raise ValueError('A program path must be absolute. Relative programs cannot run in Front Lot.')
        return name
    setting='OPENMONTAGE_'+name.upper().replace('-','_')
    explicit=os.environ.get(setting,'').strip()
    if explicit and not Path(explicit).is_absolute():
        raise ValueError(f'{setting} must name an absolute program path. Ask the person who starts Front Lot to correct it.')
    return explicit or name

def which(name, *, path=None):
    found=shutil.which(_candidate(name),path=_search_path(path))
    return found if (found and Path(found).is_absolute()
        and Path(found).resolve().parent!=Path.cwd().resolve()) else None

def require(name, *, path=None):
    result=which(name,path=path)
    if result is None:
        raise ValueError(f'{Path(name).name} is missing or its explicit setting cannot run. Ask the person who starts Front Lot to install or configure it and restart the board.')
    return result

def preflight(*names):
    for name in names:
        try:
            executable=require(name)
            run([executable,'-version'],stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,check=True,timeout=10)
        except ValueError as exc:
            raise ValueError(str(exc)+' Nothing was reserved or sent.') from None
        except (OSError,subprocess.SubprocessError):
            raise ValueError(f'{name} could not run. Ask the person who starts Front Lot to repair its installation and restart the board. Nothing was reserved or sent.') from None

def _command(args,kwargs):
    if kwargs.get('shell') or kwargs.get('executable') is not None:
        raise ValueError('Front Lot starts programs directly. Shell and alternate-program overrides are not available.')
    # Scripts such as npx also need their interpreter to be discoverable.
    env=dict(os.environ if kwargs.get('env') is None else kwargs['env'])
    child_cwd=Path(kwargs.get('cwd') or Path.cwd()).resolve()
    env['PATH']=os.pathsep.join(p for p in _search_path(env.get('PATH','')).split(os.pathsep)
                               if p and Path(p).resolve()!=child_cwd)
    kwargs['env']=env
    if isinstance(args,(list,tuple)) and args:
        cmd=list(args)
        cmd[0]=require(os.fspath(cmd[0]),path=env['PATH'])
        if Path(cmd[0]).resolve().parent==child_cwd:
            raise ValueError('A program in the current folder cannot run. Configure its installed full path instead.')
        return cmd
    raise ValueError('Give Front Lot a program and its arguments as a list.')

def run(args,*a,**kw):return subprocess.run(_command(args,kw),*a,**kw)
def check_output(args,*a,**kw):return subprocess.check_output(_command(args,kw),*a,**kw)
def Popen(args,*a,**kw):return subprocess.Popen(_command(args,kw),*a,**kw)
