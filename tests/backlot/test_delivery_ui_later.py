"""File feedback expires on an independent read; history stays open."""
import copy,os
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http
from tests.backlot.test_delivery_actions import snapshot

@pytest.fixture
def screen(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];outside=[];calls=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            if r.request.url==url+'/delivery-ui-proof':r.fulfill(body='<html><body></body></html>',content_type='text/html')
            elif r.request.url.startswith(url+'/'):r.continue_()
            else:outside.append(r.request.url);r.abort()
        page.route('**/*',route);page.goto(url+'/delivery-ui-proof')
        state=snapshot('Signed by you','Needs your signature',colour=None);old=copy.deepcopy(state['exports'][0]);old.update(id='earlier-file',status='Approved by you');state['exports'].append(old)
        page.evaluate('''async state=>{
          const style=document.createElement('link');style.rel='stylesheet';style.href='/ui/board.css';document.head.append(style);
          const m=await import('/ui/delivery.js');window.module=m;window.saved=state;window.refuse=false;window.posts=[];
          window.fetch=async(url,options)=>{
            if(options?.method==='POST'){
              window.posts.push(String(url));
              if(window.refuse)return new Response(JSON.stringify({detail:'This file needs a fresh look.'}),{status:400});
              return new Response(JSON.stringify({gate_request_id:'invented-gate',message:'Your final-file signature for this file is recorded.',path:'/private/invented/movie.mp4',delivery:window.saved}));
            }
            return new Response(JSON.stringify(window.saved));
          };
          const panel=m.renderDelivery({project_id:'invented-ui'},async()=>{
            panel.hidden=true;
            const review=m.renderDeliverySigning({request_id:'invented-gate',request:{stage:'compose'},packet:{}},[],async()=>{
              review.remove();panel.hidden=false;await m.refreshDelivery();
            });document.body.append(review);
          });document.body.append(panel);
        }''',state)
        panel=page.get_by_role('region',name='Trailer delivery');expect(panel.get_by_role('status')).to_contain_text('Current final file: Needs your signature.')
        try:yield page,panel
        finally:
            assert posts==[] and gets==[] and outside==[] and errors==[]
            browser.close()

def capture(page,name):
    folder=os.environ.get('DELIVERY_UI_CAPTURE')
    if folder:page.screenshot(path=str(Path(folder)/(name+'.png')),full_page=True)

@pytest.mark.parametrize('identity',['invented-final','earlier-file'])
def test_return_without_signing_clears_rechecking(screen,identity):
    page,panel=screen;main=panel.locator(f'[data-delivery="{identity}"]')
    if identity=='earlier-file':
        page.evaluate('()=>{window.saved.exports[1].status="Needs your signature";window.module.renderDelivery({project_id:"invented-ui"},()=>{});}')
        panel.get_by_text('Earlier exports',exact=True).click()
    main.get_by_role('button',name='Review and sign final approval',exact=True).click()
    assert page.evaluate('()=>window.posts.at(-1)').endswith('/'+identity+'/approval')
    page.get_by_role('button',name='Return to delivery',exact=True).click()
    expect(main.get_by_role('note')).to_be_empty()
    expect(panel.get_by_role('status')).to_contain_text('Current final file: Needs your signature.')
    capture(page,'returned-without-signing')

def test_refresh_clears_card_result_and_keeps_earlier_exports_open(screen):
    page,panel=screen;fold=panel.locator('details').filter(has=page.get_by_text('Earlier exports',exact=True))
    fold.locator('summary').first.click();old=panel.locator('[data-delivery="earlier-file"]')
    old.get_by_role('button',name='Record the signed final approval',exact=True).click()
    expect(old.get_by_role('note')).to_contain_text('Your final-file signature for this file is recorded.')
    expect(panel.locator(':scope > [role="note"]')).to_be_empty()
    expect(panel.get_by_role('status')).to_contain_text('Current final file: Needs your signature.')
    panel.get_by_role('button',name='Refresh delivery',exact=True).click()
    expect(fold).to_have_attribute('open','')
    expect(old.get_by_role('note')).to_be_empty()
    page.evaluate('()=>{window.saved.exports[1].status="Needs a fresh look";window.module.renderDelivery({project_id:"invented-ui"},()=>{});}')
    expect(old).to_contain_text('Needs a fresh look')
    expect(fold).to_have_attribute('open','')
    capture(page,'refreshed-earlier-exports-open')

def test_independent_unchanged_read_clears_refusal_on_its_own_card(screen):
    page,panel=screen;panel.get_by_text('Earlier exports',exact=True).click();old=panel.locator('[data-delivery="earlier-file"]')
    page.evaluate('()=>window.refuse=true')
    old.get_by_role('button',name='Record the signed final approval',exact=True).click()
    expect(old.get_by_role('note')).to_have_text('This is an earlier file. This file needs a fresh look.')
    page.evaluate('()=>window.module.renderDelivery({project_id:"invented-ui"},()=>{})')
    expect(old.get_by_role('note')).to_be_empty()
    expect(panel.locator(':scope > [role="note"]')).to_be_empty()
    expect(panel.get_by_role('status')).to_contain_text('Current final file: Needs your signature.')
