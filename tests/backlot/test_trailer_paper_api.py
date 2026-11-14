import json
from fastapi.testclient import TestClient
from backlot import server
from tests.lib.test_trailer_edit import paper
from tests.lib.look_lock_helpers import approve_request
import pytest


@pytest.fixture
def app(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'PROJECTS_DIR',tmp_path)
    monkeypatch.setattr(server,'_PROJECTS_ROOT_STR',str(tmp_path.resolve()))
    from scripts import gate_approve
    monkeypatch.setattr(gate_approve,'require_tty',lambda:None)
    return server.create_app(port=4783,capability_token='invented-proof')


def test_start_save_auth_and_no_stage_approval(app,tmp_path):
    c=TestClient(app)
    body={'title':'Night Window','seconds':4,'ceiling':25}
    assert c.post('/api/trailers',json=body).status_code==403
    headers={'origin':'http://localhost:4783','x-frontlot-capability':'invented-proof'}
    r=c.post('/api/trailers',headers=headers,json=body)
    assert r.status_code==200
    slug=r.json()['project_id'];root=tmp_path/slug
    req=json.loads(next((root/'.gate-requests').glob('*.json')).read_text())
    assert c.post(f'/api/project/{slug}/paper-cut',headers=headers,json={'paper':paper(),'expected_revision':None}).status_code==400
    approve_request(req,root)
    result=c.post(f'/api/project/{slug}/paper-cut',headers=headers,json={'paper':paper(),'expected_revision':None})
    assert result.status_code==200
    assert result.json()['unfinished']
    assert not list(root.glob('checkpoint*'))
    assert c.post(f'/api/project/{slug}/paper-cut',headers=headers,json={'paper':paper(),'expected_revision':None}).status_code==400


@pytest.mark.parametrize('seconds',[0,-2,121,'bad',1.01])
def test_plain_length_refusals(app,seconds):
    c=TestClient(app)
    response=c.post('/api/trailers',headers={'origin':'http://localhost:4783','x-frontlot-capability':'invented-proof'},json={'title':'Night Window','seconds':seconds,'ceiling':25})
    assert response.status_code==400
    assert 'whole frames' in response.json()['detail']
