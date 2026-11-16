"""Keep every pytest run away from the owner's signing and app folders.

This setup runs before collection, so module fixtures and child board launchers
inherit an isolated home as well as an isolated signing directory. Tests may
still choose their own temporary homes and signing folders.
"""
import os
from pathlib import Path
import pwd
import sys
import tempfile

import pytest


_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
_REAL_APP = (_REAL_HOME / '.openmontage').resolve()
_VIOLATIONS = set()


def _inside_real_app(path):
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return False  # Let unrelated invalid paths report their normal filesystem error.
    return resolved == _REAL_APP or _REAL_APP in resolved.parents


def _unsafe_locations(environ):
    # Missing HOME means Path.home() falls back to the real account record.
    home = Path(environ.get('HOME') or str(_REAL_HOME)).resolve()
    raw = environ.get('OPENMONTAGE_GATES_DIR')
    if raw == '~' or (raw and raw.startswith('~/')):
        gates = home / raw[2:] if raw != '~' else home
    else:
        gates = Path(raw).expanduser() if raw else home / '.openmontage' / 'gates'
    return home == _REAL_HOME or any(_inside_real_app(p) for p in (home, gates, home / '.openmontage' / 'backlot'))


def _install_test_locations():
    root = Path(tempfile.mkdtemp(prefix='frontlot-tests-', dir=os.environ.get('TMPDIR')))
    home, gates = root / 'home', root / 'gates'
    home.mkdir()
    gates.mkdir()
    os.environ['FRONTLOT_TEST_RUN_ROOT'] = str(root)
    os.environ["HOME"] = str(home)
    os.environ["OPENMONTAGE_GATES_DIR"] = str(gates)
    # Browsers are already installed on the machine. Reuse their executable
    # cache while app data stays in the temporary home; never install anything.
    cache = _REAL_HOME / ('Library/Caches/ms-playwright' if sys.platform == 'darwin' else '.cache/ms-playwright')
    if not os.environ.get('PLAYWRIGHT_BROWSERS_PATH') and cache.is_dir():
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(cache)


def _refuse(message):
    _VIOLATIONS.add(message)
    raise RuntimeError(message)


def _audit_test_locations(event, args):
    if event == 'subprocess.Popen':
        _executable, argv, _cwd, environment = args
        # An invalid environment cannot spawn a process. Preserve the existing
        # application's TypeError handling for this negative launch test.
        if environment is not None and any(not isinstance(v, (str, bytes, os.PathLike)) for v in environment.values()):
            return
        words = argv if isinstance(argv, (list, tuple)) else [argv]
        command = ' '.join(str(word) for word in words)
        board_child = ('-m backlot' in command or any(name in command for name in
                       ('backlot/__main__.py', 'gate_sign.py', 'gate_approve.py', 'claude_session.py',
                        'scripts.gate_sign', 'scripts.gate_approve', 'scripts.claude_session')))
        python_child = 'python' in Path(str(_executable)).name.lower()
        # Pytest children install this same guard before collection; probes
        # deliberately start them with unsafe inputs to prove startup refusal.
        pytest_child = '-m pytest' in command
        if python_child and not board_child and not pytest_child and _unsafe_locations(os.environ if environment is None else environment):
            _refuse('A test tried to start a Python child with real app folders. Use a temporary home and signing folder.')
        if board_child and _unsafe_locations(os.environ if environment is None else environment):
            _refuse('A test tried to start the board with real app folders. Use a temporary home and signing folder.')
        return
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', 'localhost', '::1'):
            _refuse('A test tried to contact an outside service. Use a local fake service instead.')
        return
    events = {'open', 'os.mkdir', 'os.listdir', 'os.scandir', 'os.remove',
              'os.rmdir', 'os.rename', 'os.chmod', 'os.truncate', 'os.symlink'}
    if event not in events:
        return
    paths = args[:2] if event in {'os.rename', 'os.symlink'} else args[:1]
    for raw in paths:
        if isinstance(raw, (str, bytes, os.PathLike)) and _inside_real_app(os.fsdecode(raw)):
            _refuse('A test tried to use the real app folder. Use a temporary home and signing folder.')


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    _install_test_locations()
    if _unsafe_locations(os.environ):
        raise pytest.UsageError('Tests cannot start: the home or signing folder still reaches the real app folder. Use temporary folders for both.')
    sys.addaudithook(_audit_test_locations)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    # A broad app fallback must not hide an attempted access from pytest.
    if _VIOLATIONS:
        reporter = session.config.pluginmanager.get_plugin('terminalreporter')
        if reporter:
            for message in sorted(_VIOLATIONS):
                reporter.write_line(message, red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def _isolated_gates_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setenv('OPENMONTAGE_GATES_DIR', str(tmp_path_factory.mktemp('gates')))


@pytest.fixture(autouse=True)
def _isolated_writeros_library(tmp_path_factory, monkeypatch):
    monkeypatch.setenv('WRITEROS_LIBRARY', str(tmp_path_factory.mktemp('writeros-library')))
