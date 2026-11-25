"""The newest final file alone determines Delivery's approval summary."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign

def test_fresh_delivery_summary_describes_only_the_main_file(signed_cut,monkeypatch):
    """An older signature must not describe a newer, unsigned final file."""
    from lib import trailer_delivery as d,cutting_view as cut
    from tests.lib.test_delivery_export import approve_rough
    root,provider,posts,*_=signed_cut
    from lib import trailer_edit
    saved=trailer_edit.read_revision(root)
    value=cut.editable(saved['timeline']);value['audio']=value['audio'][:1]
    cut.save_cut(root,value,saved['edit_revision_sha256'])
    saved=approve_rough(root)
    first=d.export(root,saved['edit_revision_sha256'])
    looks.approve_request(d.request_final(root,first['id']),root)
    assert d.final_state(root,first['id'])=='Approved by you'
    second=d.export(root,saved['edit_revision_sha256'])
    assert d.final_state(root,second['id'])=='Needs your signature'
    assert d.final_state(root,first['id'])=='Approved by you'
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            def open_delivery(status):
                # Leave the document first: navigating to its hash again can
                # keep the old page alive instead of opening Delivery fresh.
                page.goto('about:blank')
                page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
                panel=page.get_by_role('region',name='Trailer delivery',exact=True)
                main=panel.locator('[data-delivery]').first
                expect(main).to_have_attribute('data-delivery',second['id'],timeout=60000)
                expect(main).to_contain_text(status)
                expect(panel.get_by_role('status')).to_contain_text('Current final file: '+status+'.',timeout=5000)
                return main
            open_delivery('Needs your signature')
            looks.approve_request(d.request_final(root,second['id']),root)
            open_delivery('Approved by you')
            value=cut.editable(saved['timeline']);value['picture'][0]['colour']['brightness']=20
            cut.save_cut(root,value,saved['edit_revision_sha256'])
            open_delivery('Needs a fresh look')
            assert len(provider['posts'])==3 and len(posts)==1 and not errors
        finally:browser.close()

