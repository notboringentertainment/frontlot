import os
from pathlib import Path
from lib import programs

def test_explicit_then_path_then_standard(tmp_path,monkeypatch):
    exe=tmp_path/'invented-tool';exe.write_text('#!/bin/sh\nexit 0\n');exe.chmod(0o700)
    monkeypatch.setenv('PATH','/nonexistent')
    monkeypatch.setattr(programs,'STANDARD_DIRS',(str(tmp_path),))
    assert programs.which('invented-tool')==str(exe)
    monkeypatch.setenv('OPENMONTAGE_INVENTED_TOOL','/nonexistent/missing')
    assert programs.which('invented-tool') is None
    monkeypatch.setenv('OPENMONTAGE_INVENTED_TOOL',str(exe))
    assert programs.which('invented-tool')==str(exe)

def test_missing_everywhere(monkeypatch):
    monkeypatch.setenv('PATH','/nonexistent');monkeypatch.setattr(programs,'STANDARD_DIRS',())
    monkeypatch.delenv('OPENMONTAGE_FFPROBE',raising=False)
    import pytest
    with pytest.raises(ValueError,match='ffprobe'):programs.require('ffprobe')

def test_explicit_missing_program_is_not_silently_replaced(monkeypatch):
    import pytest
    monkeypatch.setenv('OPENMONTAGE_FFPROBE','/nonexistent/invented-probe')
    with pytest.raises(ValueError,match='ffprobe'):
        programs.run(['ffprobe','-version'],capture_output=True)

def test_broken_program_is_refused_before_use(tmp_path,monkeypatch):
    import pytest
    broken=tmp_path/'broken';broken.write_text('#!/bin/sh\nexit 2\n');broken.chmod(0o700)
    monkeypatch.setenv('OPENMONTAGE_FFPROBE',str(broken))
    with pytest.raises(ValueError,match='ffprobe could not run'):programs.preflight('ffprobe')
