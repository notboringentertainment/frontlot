"""Only an existing regular server log's data and write time may differ."""
import copy
import stat
import pytest
from tests import isolation_inventory as audit


@pytest.mark.parametrize('case,expected',[
    ('contents',True),('size',True),('time',True),('all-log-data',True),
    ('another-file',False),('added-entry',False),('removed-entry',False),
    ('added-log',False),('removed-log',False),('permissions',False),
    ('link',False),('directory',False),('unknown-field',False),
])
def test_live_log_exception_is_exact(case,expected):
    regular={'mode':stat.S_IFREG|0o600,'mtime_ns':1,'size':20,'value':'old-content-hash'}
    before={'backlot/server.log':regular.copy(),'gates/receipt':regular.copy()}
    after=copy.deepcopy(before)
    if case in ('contents','all-log-data'):after['backlot/server.log']['value']='new-content-hash'
    if case in ('size','all-log-data'):after['backlot/server.log']['size']=30
    if case in ('time','all-log-data'):after['backlot/server.log']['mtime_ns']=2
    if case=='another-file':after['gates/receipt']['value']='changed'
    if case=='added-entry':after['backlot/new-entry']=regular.copy()
    if case=='removed-entry':after.pop('gates/receipt')
    if case=='added-log':before.pop('backlot/server.log')
    if case=='removed-log':after.pop('backlot/server.log')
    if case=='permissions':after['backlot/server.log']['mode']=stat.S_IFREG|0o644
    if case=='link':after['backlot/server.log']['mode']=stat.S_IFLNK|0o600
    if case=='directory':before['backlot/server.log']['mode']=after['backlot/server.log']['mode']=stat.S_IFDIR|0o600
    if case=='unknown-field':after['backlot/server.log']['new-detail']=True
    assert audit.expected_live_log_change(before,after) is expected


def test_log_plus_another_change_still_stops():
    regular={'mode':stat.S_IFREG|0o600,'mtime_ns':1,'size':20,'value':'old'}
    before={'backlot/server.log':regular.copy(),'gates/receipt':regular.copy()}
    after=copy.deepcopy(before)
    after['backlot/server.log']['value']='new'
    after['gates/receipt']['mtime_ns']=2
    assert not audit.expected_live_log_change(before,after)
