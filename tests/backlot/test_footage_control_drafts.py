"""Quote refresh keeps the writer's unsaved account-review entries."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def test_quote_refresh_preserves_account_review_draft():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            def respond(route):
                path=route.request.url.split('127.0.0.1:4777',1)[1]
                if path.startswith('/ui/'):
                    route.fulfill(content_type='text/javascript',body=(Path('backlot')/path.lstrip('/')).read_text())
                elif path.endswith('/quote'):
                    route.fulfill(content_type='application/json',body=json.dumps({'highest_usd':.5,'max_cash_usd':.5,
                        'max_credits_usd':0,'remaining_usd':49.5,'price_expires_at':4102444800}))
                else:route.fulfill(content_type='text/html',body='<main></main>')
            page.route('http://127.0.0.1:4777/**',respond)
            page.goto('http://127.0.0.1:4777/')
            page.evaluate('''async()=>{
              const ui=await import('/ui/footage.js');
              const state={models:[{id:'invented',label:'Invented model'}],looks:[],remaining_usd:49.5,story_ready:true,
                shots:[{id:'invented-shot',label:'Shot 1',purpose:'An invented doorway.',duration_seconds:5,
                  references:[],takes:[],requests:[{id:'invented-request',approved:true,
                    reservation:{state:'pending_billing',reservation_id:'invented-reservation'}}],status:'Charge needs your review'}]};
              window.draws=0;
              function render(){document.querySelector('main').replaceChildren(ui.renderFootageControls({project_id:'invented'}));window.draws++;}
              ui.setFootageState('invented',state,{render,refresh:async()=>render(),lookCard:()=>null,isRefreshing:()=>true});
              render();
            }''')
            page.get_by_text('Highest charge: $0.5000',exact=False).wait_for()
            note=page.get_by_label('What you checked in the service account')
            note.fill('The invented account confirms this charge.')
            page.get_by_label('The service charged this request',exact=True).check()
            page.get_by_label('Cash charged in dollars').fill('0.4')
            page.get_by_label('Credit replacement value in dollars').fill('0.1')
            count=page.evaluate('window.draws')
            page.get_by_role('button',name='Refresh price',exact=True).click()
            page.wait_for_function('(count)=>window.draws>count',arg=count)
            assert note.input_value()=='The invented account confirms this charge.'
            assert page.get_by_label('The service charged this request',exact=True).is_checked()
            assert page.get_by_label('Cash charged in dollars').input_value()=='0.4'
            assert page.get_by_label('Credit replacement value in dollars').input_value()=='0.1'
            assert not errors
        finally:browser.close()
