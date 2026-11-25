"""A private signing check never clears uncertain or signed material."""
import pytest
from tests.lib.test_cutting_view import media,cut
from tests.tools.test_signed_footage import film,provider,looks,workspace
from tests.lib.test_preview_cleanup_round3 import world,approve_preview

@pytest.mark.parametrize('fault',['consumed-approval','signed-stem','unknown-file'])
def test_check_cleanup_is_bounded_and_refuses_uncertain_history(media,monkeypatch,fault):
    from lib import cut_preview_files as cleanup,gates
    root,prior,target,mix,refs=world(media);parts=mix.parent/(mix.stem+'-parts')
    contacts=[];monkeypatch.setattr('requests.post',lambda *a,**k:contacts.append(1))
    if fault=='consumed-approval':
        with pytest.raises(OSError,match='Invented stop'):approve_preview(root,prior,monkeypatch,interrupt=True)
        before={str(p):p.read_bytes() for p in [mix,*parts.iterdir()]}
        with pytest.raises(ValueError,match='Nothing was cleared'):cleanup.clear_measurement_parts(root,mix,refs)
        assert gates.wal_entries()
        assert all(__import__('pathlib').Path(p).read_bytes()==v for p,v in before.items())
    elif fault=='signed-stem':
        approve_preview(root,mix,monkeypatch)
        cleanup.clear_measurement_parts(root,mix,refs)
        assert mix.exists() and list(parts.iterdir())
    else:
        unknown=parts/'an-unrelated-file.wav';unknown.write_bytes(b'Not created by this check.')
        cleanup.clear_measurement_parts(root,mix,refs)
        assert unknown.read_bytes()==b'Not created by this check.' and mix.exists()
    assert contacts==[]
