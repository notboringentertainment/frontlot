"""The suite must refuse unsafe locations before it can run app code."""
import os
from pathlib import Path
import pwd
import subprocess
import sys

import pytest

SOURCE = Path(__file__).with_name('conftest.py')
REPO = SOURCE.parent.parent
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def run_probe(tmp_path, body, *, remove_redirect=None, protected_home=None):
    suite = tmp_path / 'probe'
    suite.mkdir()
    source = SOURCE.read_text()
    if protected_home is not None:
        source = source.replace('_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()',
                                f'_REAL_HOME = Path({str(protected_home)!r}).resolve()')
    if remove_redirect == 'home':
        source = source.replace('os.environ["HOME"] = str(home)', 'pass  # Deliberately remove the home redirect.')
    if remove_redirect in ('signing', 'named_home'):
        source = source.replace('os.environ["OPENMONTAGE_GATES_DIR"] = str(gates)', 'pass  # Deliberately remove the signing redirect.')
    (suite / 'conftest.py').write_text(source)
    (suite / 'test_probe.py').write_text(body)
    env = dict(os.environ, HOME=str(REAL_HOME),
               OPENMONTAGE_GATES_DIR=str(REAL_HOME / '.openmontage' / 'gates'),
               PYTHONPATH=str(REPO), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               PYTHONDONTWRITEBYTECODE='1')
    if remove_redirect == 'named_home':
        env['OPENMONTAGE_GATES_DIR']='~'+pwd.getpwuid(os.getuid()).pw_name+'/.openmontage/gates'
    return subprocess.run([sys.executable, '-m', 'pytest', str(suite), '-q', '-p', 'no:cacheprovider'],
                          cwd=suite, env=env, text=True, capture_output=True, timeout=30)


@pytest.mark.parametrize('redirect', ['home', 'signing', 'named_home'])
def test_removed_redirect_refuses_start_before_any_test(tmp_path, redirect):
    marker = tmp_path / 'test-ran'
    result = run_probe(tmp_path, f'from pathlib import Path\ndef test_probe():\n    Path({str(marker)!r}).write_text("ran")\n',
                       remove_redirect=redirect)
    assert result.returncode == 4, result.stdout + result.stderr
    assert 'Tests cannot start' in result.stdout + result.stderr
    assert not marker.exists()


def test_locations_are_safe_during_collection_and_allow_an_isolated_home(tmp_path):
    result = run_probe(tmp_path, '''import os
from pathlib import Path
from lib.gates import gates_dir
from backlot.server import backlot_metadata_dir, capability_token_path, write_capability_token, server_log, server_log_path
from backlot.tty import metadata_root, sessions_dir
root=Path(os.environ['FRONTLOT_TEST_RUN_ROOT'])
assert Path.home().is_relative_to(root)
assert gates_dir().is_relative_to(root)
assert backlot_metadata_dir().is_relative_to(root)
assert metadata_root().is_relative_to(root)
assert sessions_dir().is_relative_to(root)
write_capability_token(64998)
server_log('Invented isolation check.')
assert capability_token_path(64998).is_relative_to(root)
assert capability_token_path(64998).is_file()
assert server_log_path().is_relative_to(root)
assert 'Invented isolation check.' in server_log_path().read_text()

def test_probe(tmp_path, monkeypatch):
    home=tmp_path/'own-home'
    home.mkdir()
    signing=tmp_path/'own-signing'
    monkeypatch.setenv('HOME',str(home))
    monkeypatch.setenv('OPENMONTAGE_GATES_DIR',str(signing))
    assert backlot_metadata_dir()==home/'.openmontage'/'backlot'
    assert metadata_root()==signing
''')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("access", ["fallback", "read"])
def test_real_home_fallback_fails_loudly_even_if_the_app_catches_it(tmp_path, access):
    owner = tmp_path / 'pretend-owner'
    app = owner / '.openmontage'
    app.mkdir(parents=True)
    sentinel = app / 'invented-private-file'
    sentinel.write_text('An invented test value.')
    action = 'backlot_metadata_dir()' if access == 'fallback' else f'Path({str(sentinel)!r}).read_text()'
    result = run_probe(tmp_path, f'''import os
from pathlib import Path
from backlot.server import backlot_metadata_dir

def test_probe():
    os.environ['HOME']={str(owner)!r}
    try:
        {action}
    except Exception:
        pass
''', protected_home=owner)
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'A test tried to use the real app folder' in result.stdout + result.stderr


def test_board_child_with_real_home_is_refused_before_launch(tmp_path):
    # A local stand-in must never run. If the guard breaks, only a temporary
    # marker is written: this negative proof cannot start a real board.
    fake = tmp_path / 'fake-python'
    marker = tmp_path / 'child-ran'
    fake.write_text('#!' + sys.executable + '\nfrom pathlib import Path\n' +
                    f'Path({str(marker)!r}).write_text("ran")\n')
    fake.chmod(0o700)
    result = run_probe(tmp_path, f'''import os, subprocess

def test_probe():
    env=dict(os.environ,HOME={str(REAL_HOME)!r})
    subprocess.run([{str(fake)!r},'-m','backlot','serve','--port','64999'],env=env,check=True)
''')
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'A test tried to start the board with real app folders' in result.stdout + result.stderr
    assert not marker.exists()


def test_generic_python_child_with_stripped_home_is_refused(tmp_path):
    marker = tmp_path / 'child-ran'
    result = run_probe(tmp_path, f'''import os, subprocess, sys

def test_probe():
    env=dict(os.environ)
    env.pop('HOME', None)
    env.pop('OPENMONTAGE_GATES_DIR', None)
    subprocess.run([sys.executable, '-c', "from pathlib import Path; Path({str(marker)!r}).write_text('ran')"], env=env, check=True)
''')
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'A test tried to start a Python child with real app folders' in result.stdout + result.stderr
    assert not marker.exists()
