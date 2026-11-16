import {el,CAPABILITY_TOKEN_KEY} from '/ui/lib.js';
let active=null;
const reviews=new Map();
// A background paint must not detach the control between press and click.
// Keep only the latest paint; its caller still verifies the current record.
let deliveryPress=null;
const pendingPaints=new Map();
document.addEventListener('pointerdown',event=>{
 if(event.button===0&&event.isPrimary&&event.target.closest?.('[aria-label="Trailer delivery"], [aria-label="Delivery review"]')){
  clearTimeout(deliveryPress?.timer);const press={id:event.pointerId};deliveryPress=press;
  press.timer=setTimeout(()=>{if(deliveryPress===press)releaseDeliveryPress({pointerId:press.id});},2000);
 }
},true);
function releaseDeliveryPress(event){
 const ending=deliveryPress;
 if(!ending||event?.pointerId!==undefined&&event.pointerId!==ending.id)return;
 clearTimeout(ending.timer);
 // The click follows pointerup. A microtask here would paint too early.
 setTimeout(()=>{
  if(deliveryPress!==ending)return;
  deliveryPress=null;const paints=[...pendingPaints.values()];pendingPaints.clear();
  for(const paint of paints)paint();
 },0);
}
document.addEventListener('pointerup',releaseDeliveryPress,true);
document.addEventListener('pointercancel',releaseDeliveryPress,true);
window.addEventListener('blur',()=>releaseDeliveryPress());
document.addEventListener('visibilitychange',()=>{if(document.hidden)releaseDeliveryPress();});
export function deferDeliveryPaint(key,paint){
 if(!deliveryPress)return false;
 pendingPaints.set(key,paint);return true;
}
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
 if(active?.id===s.project_id){active.update(s);if(refreshOnRender)active.refresh();return active.node;}
 const base=`/api/project/${encodeURIComponent(s.project_id)}/delivery`,node=el('section',{class:'paper-editor',role:'region','aria-label':'Trailer delivery'}),message=el('p',{role:'status'},'Checking the saved cut and its signed takes…'),body=el('fieldset',{'aria-label':'Saved cut and final files',disabled:'',style:'border:0;padding:0;margin:0;min-width:0'});
 let data,busy=false,flight=null,pendingRefresh=false,key='',progress=false,returningSummary=false,readGeneration=0,readController=null,returnGeneration=0,confirmation=null,boardState=s;
 const storyForms=new Map(),stageFeedback=new Map();
 const fileFeedback=new Map();let earlierOpen=false,revealedId=null;
 const summary=next=>`Saved cut loaded. Rough cut: ${next.rough_status}. ${next.exports.length?`Current final file: ${next.exports[0].status}.`:'Review the current file and its approvals below.'}`;
 function stageNote(stage){if(!stageFeedback.has(stage))stageFeedback.set(stage,el('p',{role:'note','aria-live':'polite'}));return stageFeedback.get(stage);}
 function clearFeedback(){for(const held of fileFeedback.values())held.node.textContent='';for(const note of stageFeedback.values())note.textContent='';}
 async function api(path='',value,signal){const r=await fetch(base+path,value===undefined?{cache:'no-store',signal}:{method:'POST',headers:{'Content-Type':'application/json','x-frontlot-capability':sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||''},body:JSON.stringify(value)});const v=await r.json();if(!r.ok)throw Error(v.detail||'Reopen the saved delivery controls and try again.');return v;}
 const working={
  'Review and sign the saved rough cut':'Preparing the saved rough cut for review…',
  'Record the signed rough-cut approval':'Recording your signed rough-cut approval…',
  'Review and sign colour and sound':'Preparing the saved colour and sound for review…',
  'Record the signed colour and sound approval':'Recording your signed colour and sound approval…',
  'Make the final file':'Making and measuring the final file locally…',
  'Review and sign final approval':'Checking this exact final file for review…',
  'Record the signed final approval':'Recording your signed final-file approval…',
  'Show approved file in Finder':'Showing this approved file in Finder…'};
 function button(label,fn,disabled=false,refreshAfter=true,primary=false,feedback=null){
  const control=el('button',{type:'button',disabled:disabled?'':null,...(primary?{class:'gate-primary','aria-current':'step'}:{})},label);
  control.onclick=async()=>{if(busy)return;const started=returnGeneration;let succeeded=false;
   confirmation=null;busy=true;body.disabled=true;refreshButton.disabled=true;progress=true;
   const words=working[label]||'Working…';message.textContent=words;control.textContent=words;control.setAttribute('aria-busy','true');
   if(label==='Record the signed final approval')node.querySelector('[aria-label="Next delivery step"]').textContent='Next: Wait for the signed final approval to finish recording and checking the saved file. Finder is waiting for that check.';
   if(feedback)feedback.textContent=(feedback.dataset.earlier==='true'?'This is an earlier file. ':'')+words;
   try{await fn();succeeded=true;}catch(e){progress=false;const earlier=feedback?.dataset.earlier==='true';confirmation=earlier?null:e.message;
    if(feedback)feedback.textContent=(earlier?'This is an earlier file. ':'')+e.message;
    message.textContent=summary(data)+(earlier?'':' '+e.message);
   }
   finally{busy=false;control.textContent=label;control.removeAttribute('aria-busy');
    if(!succeeded&&label==='Record the signed final approval')draw();
    if(succeeded&&!refreshAfter)body.disabled=returningSummary;
    else if(started===returnGeneration&&node.isConnected)await refresh(true,false,true);
    else body.disabled=returningSummary;
    refreshButton.disabled=false;
   }
  };return control;
 }
 function confirmed(words){confirmation=words;progress=false;returningSummary=false;message.textContent=summary(data)+' '+words;}
 function preparing(words){progress=true;message.textContent=words;}
 async function record(path,feedback){
  const r=await api(path,{});
  if(!r.delivery||!Array.isArray(r.delivery.recorded_stages))throw Error('Your signature is recorded, but the current review status could not be read. Refresh Delivery before continuing.');
  readGeneration++;readController?.abort();flight=null;pendingRefresh=false;
  await new Promise(resolve=>{
   const paint=()=>{data=r.delivery;key=JSON.stringify(data);draw();if(feedback)feedback.textContent=(feedback.dataset.earlier==='true'?'This is an earlier file. ':'')+r.message;confirmed(r.message);resolve();};
   if(!deferDeliveryPaint('delivery-record-result',paint))paint();
  });
 }
 function refresh(force=false,returning=false,preserveFeedback=false){
  if(returning){
   clearFeedback();
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
     if(!busy&&!preserveFeedback)clearFeedback();
     await new Promise(resolve=>{const paint=()=>{
     if(generation!==readGeneration){resolve();return;}
     const first=!data,encoded=JSON.stringify(next);
     if(encoded!==key){confirmation=null;data=next;key=encoded;draw();}
     body.disabled=busy;
     if(confirmation){progress=false;returningSummary=false;message.textContent=summary(next)+' '+confirmation;}
     else if(first||returningSummary||!busy){
      progress=false;returningSummary=false;
      message.textContent=summary(next);
     }
     resolve();};if(!deferDeliveryPaint('delivery-read-result-'+generation,paint))paint();});
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
 function draw(){if(deferDeliveryPaint('delivery-draw',draw))return;body.replaceChildren();
  const done=name=>data.recorded_stages.includes(name),current=data.exports[0];
  const next=data.rough_status!=='Signed by you'?'Review and sign the saved rough cut':data.colour_status&&!done('edit')?'Record the signed rough-cut approval':
   data.colour_status&&data.colour_status!=='Signed by you'?'Review and sign colour and sound':data.colour_status&&!done('colour_mix')?'Record the signed colour and sound approval':
   current?.status==='Needs your signature'?'Review and sign final approval':current?.status==='Approved by you'?!current.approval_recorded?'Record the signed final approval':revealedId===current.id?'Your trailer is finished':'Show approved file in Finder':'Make the final file';
  const action=(label,fn,disabled=false,refreshAfter=true,main=true,feedback=null)=>button(label,fn,disabled,refreshAfter,main&&label===next,feedback);
  body.append(el('p',{},`Saved version ${data.number}. Delivery uses this saved cut, including its colour, sound and signed cards. Save any editing changes before reviewing it.`),
   el('p',{'aria-label':'Next delivery step'},(next==='Your trailer is finished'?'':'Next: ')+next+'.'),
   el('section',{class:'paper-panel','data-delivery-stage':'edit'},el('h3',{},'Rough-cut approval'),el('p',{},data.rough_status),
   el('div',{class:'delivery-actions'},
    action('Review and sign the saved rough cut',async()=>{preparing('Preparing the saved rough cut for review locally…');const current=await api();const r=await api('/rough',{revision:current.revision});await onSign(r.gate_request_id);},!!data.colour_status&&(!done('canon_ingest')||!done('script')),true,true,stageNote('edit')),
    action('Record the signed rough-cut approval',()=>record('/rough/apply',stageNote('edit')),data.rough_status!=='Signed by you',false,true,stageNote('edit'))),stageNote('edit')),
   ...(data.colour_status?[el('section',{class:'paper-panel','data-delivery-stage':'colour_mix'},el('h3',{},'Colour and sound approval'),el('p',{},data.colour_status==='Needs your signature'&&data.rough_status==='Signed by you'&&!done('edit')?'Waiting for the rough cut to be recorded':data.colour_status),
    el('div',{class:'delivery-actions'},
    action('Review and sign colour and sound',async()=>{preparing('Preparing the saved colour and sound for review locally…');const r=await api('/colour',{revision:data.revision});await onSign(r.gate_request_id);},data.rough_status!=='Signed by you'||!done('edit'),true,true,stageNote('colour_mix')),
     action('Record the signed colour and sound approval',()=>record('/colour/apply',stageNote('colour_mix')),data.colour_status!=='Signed by you',false,true,stageNote('colour_mix'))),stageNote('colour_mix'))]:[]),
   el('div',{class:'paper-panel','data-delivery-stage':'export'},el('div',{class:'delivery-actions'},action('Make the final file',async()=>{preparing('Making and measuring the full-size file locally. Nothing is sent or spent…');const r=await api('/export',{revision:data.revision});progress=false;stageNote('export').textContent='The final file is ready to watch. '+r.stage_message;message.textContent=stageNote('export').textContent;},data.rough_status!=='Signed by you'||!!data.colour_status&&(data.colour_status!=='Signed by you'||!done('edit')||!done('colour_mix')),true,true,stageNote('export'))),stageNote('export')));
  if(data.outstanding_requirements?.length)body.append(el('section',{class:'paper-panel','aria-label':'Outstanding stage requirements'},
   el('h3',{},'What still prevents a finished stage'),el('p',{},'An unfinished stage is different from a missing control. Your individual signatures remain recorded; they do not substitute for a required stage review.'),
   ...data.outstanding_requirements.map(words=>el('p',{},words))));
  const earlier=el('details',{...(earlierOpen?{open:''}:{}),ontoggle:()=>{earlierOpen=earlier.open;}},el('summary',{},'Earlier exports'));
  for(const [index,item] of data.exports.entries()){
   const review=item.review,usable=['Needs your signature','Approved by you','Final file needs changes'].includes(item.status);
   let held=fileFeedback.get(item.id);
   if(!held||held.status!==item.status||held.earlier!==(index>0)){held={status:item.status,earlier:index>0,node:el('p',{role:'note','aria-live':'polite'})};fileFeedback.set(item.id,held);}
   const feedback=held.node,prefix=index>0?'This is an earlier file. ':'';feedback.dataset.earlier=String(index>0);
   const tile=el('section',{class:'paper-panel','data-delivery':item.id},el('h3',{},index>0?'Earlier final trailer file':'Final trailer file'),el('p',{},item.status),
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
   tile.append(el('div',{class:'delivery-actions'},
    action('Review and sign final approval',async()=>{const value=storyForm?{story_review:storyForm.read()}:{};feedback.textContent=prefix+'Rechecking this final file and its sound…';const r=await api('/'+item.id+'/approval',value);try{await onSign(r.gate_request_id);}finally{feedback.textContent='';}},item.status!=='Needs your signature'||!!data.colour_status&&(!done('edit')||!done('colour_mix')),true,index===0,feedback),
    action('Record the signed final approval',()=>record('/'+item.id+'/apply',feedback),item.status!=='Approved by you',false,index===0,feedback),
    action('Show approved file in Finder',async()=>{const r=await api('/'+item.id+'/reveal',{});feedback.textContent=prefix+r.message;if(index===0)await new Promise(resolve=>{const paint=()=>{revealedId=item.id;draw();confirmed('The approved trailer has been shown in Finder.');resolve();};if(!deferDeliveryPaint('delivery-reveal-result',paint))paint();});else{progress=false;message.textContent=summary(data);}},item.status!=='Approved by you',true,index===0,feedback)),feedback);
   if(index===0)body.append(tile);else earlier.append(tile);
  }
  if(data.exports.length>1)body.append(earlier);
 }
 const refreshButton=el('button',{type:'button',onclick:async()=>{if(busy)return;confirmation=null;progress=true;key='';body.disabled=true;refreshButton.disabled=true;refreshButton.textContent='Refreshing Delivery…';message.textContent='Checking the saved cut and its approvals…';try{await refresh(true);}finally{refreshButton.disabled=false;refreshButton.textContent='Refresh delivery';}}},'Refresh delivery');
 node.append(el('h2',{},'Delivery'),el('p',{},'Sign and record the saved rough cut, then colour and sound. Make the local final file, watch it, compare it with the story, then sign and record that exact file. Nothing is uploaded. No stage is finished without its own signature and required earlier reviews.'),message,
 refreshButton,el('p',{role:'note','aria-live':'polite'}),body);
 const stageKey=state=>JSON.stringify(state.stages?.map(stage=>[stage.name,stage.status]));
 active={id:s.project_id,node,refresh,update(state){const before=stageKey(boardState);boardState=state;if(data&&before!==stageKey(state))refresh();}};refresh();return node;
}
