import {el,CAPABILITY_TOKEN_KEY,actionButton,keepEditorPosition} from '/ui/lib.js';
import {setAudioProgress} from '/ui/next_step.js';
import {musicForms,effectForms,TEST_ONLY} from '/ui/music.js';
import {renderPriceSettings} from '/ui/audio_prices.js';
import {priceReview} from '/ui/media_prices.js';
import {paperCutEpoch} from '/ui/trailer.js';
let current=null;
export async function refreshVoice(project){if(current?.project===project)await current.refresh();}
// Keep the exact media element while live board data rebuilds the drawer.
// A different file or take gets a new player and never starts itself.
let signingPlayer=null;
function takePlayer(take){
  const key=JSON.stringify([take.project_id,take.id,take.sha256]);
  if(signingPlayer?.key!==key)signingPlayer={key,node:el('audio',{controls:'',preload:'metadata',src:`/api/project/${encodeURIComponent(take.project_id)}/audio-take/${take.id}/play`})};
  return signingPlayer.node;
}
const licence=t=>`Imported from: ${t.source}. Licence note: ${t.licence_note}${/[.!?]$/.test(t.licence_note.trim())?'':'.'} No generation charge.`;
const money=v=>`$${Number(v||0).toFixed(4)}`;
const button=(label,fn)=>el('button',{type:'button',onclick:fn},label);
const routeFor=kind=>kind==='effect'?'effects':kind==='music'?'music':'voice';
const moment=b=>`Shot ${b.shot_index+1} · starts at ${b.start_frame/24} seconds · Loop: ${b.loop?'yes':'no'}`;
const heldExplanation=item=>Array.isArray(item.reservation.retained_downloads)&&item.reservation.retained_downloads.length?
  'Audio arrived but could not be filed as a take. Its downloaded file has been retained. Money remains held; check your ElevenLabs account and sign the actual account outcome before buying anything else.':
  'No usable take was filed. The service outcome is unresolved and money remains held. Check your ElevenLabs account and sign the actual account outcome before buying anything else.';
async function api(url,data){
  const r=await fetch(url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','x-frontlot-capability':sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||''},body:JSON.stringify(data)});
  const value=await r.json();if(!r.ok)throw Error(value.detail||'Voice could not be read. Reload the board and try again.');return value;
}
export async function applySignedAccountOutcome(project,identity){
  return api(`/api/project/${encodeURIComponent(project)}/voice/resolution/${encodeURIComponent(identity)}/apply`,{});
}
export function refreshAudioAfterSigning(){current?.refresh(true);}
export function renderVoice(s,onSign,onChanged=async()=>{}){
  if(current?.project===s.project_id){current.refresh();return current.node;}
  const project=s.project_id,url=`/api/project/${encodeURIComponent(project)}/voice`;
  const node=el('section',{class:'voice-panel paper-editor','aria-label':'Trailer audio'}),message=el('p',{role:'status','aria-live':'polite'}),content=el('div',{}),controls=el('fieldset',{'aria-label':'Audio controls',style:'border:0;padding:0;margin:0;min-width:0'});
  const drafts=new Map(),players=new Map(),priceForm={},outcomes=new Map(),musicDraft={},effectDraft={},feedback=new Map();
  const storageKey='frontlot.voice-choice.'+project;let remembered={};
  try{remembered=JSON.parse(localStorage.getItem(storageKey)||'{}');}catch{}
  let voices=Array.isArray(remembered?.voices)?remembered.voices.filter(v=>typeof v?.id==='string'&&typeof v.name==='string'):[];
  let last='',busy=false,loading=false,pendingRefresh=false,refreshFailed=false,editEpoch=0,catalogueLoaded=false,lastData=null;
  let signatureEpoch=0,returningFromSigning=false;
  let revealingRequest=null;
  const revealResult=()=>{if(!revealingRequest)return;const target=document.querySelector(`[data-held-audio-notice="${revealingRequest}"]`);if(target){revealingRequest=null;target.scrollIntoView({block:'center',behavior:'instant'});}};
  const cancelReveal=()=>{revealingRequest=null;};
  for(const event of ['input','pointerdown','wheel','touchmove'])document.addEventListener(event,cancelReveal,{capture:true,passive:true});
  document.addEventListener('keydown',event=>{if(['Tab','PageDown','PageUp','Home','End','ArrowDown','ArrowUp',' '].includes(event.key))cancelReveal();},true);
  document.addEventListener('frontlot:signature-recorded',()=>{signatureEpoch++;message.textContent='';feedback.clear();node.querySelectorAll('.action-feedback').forEach(note=>note.remove());});
  let pressed=false,deferredData=null,pressTimer=null;
  node.addEventListener('pointerdown',()=>{pressed=true;clearTimeout(pressTimer);pressTimer=setTimeout(released,2000);},true);
  const released=()=>{clearTimeout(pressTimer);setTimeout(()=>{pressed=false;if(deferredData){const data=deferredData;deferredData=null;draw(data);}},0);};
  window.addEventListener('pointerup',released);window.addEventListener('pointercancel',released);
  const button=(label,fn,key=label)=>actionButton(label,fn,{feedback,key});
  const progress=()=>{if(lastData)setAudioProgress(project,{...lastData,catalogue_loaded:catalogueLoaded,choices:[...drafts].map(([index,d])=>({index,voice:d.voice,rights:d.rights}))});};
  const remember=()=>{try{localStorage.setItem(storageKey,JSON.stringify({voices,choices:Object.fromEntries([...drafts].map(([index,d])=>[index,d.voice]))}));}catch{}};
  const act=async fn=>{if(busy)return;const startedSignature=signatureEpoch;busy=true;controls.disabled=true;editEpoch++;refreshFailed=false;message.textContent='Working…';let resultText='';try{const result=await fn();resultText=typeof result==='string'?result:'Saved. Review the request before signing.';}catch(e){resultText=e.message;}finally{busy=false;last='';try{await refresh();await onChanged();}finally{controls.disabled=false;}if(startedSignature!==signatureEpoch)resultText='';message.textContent=resultText;}return resultText;};
  const loadVoices=button('Load account voices',()=>act(async()=>{voices=await api(url+'/catalogue',{});catalogueLoaded=true;remember();progress();return 'Account voices loaded. Choose a voice for the saved line.';}));
  node.append(el('h2',{},'Voice, music and sound effects'),el('p',{},'Choose a voice for each saved spoken line, request instrumental music or describe an effect for a paper-cut moment. One signature buys one take. Keeping or declining a take needs its own signature; a decline is final.'),
    message,controls);
  controls.append(loadVoices,el('p',{class:'source'},'Loading voices reads the connected account only. It sends no story and buys no speech.'),content);
  function draw(data){if(pressed){deferredData=data;return;}if(lastData&&JSON.stringify(lastData)!==JSON.stringify(data)){message.textContent='';feedback.clear();}keepEditorPosition(content,()=>drawContents(data));if(revealingRequest)requestAnimationFrame(revealResult);}
  function drawContents(data){
    lastData=data;
    content.replaceChildren();
    loadVoices.disabled=!data.account.configured;
    content.append(el('p',{class:'audio-account-state',role:data.account.configured?'note':'alert'},data.account.message));
    const b=data.budget;
    content.append(el('section',{class:'paper-panel voice-budget'},el('h3',{},'One production ceiling'),
      el('p',{},`${money(b.spent)} spent · ${money(b.held)} held · ${money(b.remaining)} remaining of ${money(b.ceiling)}`),
      el('p',{},`Recorded audio: ${money(b.audio_cash)} new cash · ${money(b.audio_credits)} replacement value of credits. Credits are counted once in the production total.`)));
    content.append(renderPriceSettings(data,priceForm,{api,act,onSign,url,button}));
    if(data.message)content.append(el('p',{role:'alert'},data.message));
    for(const warning of data.record_warnings||[])content.append(el('p',{role:'alert'},warning));
    for(const item of data.unresolved_requests){
      const id=item.reservation.reservation_id,r=item.request,form=el('section',{class:'paper-panel','data-account-review':id},el('h3',{},'Review an unresolved audio charge'),
        el('p',{role:'alert'},heldExplanation(item)),el('blockquote',{},r?.prompt||'The original signed request cannot be verified. Stop and investigate its approval history.'));
      const d=outcomes.get(id)||{charged:false,cash:0,credits:0,note:''};outcomes.set(id,d);
      const amounts=[],charged=el('input',{type:'checkbox'});charged.checked=d.charged;charged.onchange=()=>{
        d.charged=charged.checked;
        for(const [key,field] of amounts){if(!d.charged){d[key]=0;field.value='0';}field.disabled=!d.charged;}
      };form.append(el('label',{},charged,'The account was charged.'),el('p',{},'Leave this unchecked if your account was not charged: both amounts stay at zero. If it was charged, tick it and enter the actual amounts you checked, not the request’s maximum.'));
      for(const [label,key] of [['Actual new cash dollars','cash'],['Actual credit replacement dollars','credits'],['What I checked in the account','note']]){
        const field=el(key==='note'?'textarea':'input',key==='note'?{'aria-label':label}:{'aria-label':label,type:'number',step:'.0001'});field.value=d[key];field.oninput=()=>d[key]=key==='note'?field.value:Number(field.value);form.append(el('label',{},label,field));
        if(key!=='note'){field.disabled=!d.charged;amounts.push([key,field]);}
      }
      form.append(button('Review and sign this account outcome',()=>act(async()=>{const g=await api(url+'/resolution',{reservation_id:id,...d});await onSign(g.request_id);return 'Account outcome ready for your signature.';}),'outcome:'+id+':review'),
        button('Apply the signed account outcome',()=>act(async()=>{return (await api(`${url}/resolution/${id}/apply`,{})).message;}),'outcome:'+id+':apply'));content.append(form);
    }
    const protectedBox=el('aside',{class:'paper-panel'},el('h3',{},'Protected story lines'));
    for(const line of data.protected_lines)protectedBox.append(el('blockquote',{},line));
    if(!data.protected_lines.length)protectedBox.append(el('p',{},'No protected wording is recorded in the approved story.'));
    const lineArea=el('div',{class:'voice-lines'}),lineForms=el('div',{});lineArea.append(protectedBox,lineForms);content.append(lineArea);
    data.lines.forEach((line,index)=>{
      const d=drafts.get(index)||{voice:remembered?.choices?.[index]||'',notes:'',rights:false};drafts.set(index,d);
      if(d.voice&&!voices.some(v=>v.id===d.voice)){d.voice='';d.rights=false;}
      const box=el('section',{class:'paper-panel','data-voice-line':index},el('h3',{},`Spoken line ${index+1}`),el('blockquote',{},line.text),
        el('p',{},`${line.duration_frames/24} seconds · starts at ${line.start_frame/24} seconds`));
      const select=el('select',{'aria-label':`Voice for line ${index+1}`},el('option',{value:''},'Choose an account voice'));
      voices.forEach(v=>select.append(el('option',{value:v.id},v.name)));select.value=d.voice;select.onchange=()=>{d.voice=select.value;d.rights=false;rights.checked=false;remember();progress();};
      const notes=el('textarea',{'aria-label':`Pronunciation and delivery notes for line ${index+1}`,maxlength:2000});notes.value=d.notes;notes.oninput=()=>d.notes=notes.value;
      const rights=el('input',{type:'checkbox','aria-label':'I have permission to use this voice.'});rights.checked=d.rights;rights.onchange=()=>{d.rights=rights.checked;progress();};
      const prepare=button('Prepare a spending card',()=>act(async()=>{await api(url+'/prepare',{line_index:index,voice:d.voice,notes:d.notes,rights_confirmed:d.rights});}),'line:'+index+':prepare');prepare.disabled=!data.usable||!data.account.configured||!data.price_settings_signed||data.purchases_paused;
      if(data.line_source_warnings?.[index])box.append(el('p',{role:'alert'},data.line_source_warnings[index]));
      box.append(el('label',{},'Account voice',select),el('label',{},'Pronunciation and delivery notes',notes),
        el('p',{class:'source'},'These notes stay with the signed take for your review. This speech model does not receive direction notes. The story material sent is the saved line’s exact wording; model and voice choices are sent too.'),
        el('label',{},rights,'I have permission to use this voice.'),el('p',{},'ElevenLabs · Multilingual v2 · the saved line’s exact words'),prepare);
      lineForms.append(box);
    });
    content.append(...musicForms(data,musicDraft,{api,act,button,url:`/api/project/${encodeURIComponent(project)}/music`}));
    content.append(...effectForms(data,effectDraft,{api,act,button,url:`/api/project/${encodeURIComponent(project)}/effects`}));
    const requests=el('section',{class:'paper-panel'},el('h3',{},'Audio spending cards'));
    for(const item of [...data.requests].reverse()){
      const r=item.record,music=r.kind==='music',effect=r.kind==='effect',route=routeFor(r.kind),card=el('article',{class:'voice-spend paper-panel','data-kind':r.kind,'data-audio-request':r.request_id},el('h4',{},item.approved?'Signed request':'Waiting for your signature'),
        el('p',{},`ElevenLabs · ${r.model} · ${effect?'sound effect':music?'instrumental':r.binding.voice_label||r.voice}`),el('blockquote',{},r.prompt),el('p',{},effect?`Effect: ${r.duration_seconds} seconds. ${moment(r.binding)}`:music?`Instrumental cue: ${r.duration_seconds} seconds requested.`:`Line slot: ${r.duration_seconds} seconds. The service determines the take’s actual length; listen before keeping it.`),
        el('p',{},`Highest new cash: ${money(r.max_cash_usd)} · highest credit replacement value: ${money(r.max_credits_usd)}`),
        el('p',{},`Price expires ${new Date(r.price_expires_at*1000).toLocaleString()}`),
        el('p',{},effect?'The paper-cut moment stays here. Only the description, model, length and loop choice are sent.':music?TEST_ONLY:`Notes kept here, not sent: ${r.binding.performance_notes||'None'}`));
      if(item.source_warning)card.append(el('p',{role:'alert'},item.source_warning));
      const made=data.takes.some(t=>t.approved_request_id===r.request_id);
      const held=data.unresolved_requests.find(item=>item.request?.request_id===r.request_id);
      if(held)card.append(el('div',{'data-held-audio-notice':r.request_id},el('p',{role:'alert'},heldExplanation(held)),button('Review this held charge',()=>{document.querySelector(`[data-account-review="${held.reservation.reservation_id}"]`)?.scrollIntoView({block:'center',behavior:'instant'});})));
      if(item.recoverable)card.append(el('p',{},'A completed take is recorded in its receipt but is missing here. Recover it without another call or charge.'),
        button('Recover recorded take',()=>act(async()=>{return (await api(`/api/project/${encodeURIComponent(project)}/${route}/${r.request_id}/recover`,{})).message;}),'request:'+r.request_id+':recover'));
      else if(item.error&&!held)card.append(el('p',{role:'alert'},item.error));
      else if(!made && item.approved&&!held)card.append(button('Make this approved take',async()=>{revealingRequest=r.request_id;const message=await act(async()=>{const result=await api(`/api/project/${encodeURIComponent(project)}/${route}/${r.request_id}/send`,{});if(!result.success)throw Error(result.message);revealingRequest=null;return 'Take ready. Listen, then sign keeping or declining it.';});await new Promise(requestAnimationFrame);revealResult();return message;},'request:'+r.request_id+':send'));
      else if(!made && !held && item.gate_request_id)card.append(button('Review and sign this request',()=>onSign(item.gate_request_id),'request:'+r.request_id+':sign'));
      if(made)card.append(el('p',{},'This signature already made a take. Another take needs another spending card and signature.'));
      requests.append(card);
    }
    content.append(requests);
    const takes=el('section',{class:'paper-panel'},el('h3',{},'Audio takes'));
    for(const t of data.takes){
      const music=t.kind==='music',effect=t.kind==='effect',noun=effect?'effect':'music',route=routeFor(t.kind),box=el('article',{class:'voice-take paper-panel','data-kind':t.kind,'data-take-id':t.id},el('h4',{},t.status==='Accepted'?'Kept':t.status),
        el('p',{},effect?`Effect take · ${t.duration_seconds.toFixed(2)} seconds · ${moment(t.binding)}`:music?`Music take · ${t.duration_seconds.toFixed(2)} seconds`:`Spoken line ${t.binding.line_index+1} · ${t.voice_name} · ${t.duration_seconds.toFixed(2)} seconds`),el('blockquote',{},t.spoken_words));
      if(music&&t.origin==='generated')box.append(el('p',{},TEST_ONLY));
      if(t.origin==='imported')box.append(el('p',{},licence(t)));
      let player=players.get(t.id);if(!player){player=el('audio',{controls:'',preload:'metadata',src:`/api/project/${encodeURIComponent(project)}/audio-take/${t.id}/play`,'aria-label':effect?'Listen to effect take':music?'Listen to music take':`Listen to line ${t.binding.line_index+1}`});players.set(t.id,player);}
      box.append(player);
      if(t.status!=='Declined'){
        if(t.status==='Waiting for your decision')box.append(button('Keep this take',()=>act(async()=>{const g=await api(`/api/project/${encodeURIComponent(project)}/${route}/${t.id}/decision`,{decision:'accept'});await onSign(g.request_id);return 'Keep decision ready for your signature.';}),'take:'+t.id+':keep'));
        if(t.status==='Needs a fresh look')box.append(el('p',{},data.paper_review_message||(effect?'The effect brief, paper-cut moment or file changed. Save the brief, then make a new request or choose a new file and review it again.':music?'The brief or file changed. Make a new request or choose a new file, then review it again.':'The line or file changed. Prepare a new spending card for a new take; this take cannot be kept for changed words.')));
        box.append(button('Decline this take permanently',()=>act(async()=>{const g=await api(`/api/project/${encodeURIComponent(project)}/${route}/${t.id}/decision`,{decision:'decline'});await onSign(g.request_id);return 'Decline decision ready for your signature.';}),'take:'+t.id+':decline'));
      }else box.append(el('p',{},t.origin==='imported'?`Decline is final for this take. Choose another ${noun} file to review.`:'Decline is final. Ask for another take with its own spending card.'));
      box.append(button(t.origin==='imported'?`Choose another ${noun} file`:'Ask for another take',()=>{
        const target=node.querySelector(music||effect?`#${t.kind}-${t.origin==='imported'?'import':'compose'}`:`[data-voice-line="${t.binding.line_index}"]`);target?.scrollIntoView({block:'center'});
        message.textContent=t.origin==='imported'?'Choose another file and record its source and licence note.':'Prepare another spending card. A new signature is required.';
      },'take:'+t.id+':retake'));takes.append(box);
    }
    content.append(takes);
    progress();
  }
  async function refresh(afterSigning=false){
    if(afterSigning){editEpoch++;returningFromSigning=true;message.textContent='Checking the saved audio decisions…';}
    if(loading||busy){pendingRefresh=true;return;}loading=true;pendingRefresh=false;const started=editEpoch,paperStarted=paperCutEpoch();
    try{const data=await api(url);if(started!==editEpoch||paperStarted!==paperCutEpoch()||busy){pendingRefresh=true;return;}const key=JSON.stringify(data)+JSON.stringify(voices);if(refreshFailed){message.textContent='';refreshFailed=false;}if(key!==last){last=key;draw(data);}if(returningFromSigning){const count=status=>data.takes.filter(t=>t.status===status).length;message.textContent=`Audio loaded. Kept: ${count('Accepted')}. Waiting for a take decision: ${count('Waiting for your decision')}. Declined: ${count('Declined')}. Needs a fresh look: ${count('Needs a fresh look')}.`;returningFromSigning=false;}}
    catch(e){if(started!==editEpoch||paperStarted!==paperCutEpoch()||busy){pendingRefresh=true;return;}refreshFailed=true;message.textContent=e.message+' Check the approved story and signed audio price settings, then reload.';}
    finally{loading=false;if(pendingRefresh&&!busy){pendingRefresh=false;refresh();}}
  }
  current={project,node,refresh};refresh();return node;
}

export function renderAudioSigning(detail,actions,onReturn,stale){
  const r=detail.packet?.record;
  const box=el('section',{class:'viewer-inner paper-editor'},el('h2',{},'Review and sign audio'));
  box.append(button('Return to audio',onReturn));
  if(stale){box.append(el('p',{},'This request has been decided. Return to audio to continue.'));if(!r||r.take||r.reservation_id||detail.packet.audio_prices)return box;}
  if(!r){box.append(el('p',{role:'alert'},detail.packet?.error||'The current audio evidence is unavailable. Return to audio and prepare a new request before signing.'));return box;}
  if(detail.packet.media_prices){
    box.append(priceReview(detail.packet));
  }else if(r.reservation_id){box.append(el('h3',{},'Record the account outcome'),el('blockquote',{},r.request.prompt),el('p',{},r.charged?'Charged':'Not charged'),el('p',{},`Cash ${money(r.cash_usd)} · credit replacement value ${money(r.credits_usd)}`),el('p',{},r.note),el('p',{},'This does not submit the request again or invent a take.'));
  }else if(r.take){box.append(el('h3',{},r.decision==='accept'?'Keep this take':'Decline this take permanently'),el('p',{},'This signs your decision about the exact saved audio. A decline is final.'),
    el('blockquote',{},detail.packet.spoken_words),
    takePlayer(r.take));
    if(r.take.kind==='speech')box.append(el('p',{},r.take.binding?.performance_notes||'No direction notes.'));
    if(r.take.kind==='music')box.append(el('p',{},r.take.origin==='generated'?TEST_ONLY:licence(r.take)));
    if(r.take.kind==='effect')box.append(el('p',{},moment(r.take.binding)),el('p',{},r.take.origin==='imported'?licence(r.take):'Generated through your individually approved sound-effect request.'));
  }else{box.append(el('p',{},`ElevenLabs · ${r.model} · ${r.kind==='effect'?'sound effect':r.kind==='music'?'instrumental':r.binding.voice_label||r.voice}`),el('blockquote',{},r.prompt),
    el('p',{},`${r.kind==='effect'?'Effect':r.kind==='music'?'Instrumental cue':'Line slot'}: ${r.duration_seconds} seconds · highest cash ${money(r.max_cash_usd)} · highest credit replacement value ${money(r.max_credits_usd)}`),
    el('p',{},`Bound expires ${new Date(r.price_expires_at*1000).toLocaleString()}`),el('p',{},'One signature buys one call. No retries or another provider.'),
    el('p',{},r.kind==='effect'?`${moment(r.binding)}. Placement stays here; only description, model, length and loop choice are sent.`:r.kind==='music'?TEST_ONLY:`Notes stay here and are not sent: ${r.binding.performance_notes||'None'}`));}
  if(detail.packet.source_warning)box.append(el('p',{role:'alert'},detail.packet.source_warning));
  if(detail.packet.remaining_usd!==undefined)box.append(el('p',{},`Left under the production ceiling: ${money(detail.packet.remaining_usd)}.`));
  if(detail.packet.budget_warning)box.append(el('p',{role:'alert'},detail.packet.budget_warning));
  box.append(actions);return box;
}
