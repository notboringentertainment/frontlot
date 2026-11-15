import {el,CAPABILITY_TOKEN_KEY} from '/ui/lib.js';
let active=null;
const reviews=new Map();
function storyAnswers(questions){
 const node=el('section',{'aria-label':'Final story comparison',class:'paper-panel'},
  el('h4',{},'Compare this final file with the signed story'),
  el('p',{},'Watch and listen to this exact file. Every protected line must be heard word for word; a title or subtitle does not count. These are your findings, separate from the automatic technical checks. Leave a finding unchecked if the cut needs work.'));
 const controls=[];
 function check(words){const input=el('input',{type:'checkbox'});controls.push(input);node.append(el('label',{style:'display:block;margin:12px 0'},input,el('span',{style:'white-space:pre-wrap'},words)));return input;}
 const lines=questions.protected_lines.map(line=>({line,input:check('I heard this exact protected line, word for word: '+line)}));
 const locks=questions.locks.map(row=>({...row,input:check((row.spoiler?'This protected spoiler stays out of the trailer: ':'This story fact is honoured: ')+row.wording)}));
 const entities=questions.entities.map(subject=>({subject,input:check('This character or place stays consistent with the story: '+subject)}));
 node.append(el('p',{style:'white-space:pre-wrap'},'Story tone: '+Object.values(questions.tone).flat().join('; ')));
 const tone=el('textarea',{'aria-label':'How this final file keeps the story’s tone',rows:'3'});
 node.append(el('label',{},'In your words, how does this final file keep the story’s tone?',tone));
 return {node,read(){
  if(controls.some(input=>!input.checked)||!tone.value.trim())throw Error('Complete the final story comparison after watching and listening. If a protected line is missing or a story finding is uncertain, fix the cut before signing.');
  return {story_sha256:questions.story_sha256,canon_pass:{status:'pass',unresolved_flags:[],
   protected_lines:lines.map(row=>({line:row.line,present_verbatim:true,audible:true})),
   locks:locks.map(row=>({lock_id:row.id,verdict:'honored'})),
   continuity_spotchecks:entities.map(row=>({subject:row.subject,verdict:'consistent'})),
   tone:{must_never_feel_like_verdict:tone.value}}};
 }};
}
function boundaryPictures(samples=[],lazy=false){
 const node=el('section',{'aria-label':'Pictures from the final file',class:'paper-panel'},el('h4',{},'Pictures at cuts and cards'),
  el('p',{},'These are pictures from this exact file. Check the story, faces and lettering; the automatic picture check only measures black or unchanged picture.'));
 const grid=el('div',{style:'display:flex;flex-wrap:wrap;gap:12px'});
 for(const row of samples)grid.append(el('figure',{style:'margin:0;max-width:220px'},el('img',{src:row.url,...(lazy?{loading:'lazy'}:{}),alt:`Final-file picture at ${row.seconds.toFixed(3)} seconds`,style:'width:220px;max-width:100%;height:auto'}),
  el('figcaption',{},`${row.seconds.toFixed(3)} seconds (frame ${row.frame})`)));
 node.append(grid);node.hidden=!samples.length;return node;
}
export function refreshDelivery(){return active?.refresh(true,true);}

export function renderDeliverySigning(detail,actions,onReturn){
 const id=detail.request_id,packet=detail.packet||{};
 let held=reviews.get(id);
 if(!held){
  const video=el('video',{controls:'',preload:'auto',src:packet.play_url,'aria-label':'Watch this exact delivery review',style:'width:100%;max-height:480px'});
  const heading=el('h2',{}),controls=el('div',{}),words=el('div',{}),pictures=el('div',{}),node=el('section',{class:'paper-editor',role:'region','aria-label':'Delivery review'},
   heading,
   el('p',{},'Watch and listen to the whole file before starting your signature. A technical check never replaces your approval.'),video,words,pictures,controls,
   el('button',{type:'button',onclick:onReturn},'Return to delivery'));
  held={node,heading,video,controls,words,pictures,sampleKey:null,watched:false};reviews.set(id,held);
  video.onended=()=>{held.watched=true;for(const b of controls.querySelectorAll('button.gate-primary'))b.disabled=!held.ready;};
 }
 const stage=detail.request?.stage;
 held.heading.textContent=stage==='edit'?'Review the saved rough cut':stage==='colour_mix'?'Review the saved colour and sound':stage==='compose'?'Review this exact final file':'Preparing this saved review';
 held.ready=!!packet.play_url&&!packet.packet_error;
 const source=packet.packet_error?'':(packet.play_url??held.video.getAttribute('src')??'');
 if((held.video.getAttribute('src')||'')!==source){
  held.video.pause();held.watched=false;
  if(source)held.video.setAttribute('src',source);else held.video.removeAttribute('src');
  held.video.load();
 }
 held.words.replaceChildren(...(packet.messages||[]).map(s=>el('p',{},s)));
 if(packet.final_story_review){const check=packet.final_story_review.canon_pass;
  held.words.append(el('h3',{},'Your final story comparison'),
   el('p',{},'These findings are part of the exact-file signature, separate from the technical verdict.'),
   ...check.protected_lines.map(row=>el('p',{style:'white-space:pre-wrap'},'You confirm this protected line is heard verbatim: '+row.line)),
   ...check.locks.map(row=>{const fact=packet.final_story_questions?.locks.find(q=>q.id===row.lock_id);return el('p',{style:'white-space:pre-wrap'},(fact?.spoiler?'You confirm this protected spoiler stays out of the trailer: ':'You confirm this story fact is honoured: ')+(fact?.wording||'Reopen the story comparison to read this finding.'));}),
   ...check.continuity_spotchecks.map(row=>el('p',{},'You confirm this character or place stays consistent: '+row.subject)),
   el('p',{style:'white-space:pre-wrap'},'Your tone finding: '+check.tone.must_never_feel_like_verdict));
 }
 const sampleKey=JSON.stringify(packet.samples||[]);
 if(sampleKey!==held.sampleKey){held.sampleKey=sampleKey;held.pictures.replaceChildren(boundaryPictures(packet.samples));}
 if(packet.error)held.words.append(el('p',{role:'alert'},packet.error));
 held.controls.replaceChildren(actions);
 if(!held.watched||!held.ready)for(const b of held.controls.querySelectorAll('button.gate-primary'))b.disabled=true;
 return held.node;
}

export function renderDelivery(s,onSign,refreshOnRender=true){
 if(active?.id===s.project_id){if(refreshOnRender)active.refresh();return active.node;}
 const base=`/api/project/${encodeURIComponent(s.project_id)}/delivery`,node=el('section',{class:'paper-editor',role:'region','aria-label':'Trailer delivery'}),message=el('p',{role:'status'},'Checking the saved cut and its signed takes…'),body=el('fieldset',{'aria-label':'Saved cut and final files',disabled:'',style:'border:0;padding:0;margin:0;min-width:0'});
 let data,busy=false,flight=null,pendingRefresh=false,key='',progress=false,returningSummary=false,readGeneration=0,readController=null,returnGeneration=0,confirmation=null;
 const storyForms=new Map();
 async function api(path='',value,signal){const r=await fetch(base+path,value===undefined?{cache:'no-store',signal}:{method:'POST',headers:{'Content-Type':'application/json','x-frontlot-capability':sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||''},body:JSON.stringify(value)});const v=await r.json();if(!r.ok)throw Error(v.detail||'Reopen the saved delivery controls and try again.');return v;}
 function button(label,fn,disabled=false){return el('button',{type:'button',disabled:disabled?'':null,onclick:async()=>{if(busy)return;const started=returnGeneration;confirmation=null;busy=true;body.disabled=true;try{await fn();}catch(e){confirmation=null;progress=false;message.textContent=e.message;}finally{busy=false;if(started===returnGeneration&&node.isConnected)await refresh(true);else body.disabled=returningSummary;}}},label);}
 function confirmed(words){confirmation=words;progress=false;returningSummary=false;message.textContent=words;}
 function preparing(words){progress=true;message.textContent=words;}
 function refresh(force=false,returning=false){
  if(returning){
   returnGeneration++;
   confirmation=null;
   returningSummary=true;
   body.disabled=true;message.textContent='Checking the saved cut and its approvals…';
   // Do not let a read begun before the signature hold its return hostage.
   // The generation check also rejects transports that deliver after abort.
   readGeneration++;readController?.abort();flight=null;
  }
  if(flight){if(force)pendingRefresh=true;return flight;}
  if(busy&&!force)return Promise.resolve();
  const generation=++readGeneration,controller=new AbortController();readController=controller;
  flight=(async()=>{
   do{
    pendingRefresh=false;
    try{
     const next=await api('',undefined,controller.signal);
     if(generation!==readGeneration)return;
     // A signing return may arrive while an older read is waiting. Discard
     // that answer and fulfil every waiter only after the queued read lands.
     if(pendingRefresh)continue;
     const first=!data,encoded=JSON.stringify(next);
     if(encoded!==key){data=next;key=encoded;draw();}
     body.disabled=busy;
     if(confirmation){progress=false;returningSummary=false;message.textContent=confirmation;}
     else if(first||returningSummary||!busy&&(progress||message.textContent.startsWith('Saved cut loaded.'))){
      progress=false;returningSummary=false;
      message.textContent=`Saved cut loaded. Rough cut: ${next.rough_status}. ${next.exports.some(e=>e.status==='Approved by you')?'The current final file is approved by you.':'Review the current file and its approvals below.'}`;
     }
    }catch(e){
     if(generation!==readGeneration)return;
     if(pendingRefresh)continue;
     confirmation=null;progress=false;message.textContent=e.message;key='';body.disabled=true;
     for(const v of node.querySelectorAll('video')){v.pause();v.removeAttribute('src');v.load();}
    }
   }while(pendingRefresh);
  })().finally(()=>{if(generation===readGeneration){flight=null;readController=null;}});
  return flight;
 }
 function draw(){body.replaceChildren();
  body.append(el('p',{},`Saved version ${data.number}. Delivery uses this saved cut, including its colour, sound and signed cards. Save any editing changes before reviewing it.`),
   el('h3',{},'Rough-cut approval'),el('p',{},data.rough_status),
   button('Review and sign the saved rough cut',async()=>{await refresh(true,true);if(!key)throw Error('The saved cut could not be checked. Reopen it and try this review again.');preparing('Preparing the saved rough cut for review locally…');const r=await api('/rough',{revision:data.revision});await onSign(r.gate_request_id);}),
   button('Record the signed rough-cut approval',async()=>{const r=await api('/rough/apply',{});confirmed(r.message);},data.rough_status!=='Signed by you'),
   ...(data.colour_status?[el('h3',{},'Colour and sound approval'),el('p',{},data.colour_status),
    button('Review and sign colour and sound',async()=>{preparing('Preparing the saved colour and sound for review locally…');const r=await api('/colour',{revision:data.revision});await onSign(r.gate_request_id);},data.rough_status!=='Signed by you'),
    button('Record the signed colour and sound approval',async()=>{const r=await api('/colour/apply',{});confirmed(r.message);},data.colour_status!=='Signed by you')]:[]),
   button('Make the final file',async()=>{preparing('Making and measuring the full-size file locally. Nothing is sent or spent…');const r=await api('/export',{revision:data.revision});progress=false;message.textContent='The final file is ready to watch. '+r.stage_message;},data.rough_status!=='Signed by you'||!!data.colour_status&&data.colour_status!=='Signed by you'));
  if(data.outstanding_requirements?.length)body.append(el('section',{class:'paper-panel','aria-label':'Outstanding stage requirements'},
   el('h3',{},'What still prevents a finished stage'),el('p',{},'An unfinished stage is different from a missing control. Your individual signatures remain recorded; they do not substitute for a required stage review.'),
   ...data.outstanding_requirements.map(words=>el('p',{},words))));
  const earlier=el('details',{},el('summary',{},'Earlier exports'));
  for(const [index,item] of data.exports.entries()){
   const review=item.review,usable=['Needs your signature','Approved by you','Final file needs changes'].includes(item.status);
   const tile=el('section',{class:'paper-panel','data-delivery':item.id},el('h3',{},'Final trailer file'),el('p',{},item.status),
    el('p',{},review.metadata.test_only?'Test trailer only. Generated ElevenLabs music is not cleared for release until ElevenLabs confirms otherwise in writing.':'Local trailer file. Your signature approves this file, not a legal clearance of its material.'),
    el('p',{},'1920 by 1080 · widescreen · twenty-four frames per second · stereo sound.'),
    el('video',{controls:'',preload:'metadata',...(usable?{src:base+'/'+item.id+'/play'}:{}),'aria-label':'Play exported trailer',style:'width:100%;max-height:480px'}));
   for(const words of review.issues_found)tile.append(el('p',{},words));
   if(item.status==='Needs a fresh look')tile.append(el('p',{},'This earlier file is no longer the current approved trailer. A newer final signature or a changed cut needs its own review.'));
   tile.append(boundaryPictures(item.samples,index>0));
   const measured=review.metadata.measurements,figures=el('details',{},el('summary',{},'Measured figures from this final file'));
   figures.append(el('p',{},`Duration: ${review.checks.technical_probe.duration_seconds.toFixed(3)} seconds. File size: ${review.checks.technical_probe.file_size_bytes.toLocaleString()} bytes.`),
    el('p',{},`Overall loudness: ${measured.integrated_lufs??'not measurable'} LUFS; the target is about minus sixteen. Highest playback peak: ${measured.true_peak_dbtp??'not measurable'} dBTP; it must stay below minus one.`),
    el('p',{},`${review.metadata.boundary_samples.length} pictures sampled at cut and card boundaries. Watch them for story, faces and lettering; the automatic checks do not judge those.`));tile.append(figures);
   let storyForm;
   if(data.story_questions&&item.status==='Needs your signature'){
    const identity=item.id+data.story_questions.story_sha256;
    if(!storyForms.has(identity))storyForms.set(identity,storyAnswers(data.story_questions));
    storyForm=storyForms.get(identity);tile.append(storyForm.node);
   }
   if(data.story_questions&&item.status==='Approved by you')tile.append(el('p',{},'Your final story comparison is recorded with your signature of this exact file, including every protected line heard verbatim.'));
   tile.append(button('Review and sign final approval',async()=>{const value=storyForm?{story_review:storyForm.read()}:{};preparing('Rechecking the exact final file and its sound…');const r=await api('/'+item.id+'/approval',value);await onSign(r.gate_request_id);},item.status!=='Needs your signature'),
    button('Record the signed final approval',async()=>{const r=await api('/'+item.id+'/apply',{});confirmed(r.message);},item.status!=='Approved by you'),
    button('Show approved file in Finder',async()=>{const r=await api('/'+item.id+'/reveal',{});message.textContent=r.message+' '+r.path;},item.status!=='Approved by you'));
   if(index===0)body.append(tile);else earlier.append(tile);
  }
  if(data.exports.length>1)body.append(earlier);
 }
 node.append(el('h2',{},'Delivery'),el('p',{},'Sign the saved rough cut, make the local final file, watch it, then sign that exact file. Nothing is uploaded. No stage is finished without its own signature and required earlier reviews.'),message,
 el('button',{type:'button',onclick:async()=>{confirmation=null;progress=true;key='';body.disabled=true;await refresh(true);}},'Refresh delivery'),body);
 active={id:s.project_id,node,refresh};refresh();return node;
}
