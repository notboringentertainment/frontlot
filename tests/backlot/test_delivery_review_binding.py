"""Rough-cut signing must show the saved finish, never a later comparison."""
import json
import pytest
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign

@pytest.mark.parametrize('case',['later-before','only-before','changed-settings'])
def test_rough_review_requires_the_saved_finished_settings(signed_cut,case):
    from lib import trailer_delivery as d,trailer_edit,cutting_view as cut
    from backlot.delivery import signing_packet
    root,provider,posts,*_=signed_cut
    saved=trailer_edit.read_revision(root);timeline=cut.editable(saved['timeline'])
    timeline['picture'][0]['colour']['brightness']=25
    cut.save_cut(root,timeline,saved['edit_revision_sha256'])
    request=d.request_rough(root,trailer_edit.read_revision(root)['edit_revision_sha256'])
    revision=trailer_edit.read_revision(root)['edit_revision_sha256']
    if case!='only-before':finished=cut.render_preview(root,revision,'after')
    if case!='changed-settings':comparison=cut.render_preview(root,revision,'before')
    else:
        path=finished.with_suffix('.review.json');review=json.loads(path.read_text())
        review['settings_sha256']='f'*64;path.write_text(json.dumps(review))
    if case=='later-before':assert comparison.stat().st_mtime_ns>finished.stat().st_mtime_ns
    packet=signing_packet(root,request)
    if case=='later-before':assert packet['play_url'].endswith('/'+finished.name),packet
    else:
        assert packet['packet_error'] is True,packet
        assert 'saved colour and sound settings' in packet['error'],packet
    assert len(provider['posts'])==3 and len(posts)==1
