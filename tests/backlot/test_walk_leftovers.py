"""Three isolated presentation checks, without a board or paid services."""
from playwright.sync_api import expect
from tests.backlot.test_walk_findings import screen, UI


def test_1_older_trailer_adoption_explains_reviews_without_changing_its_request(screen):
    source = (UI / 'board.js').read_text()
    for path in UI.rglob('*'):
        if path.is_file():
            text = path.read_bytes()
            assert not any(phrase.encode() in text for phrase in ('trailer-test', 'Ben’s five', 'Ben decided')), path.name
    start = source.index('  if(s.is_trailer&&!s.trailer_damaged&&!s.trailer_setup_pending&&!s.trimmed_contract&&!selectedGateId)')
    end = source.index('\n  if(s.is_trailer &&', start)
    screen.evaluate("""async source=>{
      const {el}=await import('/ui/lib.js');const s={is_trailer:true};
      const selectedGateId=null,viewer=document.body,encodedProjectId='invented';
      const CAPABILITY_TOKEN_KEY='invented',refresh=async()=>{},selectGate=async()=>{};
      window.adoption=[];window.fetch=async(url,options)=>{
        adoption.push({url,method:options.method,body:options.body});
        return new Response(JSON.stringify({request_id:'invented'}));
      };eval(source);
    }""", source[start:end])
    words = screen.locator('body').inner_text()
    assert 'five signed reviews: Story, Paper cut, Rough cut, Colour and sound, and Final file' in words
    assert 'other whole-stage reviews are not part of a trailer' in words
    assert 'Ben decided' not in words and 'for this test' not in words
    screen.get_by_role('button', name='Review and sign the five-review list', exact=True).click()
    screen.wait_for_function('adoption.length===1')
    assert screen.evaluate('adoption') == [{'url': '/api/project/invented/trailer-contract/review', 'method': 'POST', 'body': '{}'}]


def test_2_shot_purpose_punctuation_is_not_doubled(screen):
    words = screen.evaluate("""async()=>{
      const m=await import('/ui/next_step.js');
      const s={project_id:'invented',stages:[{name:'canon_ingest',status:'completed'}],gates:{requests:[]}};
      const node=m.renderNextStep(s,()=>{});document.body.append(node);
      m.setFootageProgress('invented',{shots:['An invitation.','An invitation!','An invitation?','An invitation'].map((purpose,i)=>
        ({label:'Shot '+(i+1),purpose,references:[],requests:[]}))});
      return node.textContent;
    }""")
    for index, punctuation in enumerate(('.', '!', '?', '.'), 1):
        assert f'Shot {index}: An invitation{punctuation} No reference picture' in words
    assert 'invitation..' not in words


def test_3_recording_wait_is_visible_then_finder_works_without_a_refresh(screen):
    screen.evaluate("""async()=>{
      const {renderDelivery}=await import('/ui/delivery.js');
      window.value={number:1,revision:'invented',rough_status:'Signed by you',colour_status:'Signed by you',
        recorded_stages:['canon_ingest','script','edit','colour_mix'],exports:[{id:'final',status:'Approved by you',approval_recorded:false,samples:[],
          review:{issues_found:[],checks:{technical_probe:{duration_seconds:1,file_size_bytes:100}},
            metadata:{test_only:true,boundary_samples:[],measurements:{}}}}]};
      window.writes=[];window.reads=0;
      window.fetch=async(url,options={})=>{
        if(options.method==='POST'){
          writes.push(url);
          if(url.endsWith('/apply'))return await new Promise(resolve=>{window.finishRecording=()=>{
            value.recorded_stages.push('compose');value.exports[0].approval_recorded=true;
            resolve(new Response(JSON.stringify({message:'Your final-file signature for this file is recorded.',delivery:value})));
          };});
          if(url.endsWith('/reveal'))return new Response(JSON.stringify({message:'Shown in Finder.'}));
          throw Error('Unexpected action');
        }
        reads++;
        if(reads>1)return await new Promise(()=>{}); // A later board read must not hold Finder hostage.
        return new Response(JSON.stringify(value));
      };
      window.renderDeliveryState=renderDelivery;
      document.body.append(renderDelivery({project_id:'invented',stages:[{name:'compose',status:'not_started'}]},()=>{}));
    }""")
    next_step = screen.get_by_label('Next delivery step')
    expect(next_step).to_have_text('Next: Record the signed final approval.')
    screen.get_by_role('button', name='Record the signed final approval', exact=True).click()
    screen.wait_for_function('typeof finishRecording==="function"')
    # Model the stage-list refresh that can arrive before the apply response.
    screen.evaluate("""()=>renderDeliveryState({project_id:'invented',stages:[{name:'compose',status:'completed'}]},()=>{},false)""")
    expect(next_step).to_contain_text('Wait for the signed final approval')
    expect(next_step).to_contain_text('Finder is waiting')
    expect(screen.get_by_role('button', name='Show approved file in Finder', exact=True)).to_be_disabled()
    screen.evaluate('finishRecording()')
    expect(next_step).to_have_text('Next: Show approved file in Finder.')
    finder = screen.get_by_role('button', name='Show approved file in Finder', exact=True)
    expect(finder).to_be_enabled()
    finder.click()
    expect(next_step).to_have_text('Your trailer is finished.')
    assert screen.evaluate('writes') == ['/api/project/invented/delivery/final/apply', '/api/project/invented/delivery/final/reveal']
