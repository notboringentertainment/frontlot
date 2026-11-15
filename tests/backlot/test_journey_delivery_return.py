"""The signing return prioritises its verified record over other workspaces."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign


def test_delivery_return_checks_itself_before_refreshing_other_workspaces(signed_cut,monkeypatch):
    from lib import cutting_view
    root,calls,posts,*_=signed_cut
    monkeypatch.setattr(cutting_view,'render_preview',lambda *args,**kwargs:root/'assets/not-prepared.mp4')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];held=[];other=[];delivery_reads=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.add_init_script('''(()=>{const original=window.fetch;window.deliveryReadOrigins=[];window.fetch=function(...args){if(String(args[0]).endsWith('/delivery'))window.deliveryReadOrigins.push(new Error().stack);return original.apply(this,args);};})();''')
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(url+'/') else route.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            panel=page.get_by_role('region',name='Trailer delivery',exact=True)
            panel.get_by_role('button',name='Review and sign the saved rough cut',exact=True).click(timeout=30000)
            review=page.get_by_role('region',name='Delivery review',exact=True)
            expect(review).to_be_visible(timeout=30000)
            base=url+f'/api/project/{root.name}'
            page.route(base+'/delivery',lambda route:held.append(route))
            def capture(request):
                if request.method=='GET' and request.url==base+'/delivery':delivery_reads.append(request.url)
                elif request.method=='GET' and any(request.url.startswith(base+part) for part in ('/voice','/cut','/footage')):other.append(request.url)
            page.on('request',capture)
            review.get_by_role('button',name='Return to delivery',exact=True).click()
            expect(panel.get_by_role('status')).to_have_text('Checking the saved cut and its approvals…')
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            assert len(held)==1 and len(delivery_reads)==1
            assert other==[], 'The current signature must not queue behind unrelated workspace reads.'
            expect(panel.get_by_role('button',name='Record the signed rough-cut approval',exact=True)).to_be_disabled()
            held[0].continue_()
            expect(panel.get_by_role('status')).to_contain_text('Saved cut loaded.',timeout=30000)
            expect(page.get_by_role('button',name='Paper cut workspace',exact=True)).to_be_enabled()
            expect(page.get_by_role('region',name='Cutting view',exact=True)).to_be_attached()
            assert len(delivery_reads)==1, page.evaluate('()=>window.deliveryReadOrigins')
            assert not errors and len(calls['posts'])==3 and len(posts)==1
        finally:browser.close()
