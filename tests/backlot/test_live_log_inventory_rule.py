"""The owner's live-log exception excludes permissions and every other file."""
import copy
import pytest
from tests.followup_safety import allowed_difference

BASE={'.':{'mode':16832},'backlot/server.log':{'mode':33152,'size':4,'mtime_ns':1,'sha256':'a'},
      'backlot/board.token':{'mode':33152,'size':4,'mtime_ns':1,'sha256':'a'}}

def test_live_log_change_requires_the_explicit_exception():
    after=copy.deepcopy(BASE);after['backlot/server.log'].update(size=8,mtime_ns=2,sha256='b')
    assert not allowed_difference(BASE,after)
    assert allowed_difference(BASE,after,allow_server_log=True)

@pytest.mark.parametrize('fault',['add','remove','root-permission','log-permission','token','extra-log-field'])
def test_live_log_exception_refuses_every_other_change(fault):
    after=copy.deepcopy(BASE)
    if fault=='add':after['another']={}
    elif fault=='remove':after.pop('backlot/board.token')
    elif fault=='root-permission':after['.']['mode']=16877
    elif fault=='log-permission':after['backlot/server.log']['mode']=33188
    elif fault=='extra-log-field':after['backlot/server.log']['owner']='different'
    else:after['backlot/board.token']['sha256']='b'
    assert not allowed_difference(BASE,after,allow_server_log=True)
