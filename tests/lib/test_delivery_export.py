"""Actual final bytes, interrupted publication and gate-side rechecks."""
import copy,json
import pytest
from tests.backlot.test_delivery_browser import delivery_cut
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.title_renderer import require_title_renderer

def approve_rough(root):
    from lib import trailer_delivery as d,trailer_edit
    gate=d.request_rough(root,trailer_edit.read_revision(root)['edit_revision_sha256']);looks.approve_request(gate,root)
    return trailer_edit.read_revision(root)

def test_real_export_measurements_exact_final_signature_and_reveal_refusal(delivery_cut,monkeypatch):
    from lib import trailer_delivery as d,trailer_edit
    from lib.pathsafe import sha256_file
    root,provider,posts=delivery_cut;saved=approve_rough(root)
    original=(root/'artifacts/edit_decisions.json').read_bytes()
    take_bytes={r['path']:sha256_file(root/r['path']) for r in saved['timeline']['picture']+saved['timeline']['audio']}
    item=d.export(root,saved['edit_revision_sha256']);identity=item['id'];report=item['render_report'];review=item['final_review']
    assert review['status']=='pass',review['issues_found']
    assert review['metadata']['measurements']['on_target']
    assert review['metadata']['measurements']['speech_checks'] and all(r['status']=='clear' for r in review['metadata']['measurements']['speech_checks'])
    assert review['metadata']['measurements']['effect_checks'][0]['observed_frame']==36
    assert set(d._boundaries(saved['timeline']))=={r['frame'] for r in review['metadata']['boundary_samples']}
    assert report['outputs'][0]['resolution']=='1920x1080'
    assert report['export_sha256']==sha256_file(d.playback(root,identity))
    from PIL import Image
    import io,numpy as np
    from lib.trailer_titles import card_png
    for card in saved['timeline']['cards']:
        row=next(r for r in review['metadata']['boundary_samples'] if r['frame']==card['start_frame'])
        actual=np.array(Image.open(root/row['path']).convert('RGB')).astype(float)
        lettering=Image.open(io.BytesIO(card_png(card,resolution=(1920,1080))))
        expected=Image.new('RGBA',(1920,1080),'black');expected.alpha_composite(lettering)
        expected=np.array(expected.convert('RGB')).astype(float)
        assert np.abs(actual-expected).mean()<4
        mask=expected.mean(axis=2)>128
        assert (actual.mean(axis=2)[mask]>120).mean()>.98
    with pytest.raises(ValueError,match='Watch and sign final approval'):d.reveal(root,identity)
    gate=d.request_final(root,identity)
    from scripts import gate_approve as ga
    shown=ga.construct(root,gate);looks.approve_request(gate,root)
    assert d.final_state(root,identity)=='Approved by you'
    assert (root/'artifacts/edit_decisions.json').read_bytes()==original
    assert all(sha256_file(root/p)==digest for p,digest in take_bytes.items())
    with (root/report['outputs'][0]['path']).open('ab') as f:f.write(b'invented change')
    assert d.final_state(root,identity)=='Needs a fresh look'
    with pytest.raises(ValueError,match='fresh look'):d.request_final(root,identity)
    with pytest.raises(ValueError,match='fresh look'):shown.pre_commit_check()
    assert len(provider['posts'])==3 and len(posts)==1

def test_interrupted_export_never_publishes_and_preserves_saved_cut(signed_cut,monkeypatch):
    from lib import trailer_delivery as d
    root,provider,posts,*_=signed_cut;saved=approve_rough(root);before=(root/'artifacts/edit_decisions.json').read_bytes()
    def interrupted(root,revision,directory):
        (directory/'unfinished.mp4').write_bytes(b'not a finished movie')
        raise OSError('An invented interruption.')
    monkeypatch.setattr(d,'render_final',interrupted)
    with pytest.raises(OSError,match='invented interruption'):d.export(root,saved['edit_revision_sha256'])
    assert not list((root/'artifacts/deliveries').glob('*/complete.json'))
    assert not list((root/'artifacts/deliveries').glob('*/trailer.mp4'))
    assert (root/'artifacts/edit_decisions.json').read_bytes()==before
    assert d.snapshot(root)['exports']==[] and len(provider['posts'])==3 and len(posts)==1

def test_export_cannot_be_approved_as_a_later_cut(delivery_cut):
    from lib import trailer_delivery as d,trailer_edit,cutting_view as cut
    root,provider,posts=delivery_cut;saved=approve_rough(root);item=d.export(root,saved['edit_revision_sha256'])
    changed=cut.editable(saved['timeline']);changed['picture'][0]['colour']['contrast']=15
    cut.save_cut(root,changed,saved['edit_revision_sha256'])
    assert d.final_state(root,item['id'])=='Needs a fresh look'
    with pytest.raises(ValueError,match='rough cut'):d.request_final(root,item['id'])
    assert len(provider['posts'])==3 and len(posts)==1

def test_editable_export_report_cannot_relabel_an_old_file_as_a_new_cut(delivery_cut):
    from lib import trailer_delivery as d,trailer_edit,cutting_view as cut
    root,provider,posts=delivery_cut;saved=approve_rough(root);item=d.export(root,saved['edit_revision_sha256'])
    changed=cut.editable(saved['timeline']);changed['picture'][0]['colour']['contrast']=30
    cut.save_cut(root,changed,saved['edit_revision_sha256']);new=approve_rough(root)
    item['render_report']['edit_revision_sha256']=new['edit_revision_sha256']
    item['final_review']['edit_revision_sha256']=new['edit_revision_sha256']
    (root/'artifacts/deliveries'/item['id']/'complete.json').write_text(json.dumps(item))
    with pytest.raises(ValueError,match='render record'):d.request_final(root,item['id'])
    assert len(provider['posts'])==3 and len(posts)==1

def test_consistently_tampered_measurements_are_remeasured_at_signing(delivery_cut,monkeypatch):
    from lib import trailer_delivery as d
    root,provider,posts=delivery_cut;saved=approve_rough(root);original=d._measure
    def wrong_measurement(*args):
        probe,measured=original(*args);measured['integrated_lufs']=-16.5
        return probe,measured
    # Simulate an earlier faulty check with an otherwise genuine local
    # render receipt. Signing must measure independently, not trust it.
    with monkeypatch.context() as patch:
        patch.setattr(d,'_measure',wrong_measurement);item=d.export(root,saved['edit_revision_sha256'])
    with pytest.raises(ValueError,match='measurements no longer match'):d.request_final(root,item['id'])
    assert len(provider['posts'])==3 and len(posts)==1

def test_decline_during_export_refuses_publication(delivery_cut,monkeypatch):
    from lib import trailer_delivery as d,footage_requests as f
    root,provider,posts=delivery_cut;saved=approve_rough(root);original=d.render_final
    def decline(*args):
        result=original(*args);row=saved['timeline']['picture'][0]
        looks.approve_request(f.request_decision(root,row['shot_id'],row['take_id'],'decline'),root)
        return result
    monkeypatch.setattr(d,'render_final',decline)
    with pytest.raises(ValueError,match='rough cut'):d.export(root,saved['edit_revision_sha256'])
    assert not list((root/'artifacts/deliveries').glob('*/complete.json'))
    assert len(provider['posts'])==3 and len(posts)==1
