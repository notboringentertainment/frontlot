import {el,CAPABILITY_TOKEN_KEY} from '/ui/lib.js';
let current=null;
const money=v=>`$${Number(v||0).toFixed(4)}`;
const button=(label,fn)=>el('button',{type:'button',onclick:fn},label);
async function api(url,data){
  const r=await fetch(url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','x-frontlot-capability':sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||''},body:JSON.stringify(data)});
  const value=await r.json();if(!r.ok)throw Error(value.detail||'Voice could not be read. Reload the board and try again.');return value;
}
export function renderVoice(s,onSign){
  if(current?.project===s.project_id){current.refresh();return current.node;}
  const project=s.project_id,url=`/api/project/${encodeURIComponent(project)}/voice`;
  const node=el('section',{class:'voice-panel paper-editor','aria-label':'Trailer voice'}),message=el('p',{role:'status','aria-live':'polite'}),content=el('div',{});
  const drafts=new Map(),players=new Map(),prices={},outcomes=new Map();let voices=[],last='',busy=false,loading=false,pendingRefresh=false,pricesOpen=false;
  const act=async fn=>{if(busy)return;busy=true;message.textContent='Working…';try{const result=await fn();message.textContent=typeof result==='string'?result:'Saved. Review the request before signing.';}catch(e){message.textContent=e.message;}finally{busy=false;last='';refresh();}};
  node.append(el('h2',{},'Voice'),el('p',{},'Choose a voice for each saved spoken line. One signature buys one take. Keeping or declining a take needs its own signature; a decline is final.'),
    button('Load account voices',()=>act(async()=>{voices=await api(url+'/catalogue',{});return 'Account voices loaded. Choose a voice for the saved line.';})),
    el('p',{class:'source'},'Loading voices reads the connected account only. It sends no story and buys no speech.'),message,content);
  function draw(data){
    content.replaceChildren();
    const b=data.budget;
    content.append(el('section',{class:'paper-panel voice-budget'},el('h3',{},'One production ceiling'),
      el('p',{},`${money(b.spent)} spent · ${money(b.held)} held · ${money(b.remaining)} remaining of ${money(b.ceiling)}`),
      el('p',{},`Recorded audio: ${money(b.audio_cash)} new cash · ${money(b.audio_credits)} replacement value of credits. Credits are counted once in the production total.`)));
    const settings=el('details',{class:'paper-panel'},el('summary',{},'Audio account consent and price ceilings'),
      el('p',{},'Enter ceilings verified for your account, not plan averages. No default prices are supplied. Signing these settings permits separately approved voice words to reach ElevenLabs; it buys no take. Saving a new draft pauses paid work until you sign it. Previous signed settings are kept.'),
      el('p',{},'The music rate is recorded for the later music controls. This panel grants permission for speech only.'));
    settings.open=pricesOpen;settings.ontoggle=()=>{if(settings.isConnected)pricesOpen=settings.open;};
    const fields=[['Dollar replacement value per credit','dollars_per_credit'],['Highest speech credits per character','speech_credits_per_character'],['Highest music credits per minute','music_credits_per_minute'],['Highest voice rate multiplier','voice_multiplier_ceiling'],['Prices expire','expiry']];
    for(const [label,key] of fields){const input=el('input',{type:key==='expiry'?'datetime-local':'number','aria-label':label,step:'any'});input.value=prices[key]||'';input.oninput=()=>prices[key]=input.value;settings.append(el('label',{},label,input));}
    settings.append(button('Prepare price settings for signing',()=>act(async()=>{
      const body={expires_at:Date.parse(prices.expiry)/1000};for(const [,k] of fields)if(k!=='expiry')body[k]=Number(prices[k]);
      const g=await api(url+'/prices',body);onSign(g.request_id);
    })),button('Restore previous signed settings',()=>act(async()=>{return (await api(url+'/prices/restore',{})).message;})));content.append(settings);
    if(data.message)content.append(el('p',{role:'alert'},data.message));
    for(const item of data.unresolved_requests){
      const id=item.reservation.reservation_id,r=item.request,form=el('section',{class:'paper-panel'},el('h3',{},'Review an unresolved audio charge'),
        el('p',{role:'alert'},'Paid work is stopped. Check your ElevenLabs account; time passing will not resolve this charge.'),el('blockquote',{},r?.prompt||'The original signed request cannot be verified. Stop and investigate its approval history.'));
      const d=outcomes.get(id)||{charged:false,cash:0,credits:0,note:''};outcomes.set(id,d);
      const charged=el('input',{type:'checkbox'});charged.checked=d.charged;charged.onchange=()=>d.charged=charged.checked;form.append(el('label',{},charged,'The account was charged.'));
      for(const [label,key] of [['Actual new cash dollars','cash'],['Actual credit replacement dollars','credits'],['What I checked in the account','note']]){
        const field=el(key==='note'?'textarea':'input',key==='note'?{'aria-label':label}:{'aria-label':label,type:'number',step:'.0001'});field.value=d[key];field.oninput=()=>d[key]=key==='note'?field.value:Number(field.value);form.append(el('label',{},label,field));
      }
      form.append(button('Review and sign this account outcome',()=>act(async()=>{const g=await api(url+'/resolution',{reservation_id:id,...d});onSign(g.request_id);})),
        button('Apply the signed account outcome',()=>act(async()=>{return (await api(`${url}/resolution/${id}/apply`,{})).message;})));content.append(form);
    }
    const protectedBox=el('aside',{class:'paper-panel'},el('h3',{},'Protected story lines'));
    for(const line of data.protected_lines)protectedBox.append(el('blockquote',{},line));
    if(!data.protected_lines.length)protectedBox.append(el('p',{},'No protected wording is recorded in the approved story.'));
    const lineArea=el('div',{class:'voice-lines'}),lineForms=el('div',{});lineArea.append(protectedBox,lineForms);content.append(lineArea);
    data.lines.forEach((line,index)=>{
      const d=drafts.get(index)||{voice:'',notes:'',rights:false};drafts.set(index,d);
      const box=el('section',{class:'paper-panel','data-voice-line':index},el('h3',{},`Spoken line ${index+1}`),el('blockquote',{},line.text),
        el('p',{},`${line.duration_frames/24} seconds · starts at ${line.start_frame/24} seconds`));
      const select=el('select',{'aria-label':`Voice for line ${index+1}`},el('option',{value:''},'Choose an account voice'));
      voices.forEach(v=>select.append(el('option',{value:v.id},v.name)));select.value=d.voice;select.onchange=()=>{d.voice=select.value;d.rights=false;rights.checked=false;};
      const notes=el('textarea',{'aria-label':`Pronunciation and delivery notes for line ${index+1}`,maxlength:2000});notes.value=d.notes;notes.oninput=()=>d.notes=notes.value;
      const rights=el('input',{type:'checkbox'});rights.checked=d.rights;rights.onchange=()=>d.rights=rights.checked;
      const prepare=button('Prepare a spending card',()=>act(async()=>{await api(url+'/prepare',{line_index:index,voice:d.voice,notes:d.notes,rights_confirmed:d.rights});}));prepare.disabled=!data.usable;
      box.append(el('label',{},'Account voice',select),el('label',{},'Pronunciation and delivery notes',notes),
        el('p',{class:'source'},'These notes stay with the signed take for your review. This speech model does not receive direction notes. The story material sent is the saved line’s exact wording; model and voice choices are sent too.'),
        el('label',{},rights,'I have permission to use this voice.'),el('p',{},'ElevenLabs · Multilingual v2 · the saved line’s exact words'),prepare);
      lineForms.append(box);
    });
    const requests=el('section',{class:'paper-panel'},el('h3',{},'Voice spending cards'));
    for(const item of [...data.requests].reverse()){
      const r=item.record,card=el('article',{class:'voice-spend paper-panel'},el('h4',{},item.approved?'Signed request':'Waiting for your signature'),
        el('p',{},`ElevenLabs · ${r.model} · ${r.binding.voice_label||r.voice}`),el('blockquote',{},r.prompt),el('p',{},`Line slot: ${r.duration_seconds} seconds. The service determines the take’s actual length; listen before keeping it.`),
        el('p',{},`Highest new cash: ${money(r.max_cash_usd)} · highest credit replacement value: ${money(r.max_credits_usd)}`),
        el('p',{},`Price expires ${new Date(r.price_expires_at*1000).toLocaleString()}`),
        el('p',{},`Notes kept here, not sent: ${r.binding.performance_notes||'None'}`));
      const made=data.takes.some(t=>t.approved_request_id===r.request_id);
      if(item.error)card.append(el('p',{role:'alert'},item.error));
      else if(!made && item.approved)card.append(button('Make this approved take',()=>act(async()=>{const result=await api(`${url}/${r.request_id}/send`,{});if(!result.success)throw Error(result.message);})));
      else if(!made && item.gate_request_id)card.append(button('Review and sign this request',()=>onSign(item.gate_request_id)));
      if(made)card.append(el('p',{},'This signature already made a take. Another take needs another spending card and signature.'));
      requests.append(card);
    }
    content.append(requests);
    const takes=el('section',{class:'paper-panel'},el('h3',{},'Voice takes'));
    for(const t of data.takes){
      const box=el('article',{class:'voice-take paper-panel'},el('h4',{},t.status==='Accepted'?'Kept':t.status),
        el('p',{},`Spoken line ${t.binding.line_index+1} · ${t.voice_name} · ${t.duration_seconds.toFixed(2)} seconds`),el('blockquote',{},t.spoken_words));
      let player=players.get(t.id);if(!player){player=el('audio',{controls:'',preload:'metadata',src:`/api/project/${encodeURIComponent(project)}/audio-take/${t.id}/play`,'aria-label':`Listen to line ${t.binding.line_index+1}`});players.set(t.id,player);}
      box.append(player);
      if(t.status!=='Declined'){
        if(t.status==='Waiting for your decision')box.append(button('Keep this take',()=>act(async()=>{const g=await api(`${url}/${t.id}/decision`,{decision:'accept'});onSign(g.request_id);})));
        if(t.status==='Needs a fresh look')box.append(el('p',{},'The line or file changed. Prepare a new spending card for a new take; this take cannot be kept for changed words.'));
        box.append(button('Decline this take permanently',()=>act(async()=>{const g=await api(`${url}/${t.id}/decision`,{decision:'decline'});onSign(g.request_id);})));
      }else box.append(el('p',{},'Decline is final. Ask for another take with its own spending card.'));
      box.append(button('Ask for another take',()=>{const target=node.querySelector(`[data-voice-line="${t.binding.line_index}"]`);target?.scrollIntoView({block:'center'});message.textContent='Prepare another spending card for this line. A new signature is required.';}));takes.append(box);
    }
    content.append(takes);
    content.append(el('section',{class:'paper-panel'},el('h3',{},'Music'),el('p',{},'Music generation, licensed-file import and music take controls are not built in this panel yet.')));
  }
  async function refresh(){
    if(loading||busy){pendingRefresh=true;return;}loading=true;pendingRefresh=false;
    try{const data=await api(url),key=JSON.stringify(data)+JSON.stringify(voices);if(key!==last){last=key;draw(data);}}
    catch(e){message.textContent=e.message+' Check the approved story and signed audio price settings, then reload.';}
    finally{loading=false;if(pendingRefresh&&!busy){pendingRefresh=false;refresh();}}
  }
  current={project,node,refresh};refresh();return node;
}

export function renderAudioSigning(detail,actions,onReturn,stale){
  const r=detail.packet?.record;
  const box=el('section',{class:'viewer-inner paper-editor'},el('h2',{},'Review and sign audio'));
  box.append(button('Return to voice',onReturn));
  if(stale){box.append(el('p',{},'This request has been decided. Return to voice to continue.'));return box;}
  if(!r){box.append(el('p',{role:'alert'},detail.packet?.error||'The current audio evidence is unavailable. Return to voice and prepare a new request before signing.'));return box;}
  if(detail.packet.audio_prices){
    box.append(el('h3',{},'Account consent and maximum audio prices'),el('p',{},'This signature buys no audio. It permits ElevenLabs to receive voice words only after you sign an individual request. All other saved production settings are part of this same settings signature.'));
    const p=detail.packet;
    box.append(el('p',{},`Production ceiling: ${money(p.production_ceiling)}. Run limit: ${p.run_minutes} minutes. Cast ceiling: ${p.cast_ceiling.characters} characters and ${p.cast_ceiling.locations} places.`),
      el('p',{},`Saved footage model: ${p.footage_model==='not-selected'?'Not selected':p.footage_model}`),
      el('p',{},p.picture_judge?`Saved picture judge: ${p.picture_judge.judge_provider} / ${p.picture_judge.judge_model}`:'No paid picture judge is selected.'));
    const classes={prompts:'text prompts',reference_images:'reference pictures',generated_sheet_images:'generated character sheets',speech_text:'spoken words',music_prompt:'music descriptions',effect_prompt:'sound-effect descriptions'};
    for(const permission of p.outbound_permissions)box.append(el('p',{},`${permission.provider}: may receive ${permission.content_classes.map(x=>classes[x]).join(', ')}. Paid requests still need their individual approval.`));
    const labels={dollars_per_credit:'Dollar replacement value per credit',speech_credits_per_character:'Highest speech credits per character',music_credits_per_minute:'Highest music credits per minute',voice_multiplier_ceiling:'Highest voice rate multiplier'};
    for(const [k,v] of Object.entries(detail.packet.audio_prices))box.append(el('p',{},`${labels[k]}: ${v.value}. Set ${new Date(v.set_at*1000).toLocaleString()}; expires ${new Date(v.expires_at*1000).toLocaleString()}.`));
  }else if(r.reservation_id){box.append(el('h3',{},'Record the account outcome'),el('blockquote',{},r.request.prompt),el('p',{},r.charged?'Charged':'Not charged'),el('p',{},`Cash ${money(r.cash_usd)} · credit replacement value ${money(r.credits_usd)}`),el('p',{},r.note),el('p',{},'This does not submit the request again or invent a take.'));
  }else if(r.take){box.append(el('h3',{},r.decision==='accept'?'Keep this take':'Decline this take permanently'),el('p',{},'This signs your decision about the exact saved audio. A decline is final.'),
    el('blockquote',{},detail.packet.spoken_words),
    el('audio',{controls:'',src:`/api/project/${encodeURIComponent(r.take.project_id)}/audio-take/${r.take.id}/play`}),el('p',{},r.take.binding?.performance_notes||'No direction notes.'));
  }else{box.append(el('p',{},`ElevenLabs · ${r.model} · ${r.binding.voice_label||r.voice}`),el('blockquote',{},r.prompt),
    el('p',{},`Line slot: ${r.duration_seconds} seconds · highest cash ${money(r.max_cash_usd)} · highest credit replacement value ${money(r.max_credits_usd)}`),
    el('p',{},`Bound expires ${new Date(r.price_expires_at*1000).toLocaleString()}`),el('p',{},'One signature buys one call. No retries or another provider.'),
    el('p',{},`Notes stay here and are not sent: ${r.binding.performance_notes||'None'}`));}
  box.append(actions);return box;
}
