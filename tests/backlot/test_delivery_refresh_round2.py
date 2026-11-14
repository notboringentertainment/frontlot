"""A signing return waits for the newest response, including a queued refresh."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign


def test_overlapping_returns_wait_for_verified_server_state_twenty_times(signed_cut,monkeypatch):
    root,provider,posts,*_=signed_cut
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            def route(r):
                if r.request.url==url+'/delivery-refresh-harness':r.fulfill(body='<html><body></body></html>',content_type='text/html')
                elif r.request.url.startswith(url+'/'):r.continue_()
                else:r.abort()
            page.route('**/*',route)
            for iteration in range(20):
                page.goto(url+'/delivery-refresh-harness')
                page.evaluate('''async (iteration)=>{
                    const m=await import('/ui/delivery.js');
                    window.refreshDefinition=m.refreshDelivery.toString();
                    const stale={number:1,revision:'invented-saved-version',rough_status:'Needs your signature',exports:[]};
                    const latest={...stale,rough_status:'Signed by you'};
                    let count=0;window.reads=[];window.words=[];
                    window.fetch=async()=>{
                        count++;
                        window.reads.push(count);
                        if(count===1)return new Response(JSON.stringify(stale));
                        if(count===2)return await new Promise(resolve=>{window.releaseOld=()=>resolve(new Response(JSON.stringify(stale)));});
                        return new Response(JSON.stringify(latest));
                    };
                    document.body.append(m.renderDelivery({project_id:'invented-return-'+iteration},()=>{}));
                    new MutationObserver(()=>window.words.push(document.querySelector('[role=status]').textContent)).observe(document.querySelector('[role=status]'),{childList:true,subtree:true});
                    window.module=m;
                }''',iteration)
                expect(page.get_by_role('status')).to_contain_text('Rough cut: Needs your signature.')
                page.evaluate('''()=>{
                    document.querySelector('button:last-of-type').click();
                    window.returned=false;
                    window.returnPromise=Promise.resolve(window.module.refreshDelivery()).then(()=>{window.returned=true;});
                }''')
                page.wait_for_function('()=>!!window.releaseOld')
                # This is the exact contract used by Return to delivery. It
                # must not resolve while the pre-signing answer is in flight.
                assert page.evaluate('()=>window.returned') is False
                page.evaluate('async()=>{window.releaseOld();await window.returnPromise;}')
                expect(page.get_by_role('region',name='Trailer delivery')).to_contain_text('Signed by you')
                expect(page.get_by_role('button',name='Make the final file',exact=True)).to_be_enabled()
                try:expect(page.get_by_role('status')).to_contain_text('Rough cut: Signed by you.')
                except AssertionError as e:raise AssertionError(repr((iteration,page.evaluate('()=>({reads:window.reads,words:window.words,definition:window.refreshDefinition})')))) from e
                assert page.evaluate('()=>window.returned') is True
            assert len(provider['posts'])==3 and len(posts)==1 and not errors
        finally:browser.close()
