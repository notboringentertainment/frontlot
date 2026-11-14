"""A copied signed folder is not a registered production; aliases recover once."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.tools.test_audio_governance import (
    project, prepare, approve, run, Transport, no_real_network_contact,
)
from tests.tools.test_audio_round3 import _older_settings


def _legacy_call(root, tool_name, route):
    import requests
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    from tools.audio.music_gen import MusicGen
    from tools.tool_registry import ToolRegistry
    calls = []

    class Response:
        content = b'invented audio'

        def raise_for_status(self):
            pass

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    requests.post = post
    tool = {'elevenlabs_tts': ElevenLabsTTS, 'music_gen': MusicGen}[tool_name]()
    args = {'text': 'The gallery is closed.', 'prompt': 'A quiet bell.',
            'duration_seconds': 3, 'output_path': str(root / 'audio.mp3')}
    if route == 'generate':
        result = tool._generate(args, 'invented-only')
    elif route == 'registry':
        registry = ToolRegistry()
        registry.register(tool)
        result = registry.get(tool_name).execute(args)
    else:
        result = tool.execute(args)
    return {'success': result.success, 'error': result.error, 'posts': len(calls)}


@pytest.mark.parametrize('tool_name', ['elevenlabs_tts', 'music_gen'])
@pytest.mark.parametrize('route', ['execute', 'generate', 'registry', 'subprocess'])
@pytest.mark.parametrize('place', ['inside-trailer', 'inside-production', 'outside', 'linked', 'renamed'])
def test_signed_copy_cannot_authorize_legacy_audio(project, tmp_path, monkeypatch, tool_name, route, place):
    _older_settings(project)
    # Keep the original's name and signed files in the bypass cases.
    if place.startswith('inside-'):
        parent = tmp_path / 'enclosing'
        parent.mkdir()
        marker = {'production_kind': 'trailer'} if place == 'inside-trailer' else {'pipeline_type': 'authored-film'}
        (parent / 'project.json').write_text(json.dumps(marker))
        destination = parent / 'assets' / project.name
    elif place == 'renamed':
        destination = tmp_path / 'changed-name'
    else:
        scratch = tmp_path.parent / (tmp_path.name + '-outside')
        scratch.mkdir()
        destination = scratch / project.name
    shutil.copytree(project, destination)
    if place == 'linked':
        alias = tmp_path / 'linked-copy'
        alias.symlink_to(destination, target_is_directory=True)
        destination = alias
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'invented-only')
    if route == 'subprocess':
        repo = Path(__file__).resolve().parents[2]
        code = """import json,sys
from pathlib import Path
import lib.paths,lib.events
from tests.tools.test_audio_round4 import _legacy_call
lib.paths.PROJECTS_DIR=Path(sys.argv[1]);lib.events.PROJECTS_DIR=Path(sys.argv[1])
print(json.dumps(_legacy_call(Path(sys.argv[2]),sys.argv[3],'execute')))
"""
        env = dict(os.environ, PYTHONPATH=os.environ.get('PYTHONPATH', '') + os.pathsep + str(repo))
        completed = subprocess.run([sys.executable, '-c', code, str(tmp_path), str(destination), tool_name],
                                   cwd=tmp_path, env=env, text=True, capture_output=True, check=True, timeout=15)
        result = json.loads(completed.stdout)
    else:
        import requests
        # Restore the fake at test teardown, including when the assertion fails.
        monkeypatch.setattr(requests, 'post', requests.post)
        result = _legacy_call(destination, tool_name, route)
    assert result['posts'] == 0, result
    assert not result['success'], result
    if place == 'renamed':
        assert ('inside one production' in result['error'] or 'cannot be verified' in result['error']), result
    else:
        assert 'inside one production' in result['error'], result


@pytest.mark.parametrize('tool_name', ['elevenlabs_tts', 'music_gen'])
def test_registered_production_nested_in_another_production_is_refused(project, tmp_path, monkeypatch, tool_name):
    import requests
    _older_settings(project)
    # Even a configured registry must not hide the production enclosing it.
    (tmp_path / 'project.json').write_text('{"production_kind":"trailer"}')
    neutral = tmp_path.parent / (tmp_path.name + '-neutral')
    neutral.mkdir()
    monkeypatch.chdir(neutral)
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'invented-only')
    monkeypatch.setattr(requests, 'post', requests.post)
    result = _legacy_call(project, tool_name, 'execute')
    assert result['posts'] == 0, result
    assert not result['success'] and 'inside one production' in result['error'], result


def test_case_alias_recovers_crashed_audio_without_a_second_call(project, monkeypatch):
    from lib import receipts
    alias = project.with_name(project.name.upper())
    if not alias.exists() or not alias.samefile(project):
        pytest.skip('This check needs a case-insensitive filesystem.')
    record = prepare(project)
    approve(project, record)
    transport = Transport()

    def crash(*args, **kwargs):
        raise OSError('invented crash before completion')

    from tools.audio import _governed as audio
    monkeypatch.setattr(audio, 'complete_generation', crash)
    with pytest.raises(OSError, match='invented crash'):
        run(project, record, transport)
    repo = Path(__file__).resolve().parents[2]
    code = """import json,sys
from pathlib import Path
from tools.cost_tracker import resume_check,reservation_lock
from lib.receipts import verified_generation_receipts
root=Path(sys.argv[1])
with reservation_lock(Path(sys.argv[2])):
    resume_check(root)
print(json.dumps({'receipts':len(verified_generation_receipts(Path(sys.argv[2])))}))
"""
    env = dict(os.environ, PYTHONPATH=os.environ.get('PYTHONPATH', '') + os.pathsep + str(repo))
    completed = subprocess.run([sys.executable, '-c', code, str(alias), str(project)],
                               env=env, text=True, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {'receipts': 1}
    assert (transport.calls, transport.downloads) == (1, 1)
    assert len(receipts.verified_generation_receipts(project)) == 1
