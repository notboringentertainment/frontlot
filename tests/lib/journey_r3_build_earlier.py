"""Opt-in producer run inside the exact earlier source archive, never current code."""
import json,os
from pathlib import Path
import pytest
from tests.backlot.test_delivery_browser import delivery_cut
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign

@pytest.mark.parametrize('slot',['compare','resolution','save','browser'])
def test_build_earlier_trailer(delivery_cut,slot):
    from lib import gates,trailer_edit
    from tests.lib.test_delivery_export import approve_rough
    root,calls,posts=delivery_cut
    approve_rough(root)
    saved=trailer_edit.read_revision(root)
    assert saved['inputs']['checkpoint_script.json']=='ABSENT'
    assert len(calls['posts'])==3 and len(posts)==1
    path=Path(os.environ['JOURNEY_R3_FIXTURES'])/(slot+'.json')
    with path.open('x') as f:json.dump({'root':str(root),'gates':str(gates.gates_dir()),'producer':'56ad1a9'},f)
