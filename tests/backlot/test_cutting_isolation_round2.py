import asyncio
from pathlib import Path
import pytest
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http

def test_board_accepts_tmp_alias_without_any_private_redirect(voice_world,monkeypatch):
    from tests.backlot.test_voice_panel_browser import board
    from lib import gates
    root,*_=voice_world
    signing=gates.gates_dir()
    alias=Path(str(signing).replace('/private/tmp/','/tmp/',1))
    monkeypatch.setattr(gates,'gates_dir',lambda:alias)
    with board(root,monkeypatch):pass

def test_slow_broker_hello_is_unavailable_not_an_unhandled_timeout(tmp_path,monkeypatch):
    from backlot import tty
    import os,socket,tempfile
    # macOS Unix sockets have a short pathname limit. Keep this inside the
    # redirected app home without adding pytest's long per-test directory.
    socket_path=Path(tempfile.mkdtemp(prefix='hello-',dir=os.environ.get('OPENMONTAGE_HOME') or tempfile.gettempdir()))/'pending.sock'
    sock=socket.socket(socket.AF_UNIX);sock.bind(str(socket_path))
    class Writer:
        closed=False
        def close(self):self.closed=True
        async def wait_closed(self):pass
    writer=Writer()
    async def connect(*a):return object(),writer
    async def timed_read(*a):raise asyncio.TimeoutError()
    monkeypatch.setattr(tty,'session_paths',lambda p:{'socket':socket_path})
    monkeypatch.setattr(asyncio,'open_unix_connection',connect)
    monkeypatch.setattr(tty,'read_frame',timed_read)
    try:
        with pytest.raises(tty.BrokerUnavailable,match='ready'):
            asyncio.run(tty._attach('invented','request'))
        assert writer.closed
    finally:sock.close()

@pytest.mark.parametrize('temporary',['/tmp','/private/tmp','/var/folders/t7/invented/T'])
def test_resolved_locations_accept_default_named_and_mac_temporary_roots(tmp_path,monkeypatch,temporary):
    from tests.backlot.test_voice_panel_browser import assert_board_locations
    import tempfile,os
    run=Path(os.environ['FRONTLOT_TEST_RUN_ROOT']);monkeypatch.setattr(tempfile,'gettempdir',lambda:temporary)
    assert_board_locations(run/'home',Path(temporary)/'signing',run/'home/metadata')
