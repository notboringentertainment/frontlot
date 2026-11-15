"""Discovery must never execute a program planted in the working directory."""
import os
from pathlib import Path
import pytest
from lib import programs

def executable(path, text='trusted'):
    path.write_text('#!/bin/sh\nprintf "'+text+'"\n')
    path.chmod(0o700)
    return path

@pytest.mark.parametrize('path',['',':','.:relative:','absolute-working-directory'])
def test_relative_search_entries_are_never_used(tmp_path,monkeypatch,path):
    work=tmp_path/'work';work.mkdir();safe=tmp_path/'safe';safe.mkdir()
    executable(work/'ffprobe','untrusted');trusted=executable(safe/'ffprobe')
    monkeypatch.chdir(work);monkeypatch.setenv('PATH',str(work) if path=='absolute-working-directory' else path)
    monkeypatch.delenv('OPENMONTAGE_FFPROBE',raising=False)
    monkeypatch.setattr(programs,'STANDARD_DIRS',(str(safe),))
    assert programs.require('ffprobe')==str(trusted)
    assert programs.run(['ffprobe'],capture_output=True,text=True,check=True).stdout=='trusted'

@pytest.mark.parametrize('setting',['ffprobe','./ffprobe','nested/ffprobe','~/ffprobe'])
def test_explicit_settings_must_be_absolute(tmp_path,monkeypatch,setting):
    executable(tmp_path/'ffprobe');monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('OPENMONTAGE_FFPROBE',setting)
    with pytest.raises(ValueError,match='absolute'):
        programs.run(['ffprobe'],capture_output=True)

def test_relative_argv_is_refused(tmp_path,monkeypatch):
    executable(tmp_path/'ffprobe');monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError,match='absolute'):
        programs.run(['./ffprobe'],capture_output=True)

def test_empty_environment_does_not_inherit(monkeypatch):
    monkeypatch.setenv('INVENTED_PARENT_ONLY','present')
    result=programs.run(['/usr/bin/env'],env={},capture_output=True,text=True,check=True)
    assert 'INVENTED_PARENT_ONLY=' not in result.stdout,'An explicitly empty environment inherited its parent.'
    child_path=next(line[5:] for line in result.stdout.splitlines() if line.startswith('PATH='))
    assert all(p and Path(p).is_absolute() for p in child_path.split(os.pathsep))

def test_absolute_override_is_the_executed_program(tmp_path,monkeypatch):
    chosen=executable(tmp_path/'chosen')
    monkeypatch.setenv('OPENMONTAGE_FFPROBE',str(chosen))
    assert programs.run(['ffprobe'],env={'PATH':'.'},capture_output=True,text=True,check=True).stdout=='trusted'

@pytest.mark.parametrize('options',[{'shell':True},{'executable':'./ffprobe'}])
def test_alternate_execution_cannot_bypass_discovery(tmp_path,monkeypatch,options):
    executable(tmp_path/'ffprobe','untrusted');monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError,match='directly'):
        programs.run(['/bin/echo','harmless'],capture_output=True,**options)

def test_child_working_folder_is_not_a_search_location(tmp_path,monkeypatch):
    work=tmp_path/'work';work.mkdir();safe=tmp_path/'safe';safe.mkdir()
    executable(work/'ffprobe','untrusted');executable(safe/'ffprobe')
    monkeypatch.delenv('OPENMONTAGE_FFPROBE',raising=False)
    monkeypatch.setattr(programs,'STANDARD_DIRS',(str(safe),))
    result=programs.run(['ffprobe'],cwd=work,env={'PATH':str(work)},capture_output=True,text=True,check=True)
    assert result.stdout=='trusted'

def test_explicit_program_in_child_working_folder_is_refused(tmp_path):
    exe=executable(tmp_path/'ffprobe','untrusted')
    with pytest.raises(ValueError,match='current folder'):
        programs.run([str(exe)],cwd=tmp_path,capture_output=True)
