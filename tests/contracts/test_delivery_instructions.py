"""The assistant describes only the delivery controls the board provides."""
from pathlib import Path
import pytest

@pytest.mark.parametrize('name',['AGENT_GUIDE.md','skills/pipelines/authored-film/edit-director.md','skills/pipelines/authored-film/compose-director.md'])
def test_trailer_delivery_instructions_name_the_existing_signed_controls(name):
    text=Path(name).read_text()
    for label in ('Review and sign the saved rough cut','Make the final file','Review and sign final approval','Show approved file in Finder'):
        assert label in text
    assert 'Earlier stage reviews' in text
    assert 'Whole-stage signing controls are not available yet' not in text
    assert 'The delivery piece will supply the visible exact-file signing controls' not in text
