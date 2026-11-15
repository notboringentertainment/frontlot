"""The upload and the paid queue choose the same invented connection."""

def test_upload_ignores_blank_primary_connection_before_using_the_alternative(tmp_path,monkeypatch):
    import requests
    from tools.video import _shared
    monkeypatch.setenv('FAL_KEY',' \t ');monkeypatch.setenv('FAL_AI_API_KEY',' invented-fallback ')
    path=tmp_path/'invented-reference.png';path.write_bytes(b'Invented reference bytes')
    calls=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'upload_url':'https://simulation.invalid/upload','file_url':'https://simulation.invalid/reference.png'}
    def post(url,**kw):
        assert kw['headers']['Authorization']=='Key invented-fallback'
        calls.append('initiate');return Response()
    def put(url,**kw):
        assert url=='https://simulation.invalid/upload' and kw['data']==path.read_bytes()
        calls.append('upload');return Response()
    monkeypatch.setattr(requests,'post',post);monkeypatch.setattr(requests,'put',put)
    assert _shared.upload_image_fal(str(path))=='https://simulation.invalid/reference.png'
    assert calls==['initiate','upload']
