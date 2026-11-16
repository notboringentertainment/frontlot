"""Six presentation checks for the six findings in the writer's walk.

These render individual components against invented responses. They start no
board or signer and permit no external requests or purchases.
"""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect

REPO = Path(__file__).resolve().parents[2]
UI = REPO / 'backlot/ui'


def function(name):
    source = (UI / 'board.js').read_text()
    start = source.index('function ' + name + '(')
    return source[start:source.index('\n}', start) + 2]


@pytest.fixture
def screen():
    outside, errors = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(timezone_id='America/Los_Angeles')
        def route(r):
            url = r.request.url
            if url == 'http://walk.invalid/':
                r.fulfill(body='<!doctype html><html><body></body></html>', content_type='text/html')
            elif url.startswith('http://walk.invalid/ui/') and '?' not in url:
                path = UI / url.rsplit('/', 1)[1]
                assert path.is_file()
                r.fulfill(body=path.read_text(), content_type='text/javascript')
            elif url == 'http://walk.invalid/api/writer-status':
                r.fulfill(json={'writer': True})
            elif url == 'http://walk.invalid/api/project/invented/delivery/final/play':
                r.fulfill(status=204, content_type='video/mp4', body='')
            else:
                outside.append(url)
                r.abort()
        page.route('**/*', route)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://walk.invalid/')
        yield page
        browser.close()
    assert outside == []
    assert errors == []


def test_1_next_step_skips_finished_setup_and_follows_ready_shots(screen):
    result = screen.evaluate("""async()=>{
      const m=await import('/ui/next_step.js');
      const state={project_id:'invented',gates:{requests:[]},stages:[
        {name:'canon_ingest',status:'completed'},{name:'script',status:'completed'},
        {name:'edit',status:'not_started'}],paper_cut:{usable_downstream:true,
        current:{paper_cut:{shots:[{}],lines:[]}}}};
      const setup={approved_looks:[{}],pictures:[{}],price_settings_signed:true,footage_prices:{}};
      const shots=[{id:'one',label:'Shot 1',purpose:'Open the empty gallery',references:['picture.png'],requests:[],takes:[]},
        {id:'two',label:'Shot 2',purpose:'Hold the closing invitation',references:[],requests:[],takes:[]}];
      const node=m.renderNextStep(state,()=>{});document.body.append(node);
      m.setAudioProgress('invented',{price_settings_signed:true,takes:[],requests:[]});
      m.setFootageProgress('invented',{shots});m.setFootageSetupProgress('invented',setup);
      const ready=node.textContent;
      shots[0].takes.push({status:'Accepted'});m.setFootageProgress('invented',{shots});
      const cutting=node.textContent;
      state.paper_cut.current.cutting_view=true;state.paper_cut.current.timeline={picture:[{}]};
      m.renderNextStep(state,()=>{});const delivery=node.textContent;
      m.setFootageSetupProgress('invented',{...setup,footage_prices:null});const priceOnly=node.textContent;
      return {ready,cutting,delivery,priceOnly};
    }""")
    assert 'Open Shot 1 in Footage' in result['ready']
    for key in ('ready', 'cutting', 'delivery'):
        assert 'Shot 2: Hold the closing invitation. No reference picture' in result[key]
        assert 'Open Footage setup' not in result[key]
    assert 'Open Cutting workspace' in result['cutting']
    assert 'Open Delivery workspace' in result['delivery']
    assert 'sign current footage price settings' in result['priceOnly']
    assert 'review and sign the location look' not in result['priceOnly']
    assert 'import and sign its reference picture' not in result['priceOnly']


def test_2_recorded_setup_signatures_do_not_report_pending_read_errors(screen):
    result = screen.evaluate("""async()=>{
      const {el}=await import('/ui/lib.js');
      const {renderPrereqSigning}=await import('/ui/footage_setup.js');
      const {renderPriceSigning}=await import('/ui/media_prices.js');
      const result=[];
      for(const state of ['pending','done','declined']){
        const detail={state,request:{kind:'look_lock'},packet:{error:'This source could not be read. Prepare again.'}};
        const location=renderPrereqSigning(detail,el('button',{},'Sign'),()=>{},()=>{},'invented',state!=='pending');
        const price=renderPriceSigning(detail,el('button',{},'Sign'),()=>{},state!=='pending');
        result.push({state,location:location.textContent,price:price.textContent});
      }return result;
    }""")
    for row in result:
        for kind in ('location', 'price'):
            if row['state'] == 'done':
                assert 'Your signature is recorded.' in row[kind]
                assert 'could not be read' not in row[kind]
            else:
                assert 'could not be read' in row[kind]
                assert 'Your signature is recorded.' not in row[kind]


def test_3_prepare_feedback_finishes_when_signing_detaches_the_setup(screen):
    screen.evaluate("""async()=>{
      const {renderFootageSetup}=await import('/ui/footage_setup.js');
      window.prepares=[];window.returned=0;
      const snapshot={message:'Ready for your next step.',approved_looks:[{name:'Invented gallery',ref:{}}],
        shots:[{id:'one',purpose:'Open gallery'}],pictures:[],existing_sources:[],pending:[]};
      window.fetch=async(url,options={})=>{
        if(options.method==='POST'){prepares.push(url);return new Response(JSON.stringify({gate:{request_id:'invented'},request_id:'invented'}));}
        return new Response(JSON.stringify(snapshot));
      };
      window.setup=renderFootageSetup({project_id:'invented'},async()=>{
        setup.remove();await Promise.resolve();returned++;
      });document.body.append(setup);
    }""")
    screen.get_by_label('Exact location description', exact=True).fill('An invented empty gallery.')
    screen.get_by_label('Highest cash dollars per second', exact=True).fill('0.1')
    screen.get_by_label('Highest credit replacement dollars per second', exact=True).fill('0.1')
    screen.get_by_label('Footage prices expire', exact=True).fill('2030-01-01T12:00')
    for index, label in enumerate(('Prepare location look for signing', 'Prepare picture origin for signing',
                                    'Prepare footage price settings for signing'), 1):
        screen.get_by_role('button', name=label, exact=True).click()
        screen.wait_for_function('(n)=>returned===n && !setup.querySelector("fieldset").disabled', arg=index)
        assert screen.evaluate('setup.textContent.includes("Working…")') is False
        screen.evaluate('document.body.append(setup)')
        expect(screen.locator('.action-feedback', has_text='Working…')).to_have_count(0)
    assert screen.evaluate('prepares.map(url=>url.split("/").at(-1))') == ['look', 'picture', 'prices']


def test_4_creation_review_explains_the_five_reviews_without_changing_contract(screen):
    screen.evaluate("""async source=>{
      const {el}=await import('/ui/lib.js');
      const selectedGateId='creation',gateDetailState='ready';
      const row={kind:'pipeline_migration',state:'pending'};
      const decision={required_reviews:['Story','Paper cut','Rough cut','Colour and sound','Final file'],
        dropped_whole_stage_reviews:['Whole-stage asset production','Whole-stage storyboard']};
      const before=JSON.stringify(decision),gateDetail={state:'pending',packet:{trailer_test_contract:decision}};
      const gateRowById=()=>row,gateActions=()=>el('button',{},'Start signing');
      const render=eval('('+source+')');document.body.append(render({trailer_setup_pending:true}));
      window.contractUnchanged=before===JSON.stringify(decision);
    }""", function('renderGateViewer'))
    words = screen.locator('body').inner_text()
    assert 'five signed reviews: Story, Paper cut, Rough cut, Colour and sound, and Final file' in words
    assert 'Other whole-stage reviews are not part of a trailer' in words
    assert 'Ben decided' not in words and 'set aside for this test' not in words
    assert 'It approves no stage, purchase or take.' in words
    assert screen.evaluate('contractUnchanged')


def test_5_signed_location_keeps_its_name_and_does_not_take_over_the_workbench(screen, tmp_path, monkeypatch):
    from backlot.state import _looks_summary
    from lib import frontlot_location_source
    entry = {'entity_id': 'trailer-location', 'entity_kind': 'location', 'look_hash': 'invented',
             'writer_source_ref': {'project_id': tmp_path.name, 'path': 'invented-source', 'sha256': 'invented'}}
    (tmp_path / 'checkpoint_look_lock.json').write_text(json.dumps({'artifacts': {'look_packet': {'looks': [entry]}}}))
    reads = []
    def read(root, ref):
        assert root == tmp_path and ref == entry['writer_source_ref']
        reads.append(ref)
        return {'name': 'Invented empty gallery'}
    monkeypatch.setattr(frontlot_location_source, 'read', read)
    looks = _looks_summary(tmp_path)
    source = (UI / 'board.js').read_text()
    selection = source[source.index('  const roster = authoredGates ?'):source.index('  const shot = !selectedGateId')]
    result = screen.evaluate("""async ({rosterSource,viewerSource,selection,looks})=>{
      const {el}=await import('/ui/lib.js');
      const titleCase=s=>s,FRAME_ROLES=[],entityKeyOf=()=>null,slotOfRow=()=>null;
      let selectedFrame=0;
      const slotsOf=()=>['look'],tape=()=>el('span'),SLOT_LABEL={look:'Look'};
      const renderScreen=(s,f,blank)=>blank,renderFrameStrip=()=>null,historyList=()=>null;
      const buildRoster=eval('('+rosterSource+')'),renderEntityViewer=eval('('+viewerSource+')');
      const s={is_trailer:true,gates:{looks,requests:[]}},e=buildRoster(s)[0];
      document.body.append(renderEntityViewer(s,e));
      const shotsOf=()=>[],pendingGates=()=>[],shotKey=()=>'',authoredGates=true;
      let selectedEntity=null,selectedGateId=null,trailerWorkbenchChosen=true;
      eval(selection);const afterReturn=selectedEntity;
      selectedEntity=e.key;eval(selection);
      return {afterReturn,afterExplicitChoice:selectedEntity,name:e.name};
    }""", {'rosterSource': function('buildRoster'), 'viewerSource': function('renderEntityViewer'),
           'selection': selection, 'looks': looks})
    assert reads and result == {'afterReturn': None, 'afterExplicitChoice': 'location:trailer-location',
                                'name': 'Invented empty gallery'}
    expect(screen.get_by_role('heading', name='Invented empty gallery')).to_be_visible()
    expect(screen.locator('body')).to_contain_text('The location look is signed.')
    expect(screen.locator('body')).not_to_contain_text('Use Footage setup to review and sign')
    expect(screen.locator('body')).not_to_contain_text('Nothing on film yet')


def test_6_wording_reference_roundtrip_local_expiry_and_delivery_update_together(screen, tmp_path, monkeypatch):
    from backlot.state import _render_footage
    from lib import footage_requests
    record = {'prompt': 'Empty gallery', 'duration_seconds': 5, 'max_cash_usd': .1, 'max_credits_usd': .2,
              'remaining_usd': 9.7, 'price_expires_at': 1893513600, 'references': [], 'look_material': []}
    monkeypatch.setattr(footage_requests, '_read', lambda *_: record)
    monkeypatch.setattr(footage_requests, 'check_current', lambda *_, **__: None)
    packet = _render_footage(tmp_path, {'kind': 'footage_spend', 'status': 'done', 'entity_id': 'invented'})
    screen.evaluate(r"""async ({packet,source})=>{
      const {el,spendingCard}=await import('/ui/lib.js');
      const {renderPaperCut}=await import('/ui/trailer.js');
      const state={project_id:'invented',trailer:{target_duration_seconds:1},stages:[{name:'canon_ingest',status:'completed'}],
        paper_cut:{current:{edit_revision_sha256:'one',paper_cut:{beats:[],lines:[],cards:[],shots:[
          {id:'one',purpose:'Open gallery',duration_frames:24,references:['assets/opaque-picture.png'],difficulty:'Not assessed'}]}}}};
      window.saved=null;window.paperState=state;
      window.fetch=async(url,options={})=>{
        if(options.method==='POST'){saved=JSON.parse(options.body);return new Response(JSON.stringify({...state.paper_cut.current,paper_cut:saved.paper,timing_message:'Matches target.'}));}
        return new Response(JSON.stringify(state.paper_cut));
      };
      document.body.append(renderPaperCut(state));
      const {setFootageState,renderFootageControls}=await import('/ui/footage.js');
      setFootageState('invented',{shots:[]},{takeFeedback:()=>[],render:()=>{}});
      document.body.append(renderFootageControls(state));
      const selectedGateId='clip',gateDetailState='ready',row={kind:'footage_spend',state:'pending'};
      const gateDetail={state:'pending',request:row,packet},gateRowById=()=>row;
      // The generic gate viewer includes several unrelated evidence sections.
      // Exercise its exact spending-card expression with the real card renderer.
      const expression=source.match(/spendingCard\(\{\.\.\.footageCard,expiry:[^\n]+?\}\)/)[0];
      const footageCard=packet.footage_card;document.body.append(eval(expression));
      window.localExpiry=new Date(footageCard.expiry).toLocaleString();
    }""", {'packet': packet, 'source': (UI / 'board.js').read_text()})
    reference = screen.get_by_label('References, separated by commas', exact=True)
    expect(reference).to_have_value('Reference picture 1')
    reference.fill('Reference picture 1')
    screen.get_by_role('button', name='Save a new version', exact=True).click()
    screen.wait_for_function('saved!==null')
    expect(screen.get_by_label('Trailer paper cut').get_by_role('status').first).to_contain_text('Saved.')
    assert screen.evaluate('saved.paper.shots[0].references') == ['assets/opaque-picture.png']
    expect(screen.locator('[data-story-intro]')).to_contain_text('The story is signed.')
    expect(screen.get_by_label('Footage workspace')).not_to_contain_text('Supply and sign the story')
    assert screen.evaluate('document.body.textContent.includes(localExpiry)')
    assert packet['footage_card']['expiry'].endswith('+00:00')

    screen.evaluate("""async()=>{
      document.body.replaceChildren();
      const {renderDelivery}=await import('/ui/delivery.js');
      window.finalState={number:1,revision:'invented',rough_status:'Signed by you',colour_status:'Signed by you',
        recorded_stages:['canon_ingest','script','edit','colour_mix'],exports:[{id:'final',status:'Needs your signature',approval_recorded:false,samples:[],
          review:{issues_found:[],checks:{technical_probe:{duration_seconds:1,file_size_bytes:100}},
            metadata:{test_only:true,boundary_samples:[],measurements:{}}}}]};
      window.reveals=0;window.fetch=async(url,options={})=>{
        if(options.method==='POST'){if(!url.endsWith('/final/reveal'))throw Error('Unexpected write');reveals++;return new Response(JSON.stringify({message:'Shown in Finder.'}));}
        return new Response(JSON.stringify(finalState));
      };
      window.deliveryRender=renderDelivery;
      document.body.append(renderDelivery({project_id:'invented',stages:[{name:'compose',status:'not_started'}]},()=>{}));
    }""")
    next_step = screen.get_by_label('Next delivery step')
    expect(next_step).to_have_text('Next: Review and sign final approval.')
    screen.evaluate("""()=>{
      const panel=document.querySelector('[aria-label="Trailer delivery"]');
      panel.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,isPrimary:true,button:0,pointerId:7}));
      finalState.exports[0].status='Approved by you';finalState.exports[0].approval_recorded=true;
      finalState.recorded_stages.push('compose');
      deliveryRender({project_id:'invented',stages:[{name:'compose',status:'completed'}]},()=>{},false);
    }""")
    # Both visible messages stay together while a paint is deferred by a press.
    expect(screen.get_by_role('status')).to_contain_text('Current final file: Needs your signature.')
    expect(next_step).to_have_text('Next: Review and sign final approval.')
    screen.evaluate("""()=>document.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerId:7}))""")
    expect(screen.get_by_role('status')).to_contain_text('Current final file: Approved by you.')
    expect(next_step).to_have_text('Next: Show approved file in Finder.')
    screen.get_by_role('button', name='Show approved file in Finder', exact=True).click()
    expect(next_step).to_have_text('Your trailer is finished.')
    assert screen.evaluate('reveals') == 1
