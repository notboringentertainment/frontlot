"""Delivery's main file and summary agree throughout the same visit."""
import os
from pathlib import Path
import subprocess
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign

@pytest.mark.parametrize('case',['new-export','reveal-then-change'])
def test_summary_follows_main_file_after_actions_without_reopening(signed_cut,monkeypatch,case):
    from lib import trailer_delivery as d,cutting_view as cut,trailer_edit
    from tests.lib.test_delivery_export import approve_rough
    root,provider,posts,*_=signed_cut
    saved=trailer_edit.read_revision(root)
    value=cut.editable(saved['timeline']);value['audio']=value['audio'][:1]
    cut.save_cut(root,value,saved['edit_revision_sha256'])
    saved=approve_rough(root)
    first=d.export(root,saved['edit_revision_sha256'])
    looks.approve_request(d.request_final(root,first['id']),root)
    assert d.final_state(root,first['id'])=='Approved by you'
    revealed=[];original=subprocess.run
    def run(args,*a,**kw):
        if Path(args[0]).name=='open' and args[1]=='-R':
            revealed.append(args);return subprocess.CompletedProcess(args,0)
        return original(args,*a,**kw)
    monkeypatch.setattr(subprocess,'run',run)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            status=panel.get_by_role('status');main=panel.locator('[data-delivery]').first
            expect(status).to_contain_text('Current final file: Approved by you.',timeout=60000)
            endpoint=url+f'/api/project/{root.name}/delivery'
            if case=='new-export':
                with page.expect_response(lambda r:r.url==endpoint+'/export' and r.request.method=='POST',timeout=180000) as exported:
                    panel.get_by_role('button',name='Make the final file',exact=True).click()
                response=exported.value;assert response.ok,response.text()
                result=response.json()
                expect(main).to_have_attribute('data-delivery',result['id'],timeout=60000)
                expected='Needs your signature'
            else:
                with page.expect_response(lambda r:r.url.endswith('/reveal') and r.request.method=='POST',timeout=60000) as response:
                    main.get_by_role('button',name='Show approved file in Finder',exact=True).click()
                assert response.value.ok
                assert len(revealed)==1
                value=cut.editable(saved['timeline']);value['picture'][0]['colour']['brightness']=20
                cut.save_cut(root,value,saved['edit_revision_sha256'])
                with page.expect_response(lambda r:r.url==endpoint and r.request.method=='GET',timeout=60000):
                    panel.get_by_role('button',name='Refresh delivery',exact=True).click()
                expected='Needs a fresh look'
            expect(main).to_contain_text(expected,timeout=60000)
            capture=os.environ.get('DELIVERY_VISIT_CAPTURE')
            if capture:
                panel.evaluate('(e)=>e.scrollIntoView({block:"start",behavior:"instant"})')
                target=Path(capture)/(case+'.png');assert not target.exists()
                page.screenshot(path=str(target))
            expect(status).to_contain_text('Current final file: '+expected+'.',timeout=5000)
            expect(status).not_to_contain_text('approved trailer is saved')
            expect(panel.locator(':scope > [role="note"]')).not_to_contain_text('approved trailer is saved')
            if case=='new-export':
                assert 'rough-cut signature' in result['stage_message'].lower()
            else:
                expect(main.get_by_role('button',name='Show approved file in Finder',exact=True)).to_be_disabled()
                expect(main.get_by_role('note')).to_be_empty()
            # Another explicit refresh must keep the same truthful summary.
            with page.expect_response(lambda r:r.url==endpoint and r.request.method=='GET',timeout=60000):
                panel.get_by_role('button',name='Refresh delivery',exact=True).click()
            expect(status).to_contain_text('Current final file: '+expected+'.')
            assert len(provider['posts'])==3 and len(posts)==1 and not errors
        finally:browser.close()
