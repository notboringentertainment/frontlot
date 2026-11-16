// Guidance only. Every action remains in its existing workspace or signer.
import {el,actionButton} from '/ui/lib.js';
let project=null,state=null,audio=null,footage=null,setup=null,node=null,openSignature=null,signatureUpdating=false;
const decided=new Set();
document.addEventListener('frontlot:signature-recorded',e=>{decided.add(e.detail.id);signatureUpdating=true;paint();});
document.addEventListener('frontlot:paper-saved',e=>{if(project===e.detail.project&&state){state={...state,paper_cut:e.detail.paper};paint();}});
export function setAudioProgress(id,value){if(project===id){audio=value;paint();}}
export function setFootageProgress(id,value){if(project===id){footage=value;paint();}}
export function setFootageSetupProgress(id,value){if(project===id){setup=value;paint();}}
export function renderNextStep(s,onSign){
  if(project!==s.project_id){project=s.project_id;audio=null;footage=null;setup=null;node=el('section',{class:'paper-panel next-step','aria-label':'Next step'});}
  state=s;if(!(s.gates?.requests||[]).some(r=>decided.has(r.request_id)&&r.state==='pending'))signatureUpdating=false;openSignature=onSign;paint();return node;
}
function target(scope,label){
  const parent=document.querySelector(scope);
  if(!parent)return null;
  return [...parent.querySelectorAll('button,summary,input,select,textarea')].find(e=>
    e.textContent.trim()===label||e.getAttribute('aria-label')===label||e.closest('label')?.textContent.trim()===label);
}
function paint(){
  if(!node||!state)return;
  let text,find=null,gate=null,identity=null;
  const obsolete=new Set([...(audio?.requests||[]).filter(r=>r.signing_refusal).map(r=>r.gate_request_id),...(footage?.shots||[]).flatMap(s=>s.requests).filter(r=>r.signing_refusal).map(r=>r.gate_id)]);
  const rows=state.gates?.requests||[],pending=rows.find(r=>r.state==='pending'&&!r.signing_refusal&&!obsolete.has(r.request_id)&&!decided.has(r.request_id));
  const paper=state.paper_cut||{},current=paper.current;
  const held=audio?.unresolved_requests?.[0];
  const activeClip=footage?.shots?.find(s=>s.requests.some(r=>r.running));
  const heldClip=footage?.shots?.flatMap(s=>s.requests).find(r=>r.reservation&&['submitting','submitted','pending_billing'].includes(r.reservation.state));
  if(signatureUpdating){text='Your signature is recorded. Updating the saved state before choosing the next step.';}
  else if(activeClip){text='A clip is being made. Wait for it to finish before reviewing another purchase.';find=()=>document.querySelector('[aria-label="Footage workspace"]');}
  else if(held){
    identity=held.reservation.reservation_id;
    const resolution=rows.find(r=>r.kind==='audio_resolution'&&r.entity_id===held.reservation.reservation_id);
    if(resolution?.state==='pending'&&!decided.has(resolution.request_id)){gate=resolution.request_id;text='Your account-outcome review is ready. Open this signing request and review it before signing.';}
    else {const signed=resolution&&(resolution.state!=='pending'||decided.has(resolution.request_id));const label=signed?'Apply the signed account outcome':'Review and sign this account outcome';
      text=`Paid work is stopped by an unresolved charge. Use “${label}” in the audio account review.`;find=()=>target('.voice-panel',label);}
  }else if(heldClip){
    const resolution=rows.find(r=>r.kind==='audio_resolution'&&r.entity_id===heldClip.reservation.reservation_id);
    if(resolution?.state==='pending'&&!decided.has(resolution.request_id)){gate=resolution.request_id;text='Paid work is stopped by an unresolved footage charge. Review and sign this account outcome, then apply it in the shot.';}
    else {const label=heldClip.signed_outcome?'Apply signed account outcome':'Review account outcome';text=`Paid work is stopped by an unresolved footage charge. Use “${label}” in the shot’s Footage card. Nothing will be submitted again.`;find=()=>target('.footage-controls',label);}
  }else if(state.stages?.length&&state.stages.every(s=>s.status==='completed')){text='Your trailer is finished. Open Delivery workspace to watch or show the approved file in Finder.';find=()=>target('[aria-label="Trailer workspaces"]','Delivery workspace');
  }else if(pending){gate=pending.request_id;text=['audio_spend','footage_spend'].includes(pending.kind)?'A purchase is waiting. Review its exact words, service and highest charge. Your signature permits one paid call only.':'A request is waiting for your signature. Open it to review exactly what it allows. Signing these settings or source material buys nothing.';
  }else if(state.stages?.find(s=>s.name==='canon_ingest')?.status!=='completed'){
    text='Supply the story in Story workspace, then use “Save story for review”. Saving approves nothing; review and sign the saved story next.';
    find=()=>target('.story-panel','Exact story text');
  }else if(!audio){
    text='Reading the current audio and spending record before choosing the next step.';
  }else if(!audio?.price_settings_signed){
    text='Open “Audio account consent and price ceilings”. Fill the account-confirmed figures, then use “Prepare price settings for signing”. The panel starts open until prices are signed.';
    find=()=>{const summary=target('.voice-panel','Audio account consent and price ceilings');if(summary)summary.parentElement.open=true;return target('.voice-panel','Dollar replacement value per credit');};
  }else if(!current?.paper_cut?.shots?.length){
    text='Plan the trailer in Paper cut workspace. Use “Add shot”; add a spoken line if you want speech, then save a new version.';
    find=()=>target('[aria-label="Trailer paper cut"]','Add shot');
  }else if(current.paper_cut_pending){
    text='The changed paper cut kept later editing work. Review it in Cutting workspace, then use “Save cut” before making more material.';
    find=()=>target('[aria-label="Cutting view"]','Save cut');
  }else if(!paper.usable_downstream){
    text='Use “Check paper cut against approved story”. Save any changed words first. This comparison approves no purchase.';
    find=()=>target('[aria-label="Trailer paper cut"]','Check paper cut against approved story');
  }else {
    const waiting=audio?.takes?.find(t=>t.status==='Waiting for your decision');
    const ready=audio?.requests?.find(r=>r.approved&&!r.error&&!audio.takes.some(t=>t.approved_request_id===r.record.request_id));
    if(waiting){identity=waiting.id;text='Play the full take, then use “Keep this take” or decline it permanently. Your decision needs its own signature.';find=()=>target(`[data-take-id="${waiting.id}"]`,'Keep this take');}
    else if(ready){identity=ready.record.request_id;text='Your spending card is signed. Use “Make this approved take” to buy this one take.';find=()=>target(`[data-audio-request="${ready.record.request_id}"]`,'Make this approved take');}
    else if(current.paper_cut.lines?.length&&!audio?.takes?.some(t=>t.kind==='speech'&&t.status==='Accepted')){
      if(!audio?.catalogue_loaded&&!audio?.choices?.some(c=>c.voice)){text='Use “Load account voices” to read the connected account’s voice list. This buys no speech and sends no story.';find=()=>target('.voice-panel','Load account voices');}
      else {const choice=audio.choices?.find(c=>c.index===0);const label=!choice?.voice?'Voice for line 1':!choice.rights?'I have permission to use this voice.':'Prepare a spending card';
        text=label==='Prepare a spending card'?'Use “Prepare a spending card” beside the saved line. Review its maximum charge, then sign that one request.':label==='Voice for line 1'?'Choose “Voice for line 1” beside the saved words. Your previous voice choice is remembered across a reload.':'Confirm “I have permission to use this voice.” for the chosen voice before preparing its spending card.';
        find=()=>target('[data-voice-line="0"]',label);}
    }else if(!setup?.price_settings_signed||!setup?.footage_prices||!setup?.approved_looks?.length||footage?.shots?.some(s=>!s.references.length)){text='Open Footage setup. Sign the location look, import and sign its reference picture, assign it to the shot, and sign current footage prices before requesting clips.';find=()=>target('[aria-label="Trailer workspaces"]','Footage setup');}
    else if(state.stages?.find(s=>s.name==='script')?.status!=='completed'){
      const signed=paper.whole_review_status==='Signed by you',label=signed?'Record the signed paper-cut review':'Review and sign the whole paper cut';
      text=`Use “${label}” in Paper cut workspace. This review covers the beats, exact lines, cards and shot list together; it buys nothing.`;find=()=>target('[aria-label="Trailer paper cut"]',label);
    }else {
      const shot=footage?.shots?.find(s=>!s.takes.some(t=>t.status==='Accepted'));
      const obsolete=shot?.requests.find(r=>r.signing_refusal);
      if(shot){identity=shot.id;text=obsolete?`${shot.label} needs a fresh spending card. Set aside its unsigned request, then prepare a new footage request from the current figures.`:`Open ${shot.label} in Footage. Prepare and sign one request, then watch its clip and sign keeping or declining it.`;find=()=>target(`[aria-label="${shot.label} footage"]`,obsolete?'Set aside unsigned request':shot.requests.some(r=>r.approved&&!r.reservation)?'Make signed clip':shot.takes.some(t=>t.waiting)?'Keep this take':shot.takes.length?'Create a retake request':'Create footage request');}
      else if(!current.cutting_view||current.paper_cut_pending||!current.timeline?.picture?.length){text='Open Cutting workspace. Place kept pictures and sound, trim them, then Save cut. Saving finishes no stage.';find=()=>target('[aria-label="Trailer workspaces"]','Cutting workspace');}
      else {text='Open Delivery workspace. Review and sign this saved rough cut, check colour and mix, then make, watch and sign the final file. Each review remains separate.';find=()=>target('[aria-label="Trailer workspaces"]','Delivery workspace');}
    }
  }
  // Avoid replacing a focused guidance button on an unrelated board refresh.
  const key=JSON.stringify([text,gate,identity]);if(node.dataset.contentKey===key)return;node.dataset.contentKey=key;
  node.replaceChildren(el('h2',{},'Next step'),el('p',{},text),actionButton(gate?'Open this signing request':'Go to this step',async()=>{
    if(gate){await openSignature(gate);return;}
    const control=find?.();if(!control)return 'This control is still loading. Try Go to this step again.';
    control.scrollIntoView({block:'center',behavior:'instant'});control.focus({preventScroll:true});
  }));
}
