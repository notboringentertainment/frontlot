"""The first-location portion of the visible empty-production rehearsal."""
from playwright.sync_api import expect
from tests.backlot.test_early_paid_readiness import shot_picture
from tests.backlot.test_footage_controls_browser import sign
from tests.lib.test_frontlot_location_source import TEXT

def simple_sign(page,back):
    page.get_by_role('button',name='Start signing',exact=True).click()
    page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
    expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded.',timeout=20000)
    page.get_by_role('button',name=back,exact=True).click()

def complete_footage_prerequisites(page,root,calls,tmp_path):
    from PIL import Image
    original=tmp_path/'Invented machine gallery.png';Image.new('RGB',(1024,576),(49,67,83)).save(original);raw=original.read_bytes()
    page.get_by_role('button',name='Footage setup',exact=True).click()
    setup=page.get_by_role('region',name='Footage prerequisites',exact=True)
    expect(setup).to_contain_text('Location looks only')
    look=setup.get_by_role('group',name='Location written in Front Lot',exact=True)
    for label,value in [('Location name','Invented empty gallery'),('Exact location description',TEXT),
        ('Establishing view','Wide view along the empty gallery towards its doorway.'),('Default time of day','morning'),
        ('Palette anchors, one per line','cold grey\npale blue\ndark brown'),('Architecture or terrain','Stone walls and a long uninterrupted floor.'),
        ('Dressing, one item per line','Three empty display cases.'),('Weather and light rules','Soft skylight remains steady.'),
        ('Continuity risks, one per line','The positions of the cases stay the same.'),('Things to avoid, one per line','No people.')]:
        field=look.get_by_label(label,exact=True)
        if label=='Exact location description':
            from tests.backlot.test_story_round2 import paste
            paste(field,value)
        else:field.fill(value)
    look.get_by_label('This is an invented location, not a person or character.').check()
    look.get_by_role('button',name='Prepare location look for signing',exact=True).click()
    review=page.get_by_role('region',name='Location source approval',exact=True)
    expect(review).to_contain_text('Written in Front Lot')
    assert review.locator('.exact-location').text_content()==TEXT
    expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_enabled(timeout=20000)
    shot_picture(page,'location-signing',review.get_by_role('heading').first)
    assert not calls['video_posts'] and len(calls['audio_posts'])==3
    simple_sign(page,'Return to footage setup')
    expect(setup.get_by_role('status').first).to_contain_text('signed',timeout=15000)
    picture=setup.get_by_role('group',name='Machine-generated reference picture',exact=True)
    picture.get_by_label('Choose a picture file',exact=True).set_input_files(str(original))
    picture.get_by_label('Tool that made this picture',exact=True).fill('Invented local picture tool')
    picture.get_by_label('This picture is machine-generated.').check()
    picture.get_by_label('This picture depicts no real person.').check()
    picture.get_by_role('button',name='Prepare picture origin for signing',exact=True).click()
    review=page.get_by_role('region',name='Picture origin approval',exact=True)
    expect(review).to_contain_text('machine-generated, depicts no real person')
    expect(review.locator('img')).to_have_js_property('naturalWidth',960,timeout=15000)
    expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_enabled(timeout=20000)
    shot_picture(page,'picture-origin-signing',review.get_by_role('heading').first)
    simple_sign(page,'Return to footage setup')
    setup.get_by_role('button',name='Use signed picture for selected shot',exact=True).click()
    expect(setup.get_by_role('status').first).to_contain_text('picture',timeout=15000)
    assert original.read_bytes()==raw
    page.get_by_role('button',name='Open footage requests',exact=True).click()
    shot=page.get_by_role('article',name='Shot 1 footage',exact=True)
    expect(shot).to_contain_text('No footage price figures have been signed',timeout=15000)
    expect(shot.get_by_role('button',name='Create footage request',exact=True)).to_be_disabled()
    shot_picture(page,'missing-footage-prices-refused',shot)
    page.get_by_role('button',name='Footage setup',exact=True).click()
    price=setup.get_by_role('group',name='Fal footage prices and permissions',exact=True)
    assert price.get_by_label('Highest cash dollars per second',exact=True).input_value()==''
    for label,value in [('Highest cash dollars per second','0.112'),('Highest credit replacement dollars per second','0'),('Footage prices expire','2099-01-01T12:00')]:price.get_by_label(label,exact=True).fill(value)
    price.get_by_label('Allow separately approved prompts to be sent to Fal.').check()
    price.get_by_label('Allow separately approved reference pictures to be sent to Fal.').check()
    price.get_by_role('button',name='Prepare footage price settings for signing',exact=True).click()
    review=page.get_by_role('region',name='Price settings approval',exact=True)
    expect(review).to_contain_text('ElevenLabs');expect(review).to_contain_text('Fal');expect(review).to_contain_text('$10.0000')
    expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_enabled(timeout=20000)
    shot_picture(page,'prices-signing',review.get_by_role('heading').first)
    simple_sign(page,'Return to footage setup')
    page.get_by_role('button',name='Open footage requests',exact=True).click()
    shot=page.get_by_role('article',name='Shot 1 footage',exact=True)
    expect(shot.get_by_role('button',name='Create footage request',exact=True)).to_be_enabled(timeout=15000)
    with page.expect_response(lambda r:r.url.endswith('/footage/create'),timeout=30000) as created:
        shot.get_by_role('button',name='Create footage request',exact=True).click()
    assert created.value.status==200,created.value.text()
    review=page.get_by_label('Footage request details',exact=True)
    expect(review).to_contain_text('$0.5600',timeout=20000);shot_picture(page,'clip-purchase-signing',review.get_by_role('heading').first)
    assert not calls['video_posts']
    sign(page,'request')
    tile=shot.locator('.take-tile');expect(tile).to_be_visible(timeout=20000)
    video=tile.locator('video');expect(video).to_have_js_property('readyState',4,timeout=15000)
    video.evaluate('(v)=>{v.muted=true;return v.play()}');expect(video).to_have_js_property('ended',True,timeout=15000)
    tile.get_by_role('button',name='Keep this take',exact=True).click();sign(page,'decision')
    expect(shot.locator('.footage-status')).to_have_text('Kept',timeout=20000);shot_picture(page,'clip-kept',tile)
    assert len(calls['video_posts'])==1 and all(len(calls[k])==1 for k in ('uploads','waits','downloads'))
    import json
    assert json.loads((root/'checkpoint_look_lock.json').read_text())['status']=='in_progress'
    assert not (root/'checkpoint_assets.json').exists()
    from lib import trailer_edit
    saved=trailer_edit.read_revision(root)
    assert saved['timeline']['picture'][0]['kind']=='footage' and saved['timeline']['picture'][1]['kind']=='placeholder'
    assert original.read_bytes()==raw and not page.errors
    page.get_by_role('button',name='Footage setup',exact=True).click()
    look=setup.get_by_role('group',name='Location written in Front Lot',exact=True)
    look.get_by_label('Exact location description',exact=True).fill(TEXT+' The doorway now stays dark.')
    look.get_by_role('button',name='Prepare location look for signing',exact=True).click()
    page.get_by_role('button',name='Return to footage setup',exact=True).click()
    expect(shot).to_contain_text('Needs a fresh look',timeout=20000)
    shot_picture(page,'changed-look-needs-review',shot)
    assert len(calls['video_posts'])==1 and len(calls['audio_posts'])==3 and not page.errors
