"""A generic synthetic import becomes a reference in one signed location shot."""
import json
from pathlib import Path
import pytest
from tests.tools.test_signed_footage import film,provider,workspace


def test_existing_import_is_bound_to_location_by_the_signed_footage_request(film,provider,monkeypatch,tmp_path):
    from PIL import Image
    from tests.lib import look_lock_helpers as looks
    from lib import reference_import as imports,supervised_production as shots,trailer_edit as edit
    from backlot.footage_controls import create
    root,shot_id=film
    location=looks.location_look('invented-empty-room')
    looks.activate_look(root,location)
    original=tmp_path/'Invented machine room.png'
    Image.new('RGB',(1024,576),(47,65,83)).save(original)
    raw=original.read_bytes()
    staged=imports.stage_reference_upload(root,original)
    pending=imports.prepare_reference_import(root,root.name,staged,
        origin_class='imported_synthetic',origin_tool='Invented local picture tool')
    request=json.loads(pending.request_path.read_text())
    receipt=looks.approve_request(request,root)
    imported=imports.finalize_reference_import(root,receipt['receipt_id'])
    assert original.read_bytes()==raw
    assert imports.verify_lineage(root,imported.normalized_pixel_hash)
    current=edit.read_revision(root)
    paper=current['paper_cut']
    paper['shots'][0].update(purpose='An empty invented room waits in cold light.',
        references=[imported.path.relative_to(root).as_posix()])
    edit.save_revision(root,paper,current['edit_revision_sha256'])
    result=create(root,{'shot_id':shot_id,'model':workspace().MODEL,'duration_seconds':5,
        'looks':{imported.path.relative_to(root).as_posix():'location:invented-empty-room'}})
    identity=result['gate']['entity_id']
    record=workspace()._read(root,identity)
    assert record['look_refs']==[{'entity_kind':'location','entity_id':'invented-empty-room',
        'look_hash':looks.look_refs_for(location)[0]['look_hash']}]
    assert record['references'][0]['asset_id']==imported.normalized_pixel_hash
    assert record['references'][0]['visual_bible_entity_id']=='invented-empty-room'
    with pytest.raises(ValueError,match='signed'):workspace().execute(root,identity)
    assert all(not v for v in provider.values())
    looks.approve_request(result['gate'],root)
    take=workspace().execute(root,identity)
    assert take['references']==record['references']
    assert all(len(v)==1 for v in provider.values())
    workspace().execute(root,identity)
    assert all(len(v)==1 for v in provider.values())
    assert not (root/'checkpoint_assets.json').exists()
