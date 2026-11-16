"""Every final file owns its approval; a later signature retires the earlier one."""
import pytest
from tests.backlot.test_delivery_browser import delivery_cut
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.lib.test_delivery_export import approve_rough
from tests.title_renderer import require_title_renderer


@pytest.mark.parametrize('revise',[False,True],ids=['same-cut','revised-cut'])
def test_an_approved_export_does_not_lock_out_the_next_file(delivery_cut,monkeypatch,revise):
    from lib import trailer_delivery as d,cutting_view as cut
    root,p,audio=delivery_cut;saved=approve_rough(root)
    first=d.export(root,saved['edit_revision_sha256']);looks.approve_request(d.request_final(root,first['id']),root)
    assert d.final_state(root,first['id'])=='Approved by you'
    if revise:
        value=cut.editable(saved['timeline']);value['picture'][0]['colour']['brightness']=20
        cut.save_cut(root,value,saved['edit_revision_sha256']);saved=approve_rough(root)
        assert d.final_state(root,first['id'])=='Needs a fresh look'
    second=d.export(root,saved['edit_revision_sha256'])
    assert d.final_state(root,second['id'])=='Needs your signature'
    states={r['id']:r['status'] for r in d.snapshot(root)['exports']}
    assert states[second['id']]=='Needs your signature'
    assert d.playback(root,second['id']).is_file()
    reveals=[]
    with monkeypatch.context() as patch:
        original=d.programs.run
        def run(args,*a,**kw):
            if args[:2]==['open','-R']:reveals.append(args);return None
            return original(args,*a,**kw)
        patch.setattr(d.programs,'run',run)
        with pytest.raises(ValueError,match='Watch and sign final approval'):d.reveal(root,second['id'])
    assert reveals==[]
    looks.approve_request(d.request_final(root,second['id']),root)
    assert d.final_state(root,second['id'])=='Approved by you'
    assert d.final_state(root,first['id'])=='Needs a fresh look'
    with pytest.raises(ValueError,match='Watch and sign final approval'):d.reveal(root,first['id'])
    checked=[];original=d._file_artifacts
    def checked_file(root,identity,*args,**kw):
        checked.append(identity)
        return original(root,identity,*args,**kw)
    monkeypatch.setattr(d,'_file_artifacts',checked_file)
    snapshot=d.snapshot(root)
    assert {r['id']:r['status'] for r in snapshot['exports']}=={
        first['id']:'Needs a fresh look',second['id']:'Approved by you'}
    assert checked==[second['id']], 'A superseded export cannot become current and must not delay the current file’s verified state.'
    assert len(p['posts'])==3 and len(audio)==1


def test_signing_checks_clear_only_their_own_intermediate_sound(delivery_cut):
    from lib import trailer_delivery as d
    from lib.pathsafe import sha256_file
    root,p,audio=delivery_cut;saved=approve_rough(root);item=d.export(root,saved['edit_revision_sha256'])
    job=root/'artifacts/deliveries'/item['id'];movie=job/'trailer.mp4';fingerprint=sha256_file(movie)
    unrelated=job/'check-not-owned';unrelated.mkdir();(unrelated/'mix.wav').write_bytes(b'Unknown earlier work.')
    request=d.request_final(root,item['id']);looks.approve_request(request,root)
    checks=[folder for folder in job.glob('check-*') if folder!=unrelated]
    assert len(checks)>=3
    assert all(not list(folder.rglob('*.wav')) for folder in checks)
    assert all((folder/'measurement.json').is_file() for folder in checks)
    assert (unrelated/'mix.wav').read_bytes()==b'Unknown earlier work.'
    assert sha256_file(movie)==fingerprint
    assert d.final_state(root,item['id'])=='Approved by you'
    assert len(p['posts'])==3 and len(audio)==1
