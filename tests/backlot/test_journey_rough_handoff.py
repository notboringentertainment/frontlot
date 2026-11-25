import json
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign


def test_rough_review_reads_the_saved_cut_after_a_stale_panel_answer(signed_cut,monkeypatch):
    from lib import trailer_edit,cutting_view,trailer_delivery
    root,calls,posts,*_=signed_cut
    old=trailer_delivery.snapshot(root);fresh=[False];requests=[]
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            endpoint=url+f'/api/project/{root.name}/delivery'
            page.route(endpoint,lambda r:r.continue_() if fresh[0] else r.fulfill(status=200,content_type='application/json',body=json.dumps(old)))
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button',name='Delivery workspace',exact=True).click()
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            expect(panel).to_contain_text(f'Saved version {old["number"]}.',timeout=30000)
            saved=trailer_edit.read_revision(root);timeline=cutting_view.editable(saved['timeline'])
            timeline['picture'][0]['colour']['brightness']=15
            changed=cutting_view.save_cut(root,timeline,saved['edit_revision_sha256'])
            # Hold the panel on a genuine older answer. The click must start
            # its own fresh read; file-watcher timing must not rescue it.
            expect(panel).to_contain_text(f'Saved version {old["number"]}.')
            page.on('request',lambda r:requests.append((r.method,r.url,r.post_data_json if r.method=='POST' else None)) if r.url.startswith(endpoint) else None)
            fresh[0]=True
            with page.expect_response(lambda r:r.url==endpoint+'/rough' and r.request.method=='POST',timeout=120000) as response:
                panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True).evaluate('(b)=>b.click()')
            assert response.value.status==200,response.value.text()
            expect(page.get_by_role('region',name='Delivery review',exact=True)).to_be_visible(timeout=30000)
            posted=next(row for row in requests if row[0]=='POST' and row[1].endswith('/rough'))
            assert posted[2]['revision']==changed['edit_revision_sha256']
            assert any(row[0]=='GET' and row[1]==endpoint for row in requests[:requests.index(posted)])
            assert not errors and len(calls['posts'])==3 and len(posts)==1
        finally:browser.close()
