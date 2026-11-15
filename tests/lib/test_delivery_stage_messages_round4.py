"""Delivery completion messages name the signature's subject in either outcome."""
from pathlib import Path
import pytest

@pytest.mark.parametrize('unfinished',[False,True])
def test_rough_cut_signature_message_names_the_rough_cut(monkeypatch,unfinished):
    from lib import trailer_delivery as d,checkpoint
    monkeypatch.setattr(d,'require_rough',lambda *a:({'invented':'saved cut'},{}))
    monkeypatch.setattr(d,'_pin',lambda *a:None)
    monkeypatch.setattr(d.contract,'require_stage_approval',lambda *a:None)
    def checkpoint_result(*a,**kw):
        if unfinished:raise checkpoint.CheckpointValidationError('Invented earlier review still unfinished.')
    monkeypatch.setattr(checkpoint,'write_checkpoint',checkpoint_result)
    message=d.complete_stage(Path('/invented-trailer'),'edit')
    assert message.startswith('Your rough-cut signature is recorded.')
    assert ('unfinished' in message)==unfinished

@pytest.mark.parametrize('unfinished',[False,True])
def test_final_file_signature_message_names_this_file(monkeypatch,unfinished):
    from lib import trailer_delivery as d,checkpoint
    monkeypatch.setattr(d,'_artifacts',lambda *a,**kw:{'invented':'final file'})
    monkeypatch.setattr(d,'_pin',lambda *a:None)
    monkeypatch.setattr(d.contract,'require_stage_approval',lambda *a:None)
    def checkpoint_result(*a,**kw):
        if unfinished:raise checkpoint.CheckpointValidationError('Invented earlier review still unfinished.')
    monkeypatch.setattr(checkpoint,'write_checkpoint',checkpoint_result)
    message=d.complete_stage(Path('/invented-trailer'),'compose','invented-export')
    assert message.startswith('Your final-file signature for this file is recorded.')
    assert ('unfinished' in message)==unfinished
