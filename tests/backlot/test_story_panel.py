"""Source bytes, bounded file input and writer authority at the real routes."""
import base64,hashlib,json
import pytest
from fastapi.testclient import TestClient
from tests.lib.test_story_ingest import root,TEXT

@pytest.fixture
def client(root,monkeypatch):
    import backlot.server as server
    monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    with TestClient(server.create_app(port=4937,capability_token='invented-story-writer')) as c:
        c.headers.update({'origin':'http://127.0.0.1:4937','x-frontlot-capability':'invented-story-writer'})
        yield c

def url(root):return f'/api/project/{root.name}/story'
def file_body(raw=TEXT,name='Invented story.txt'):
    return {'file':{'name':name,'base64':base64.b64encode(raw).decode()},'protected':[],'spoilers':[]}

def test_chosen_text_is_copied_byte_for_byte_and_shown_without_approval(root,client):
    value=client.post(url(root),json=file_body())
    assert value.status_code==200,value.text
    result=value.json();assert (root/result['path']).read_bytes()==TEXT
    assert result['sha256']==hashlib.sha256(TEXT).hexdigest()
    state=client.get(url(root)).json()
    assert state['current']['text']==TEXT.decode() and not state['approved']
    assert state['current']['byte_length']==len(TEXT)
    assert client.post(url(root)+'/apply',json={}).status_code==400
    assert client.post(url(root)+'/check',json={}).status_code==400

@pytest.mark.parametrize('body',[
    {'path':'/outside/invented.txt'},file_body(b'\x89PNG\0'),file_body(name='picture.png'),
    file_body(name='../outside.txt'),file_body(b'a'*131073),{'text':''},{'text':'  \n\t'},
    {'text':'Good story.','protected':['Words that are not here.']},
    {'file':{'name':'story.txt','base64':'!not-base64!'}},
])
def test_refusals_are_plain_and_create_no_story_record(root,client,body):
    response=client.post(url(root),json=body)
    assert response.status_code==400,response.text
    assert isinstance(response.json()['detail'],str) and response.json()['detail']
    assert not (root/'checkpoint_canon_ingest.json').exists()
    assert not (root/'cost_log.json').exists()

def test_source_route_requires_writer_capability_and_origin(root,client):
    client.headers.pop('x-frontlot-capability')
    assert client.post(url(root),json={'text':'Invented story.'}).status_code==401
    client.headers['x-frontlot-capability']='invented-story-writer';client.headers['origin']='https://outside.invalid'
    assert client.post(url(root),json={'text':'Invented story.'}).status_code==403
    assert not (root/'checkpoint_canon_ingest.json').exists()

def test_failed_replacement_keeps_previous_source_checkpoint_and_signature(root,client,monkeypatch):
    from tests.lib.look_lock_helpers import approve_request
    made=client.post(url(root),json=file_body()).json();approve_request(made['gate'],root)
    before=(root/'checkpoint_canon_ingest.json').read_bytes();approvals=(root/'approvals.jsonl').read_bytes()
    import lib.checkpoint as cp
    def interrupted(*a,**kw):raise OSError('Invented interrupted save.')
    monkeypatch.setattr(cp,'atomic_write_bytes',interrupted)
    response=client.post(url(root),json=file_body(TEXT+b'A new sentence.'))
    assert response.status_code==400
    assert (root/'checkpoint_canon_ingest.json').read_bytes()==before
    assert (root/'approvals.jsonl').read_bytes()==approvals
    assert (root/made['path']).read_bytes()==TEXT
