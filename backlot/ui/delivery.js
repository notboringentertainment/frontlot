import {el,CAPABILITY_TOKEN_KEY} from '/ui/lib.js';
let active=null;
const reviews=new Map();
export function refreshDelivery(){return active?.refresh(true);}

export function renderDeliverySigning(detail,actions,onReturn){
 const id=detail.request_id,packet=detail.packet||{};
 let held=reviews.get(id);
 if(!held){
  const video=el('video',{controls:'',preload:'auto',src:packet.play_url,'aria-label':'Watch this exact delivery review',style:'width:100%;max-height:480px'});
  const heading=el('h2',{}),controls=el('div',{}),words=el('div',{}),node=el('section',{class:'paper-editor',role:'region','aria-label':'Delivery review'},
   heading,
   el('p',{},'Watch and listen to the whole file before starting your signature. A technical check never replaces your approval.'),video,words,controls,
   el('button',{type:'button',onclick:onReturn},'Return to delivery'));
  held={node,heading,video,controls,words,watched:false};reviews.set(id,held);
  video.onended=()=>{held.watched=true;for(const b of controls.querySelectorAll('button.gate-primary'))b.disabled=!held.ready;};
 }
 const stage=detail.request?.stage;
 held.heading.textContent=stage==='edit'?'Review the saved rough cut':stage==='compose'?'Review this exact final file':'Preparing this saved review';
 held.ready=!!packet.play_url&&!packet.packet_error;
 const source=packet.packet_error?'':(packet.play_url??held.video.getAttribute('src')??'');
 if((held.video.getAttribute('src')||'')!==source){
  held.video.pause();held.watched=false;
  if(source)held.video.setAttribute('src',source);else held.video.removeAttribute('src');
  held.video.load();
 }
 held.words.replaceChildren(...(packet.messages||[]).map(s=>el('p',{},s)));
 if(packet.error)held.words.append(el('p',{role:'alert'},packet.error));
 held.controls.replaceChildren(actions);
 if(!held.watched||!held.ready)for(const b of held.controls.querySelectorAll('button.gate-primary'))b.disabled=true;
 return held.node;
}

export function renderDelivery(s,onSign){
 if(active?.id===s.project_id){active.refresh();return active.node;}
 const base=`/api/project/${encodeURIComponent(s.project_id)}/delivery`,node=el('section',{class:'paper-editor',role:'region','aria-label':'Trailer delivery'}),message=el('p',{role:'status'},'Checking the saved cut and its signed takes…'),body=el('div',{});
 let data,busy=false,loading=false,pendingRefresh=false,key='';
 async function api(path='',value){const r=await fetch(base+path,value===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','x-frontlot-capability':sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||''},body:JSON.stringify(value)});const v=await r.json();if(!r.ok)throw Error(v.detail||'Reopen the saved delivery controls and try again.');return v;}
 function button(label,fn,disabled=false){return el('button',{type:'button',disabled:disabled?'':null,onclick:async()=>{if(busy)return;busy=true;try{await fn();}catch(e){message.textContent=e.message;}finally{busy=false;await refresh(true);}}},label);}
 async function refresh(force=false){if(loading){if(force)pendingRefresh=true;return;}if(busy&&!force)return;loading=true;pendingRefresh=false;try{const next=await api(),encoded=JSON.stringify(next);if(encoded!==key){const first=!data;data=next;key=encoded;draw();if(first)message.textContent='Saved cut loaded. Review its current approvals below.';}}catch(e){message.textContent=e.message;for(const v of node.querySelectorAll('video')){v.pause();v.removeAttribute('src');v.load();}for(const b of body.querySelectorAll('button'))b.disabled=true;}finally{loading=false;if(pendingRefresh&&!busy){pendingRefresh=false;void refresh(true);}}}
 function draw(){body.replaceChildren();
  body.append(el('p',{},`Saved version ${data.number}. Delivery uses this saved cut, including its colour, sound and signed cards. Save any editing changes before reviewing it.`),
   el('h3',{},'Rough-cut approval'),el('p',{},data.rough_status),
   button('Review and sign the saved rough cut',async()=>{message.textContent='Preparing the saved rough cut for review locally…';const r=await api('/rough',{revision:data.revision});await onSign(r.gate_request_id);}),
   button('Make the final file',async()=>{message.textContent='Making and measuring the full-size file locally. Nothing is sent or spent…';const r=await api('/export',{revision:data.revision});message.textContent='The final file is ready to watch. '+r.stage_message;},data.rough_status!=='Signed by you'));
  for(const item of data.exports){
   const review=item.review,usable=['Needs your signature','Approved by you','Sound check needs changes'].includes(item.status);
   const tile=el('section',{class:'paper-panel','data-delivery':item.id},el('h3',{},'Final trailer file'),el('p',{},item.status),
    el('p',{},review.metadata.test_only?'Test trailer only. Generated ElevenLabs music is not cleared for release until ElevenLabs confirms otherwise in writing.':'Local trailer file. Your signature approves this file, not a legal clearance of its material.'),
    el('p',{},'1920 by 1080 · widescreen · twenty-four frames per second · stereo sound.'),
    el('video',{controls:'',preload:'metadata',...(usable?{src:base+'/'+item.id+'/play'}:{}),'aria-label':'Play exported trailer',style:'width:100%;max-height:480px'}));
   for(const words of review.issues_found)tile.append(el('p',{},words));
   const measured=review.metadata.measurements,figures=el('details',{},el('summary',{},'Measured figures from this final file'));
   figures.append(el('p',{},`Duration: ${review.checks.technical_probe.duration_seconds.toFixed(3)} seconds. File size: ${review.checks.technical_probe.file_size_bytes.toLocaleString()} bytes.`),
    el('p',{},`Overall loudness: ${measured.integrated_lufs??'not measurable'} LUFS; the target is about minus sixteen. Highest playback peak: ${measured.true_peak_dbtp??'not measurable'} dBTP; it must stay below minus one.`),
    el('p',{},`${review.metadata.boundary_samples.length} pictures sampled at cut and card boundaries. Watch them for story, faces and lettering; the automatic checks do not judge those.`));tile.append(figures);
   tile.append(button('Review and sign final approval',async()=>{message.textContent='Rechecking the exact final file and its sound…';const r=await api('/'+item.id+'/approval',{});await onSign(r.gate_request_id);},item.status!=='Needs your signature'),
    button('Record the signed final approval',async()=>{const r=await api('/'+item.id+'/apply',{});message.textContent=r.message;},item.status!=='Approved by you'),
    button('Show approved file in Finder',async()=>{const r=await api('/'+item.id+'/reveal',{});message.textContent=r.message+' '+r.path;},item.status!=='Approved by you'));
   body.append(tile);
  }
 }
 node.append(el('h2',{},'Delivery'),el('p',{},'Sign the saved rough cut, make the local final file, watch it, then sign that exact file. Nothing is uploaded. No stage is finished without its own signature and required earlier reviews.'),message,
 button('Refresh delivery',async()=>{key='';await refresh(true);}),body);
 active={id:s.project_id,node,refresh};refresh();return node;
}
