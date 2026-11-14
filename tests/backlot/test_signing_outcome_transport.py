"""A late resize must not discard the signer's already queued outcome."""
import asyncio
import pytest
from tests.backlot.signing_outcome_helpers import (
    project,priced,http,voice_world,film,provider,signing_folder,held)


def test_broken_input_pipe_still_delivers_queued_approval():
    from backlot import tty
    async def check():
        reader=asyncio.StreamReader()
        reader.feed_data(tty.encode_json_frame(tty.STATUS,{'tag':{'outcome':'approved'}}));reader.feed_eof()
        incoming=asyncio.Queue();incoming.put_nowait(('resize',{'cols':80,'rows':24}))
        sent=[]
        class Writer:
            def write(self,value):pass
            async def drain(self):raise ConnectionResetError('The signer has already exited.')
        class Page:
            async def send_json(self,value):await asyncio.sleep(0);sent.append(value)
            async def send_bytes(self,value):await asyncio.sleep(0)
        await tty._relay(Page(),reader,Writer(),incoming)
        assert sent==[{'type':'status','tag':{'outcome':'approved'}}]
    asyncio.run(check())


@pytest.mark.parametrize('medium',['footage','audio'])
def test_busy_signing_constructor_reports_a_refusal_instead_of_crashing(request,medium,monkeypatch):
    from lib.media_spending import call_lock,request_resolution
    from scripts.gate_approve import construct,GateHandlerError
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count()
    gate=request_resolution(root,reservation['reservation_id'],False,0,0,'I checked the invented account.')
    with call_lock(root):
        with pytest.raises(GateHandlerError,match='operation|running|finish'):
            construct(root,gate)
    assert count()==before
