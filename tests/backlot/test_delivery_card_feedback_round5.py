"""An earlier export's actions report beside that export, never the main file."""
import os
from pathlib import Path
import subprocess
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign


def test_earlier_approved_file_keeps_record_and_finder_feedback_on_its_card(signed_cut,monkeypatch):
    from lib import trailer_delivery as d,cutting_view as cut,trailer_edit
    from tests.lib.test_delivery_export import approve_rough
    root,provider,posts,*_=signed_cut
    saved=trailer_edit.read_revision(root)
    value=cut.editable(saved['timeline']);value['audio']=value['audio'][:1]
    cut.save_cut(root,value,saved['edit_revision_sha256']);saved=approve_rough(root)
    first=d.export(root,saved['edit_revision_sha256'])
    looks.approve_request(d.request_final(root,first['id']),root)
    second=d.export(root,saved['edit_revision_sha256'])
    assert d.final_state(root,first['id'])=='Approved by you'
    assert d.final_state(root,second['id'])=='Needs your signature'
    revealed=[];original=subprocess.run
    def run(args,*a,**kw):
        if args[:2]==['open','-R']:
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
            main=panel.locator('[data-delivery]').first
            older=panel.locator(f'[data-delivery="{first["id"]}"]')
            expect(main).to_have_attribute('data-delivery',second['id'],timeout=60000)
            panel.get_by_text('Earlier exports',exact=True).click()
            endpoint=url+f'/api/project/{root.name}/delivery';failures=[]
            for action,suffix,words in [
                ('Record the signed final approval','apply','Your final-file signature for this file is recorded.'),
                ('Show approved file in Finder','reveal','This approved file has been shown in Finder.')]:
                with page.expect_response(lambda r:r.url==endpoint and r.request.method=='GET',timeout=60000) as refreshed:
                    with page.expect_response(lambda r:r.url==endpoint+'/'+first['id']+'/'+suffix and r.request.method=='POST',timeout=60000) as result:
                        older.get_by_role('button',name=action,exact=True).click()
                    assert result.value.ok,result.value.text()
                assert refreshed.value.ok;refreshed.value.json()
                expect(main).to_contain_text('Needs your signature')
                expect(panel.get_by_role('status')).to_contain_text('Current final file: Needs your signature.')
                # Check both actions on the old screen before reporting its
                # failures, so one misplaced result cannot hide the other.
                try:
                    expect(older.get_by_role('note')).to_contain_text(words,timeout=5000)
                    expect(older.get_by_role('note')).to_contain_text('This is an earlier file.')
                    expect(older.get_by_role('note')).not_to_contain_text(str(root))
                    expect(main.get_by_role('note')).to_be_empty()
                    expect(panel.locator(':scope > [role="note"]')).to_be_empty()
                except AssertionError as error:failures.append(suffix+': '+str(error).split('Aria snapshot:')[0])
                capture=os.environ.get('DELIVERY_CARD_CAPTURE')
                if capture:
                    older.get_by_role('note').scroll_into_view_if_needed()
                    path=Path(capture)/('earlier-'+suffix+'.png');assert not path.exists()
                    page.screenshot(path=str(path))
            assert len(revealed)==1
            assert revealed[0][2]==str(d.playback(root,first['id']))
            assert len(provider['posts'])==3 and len(posts)==1 and not errors
            if os.environ.get('DELIVERY_CARD_CAPTURE'):
                panel.evaluate('(e)=>e.scrollIntoView({block:"start",behavior:"instant"})')
                path=Path(os.environ['DELIVERY_CARD_CAPTURE'])/'main-after-earlier-actions.png';assert not path.exists()
                page.screenshot(path=str(path))
            assert not failures,'\n\n'.join(failures)
        finally:browser.close()
