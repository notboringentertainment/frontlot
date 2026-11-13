import {el,CAPABILITY_TOKEN_KEY} from '/ui/lib.js';
let active;
export function refreshCutting(){return active?.reopen();}
export function renderCardSigning(detail,actions,onReturn){
 const c=detail.packet?.title_card;
 return el('section',{'aria-label':'Exact card review',class:'paper-editor'},el('h2',{},'Review these exact card words'),
  c?el('div',{},el('pre',{style:'white-space:pre-wrap;font:inherit'},c.text),
   el('p',{},`Patrick Hand · letter size ${c.font_size} pixels. Starts at frame ${c.start_frame} (${(c.start_frame/24).toFixed(3)} seconds); lasts ${c.duration_frames} frames (${(c.duration_frames/24).toFixed(3)} seconds).`),
   el('p',{},c.background==='black'?'Shown on black.':'Shown over the picture.'),
   el('img',{src:detail.packet.card_image,alt:'Exact local lettering preview',style:'width:100%;background:#000'})):
   el('p',{role:'alert'},detail.packet?.error||'This card could not be verified. Save the cut and prepare a new review.'),
  el('p',{},'Your signature binds these words, their font and timing. It permits no spending and completes no stage.'),
  actions,el('button',{type:'button',onclick:onReturn},'Return to cutting view'));
}
export function renderCutting(s,onSign){
 if(active?.id===s.project_id){active.refresh();return active.node;}
 const url=`/api/project/${encodeURIComponent(s.project_id)}/cut`,node=el('section',{class:'paper-editor','aria-label':'Cutting view'}),message=el('p',{role:'status'}),body=el('div',{}),player=el('video',{controls:'',preload:'metadata',style:'width:100%;max-height:400px','aria-label':'Play saved cut'});
 let data,dirty=false,busy=false;
 let loading=false,lastSnapshot='';
 const gapNote=el('p',{'data-gap-note':'',role:'note'});
 const closeGaps=button('Close picture gaps',()=>{if(data.timeline.picture.some(r=>![r.source_in_frame,r.source_out_frame].every(Number.isInteger)||r.source_out_frame<=r.source_in_frame))throw Error('Fix the picture trims before closing gaps. No clip was moved.');let frame=0;for(const row of data.timeline.picture){row.start_frame=frame;frame+=row.source_out_frame-row.source_in_frame;}changed();draw();message.textContent='Picture gaps closed. Sound stays where you placed it; check its ending before saving.';});
 function clearPlayback(){player.pause();player.removeAttribute('src');player.load();}
 function changed(){dirty=true;clearPlayback();updateGaps();}
 function updateGaps(){if(!data)return;let end=0,gaps=0;for(const r of data.timeline.picture){gaps+=Math.max(0,r.start_frame-end);end=r.start_frame+r.source_out_frame-r.source_in_frame;}closeGaps.disabled=gaps===0;gapNote.textContent=gaps?`Shortening or moving a picture leaves a gap: ${gaps} frames (${(gaps/24).toFixed(3)} seconds) of black. Press Close picture gaps to pull the pictures together. Sound will stay where you placed it.`:'No picture gaps.';}
 function numberField(box,row,key,label,subject,onChange=changed){
  const input=el('input',{type:'number','aria-label':`${label} for ${subject}`,step:key==='level_db'?'.1':'1'});input.value=row[key];
  if(key==='level_db'){input.oninput=()=>{row[key]=Number(input.value);onChange();};box.append(el('label',{},label,input));return;}
  const secondsLabel=label.replace('frames','seconds').replace('frame','seconds');
  const seconds=el('input',{type:'number',min:'0',step:'any','aria-label':`${secondsLabel} for ${subject}`});seconds.value=(row[key]/24).toFixed(3);
  const shown=el('span',{'data-seconds-for':`${subject}:${key}`},`${(row[key]/24).toFixed(3)} seconds`);
  input.oninput=()=>{row[key]=Number(input.value);seconds.value=(row[key]/24).toFixed(3);shown.textContent=`${seconds.value} seconds`;onChange();};
  seconds.oninput=()=>{dirty=true;clearPlayback();};
  seconds.onchange=()=>{const n=Number(seconds.value);if(!seconds.value||!Number.isFinite(n)||n<0){row[key]=NaN;input.value='';shown.textContent='No time set. Enter valid seconds or a whole frame.';message.textContent='Enter a non-negative time in seconds, then leave the field to use the nearest whole frame.';onChange();return;}row[key]=Math.round(n*24);input.value=row[key];seconds.value=(row[key]/24).toFixed(3);shown.textContent=`${seconds.value} seconds`;onChange();message.textContent=`Time set to ${row[key]} whole frames (${seconds.value} seconds).`;};
  box.append(el('label',{},label,input,shown),el('label',{},secondsLabel,seconds));
 }
 async function api(path='',value){const r=await fetch(url+path,value===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','x-frontlot-capability':sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||''},body:JSON.stringify(value)});const v=await r.json();if(!r.ok)throw Error(v.detail||'Reopen the cut and try again.');return v;}
 function button(label,fn){return el('button',{type:'button',onclick:async()=>{if(busy)return;busy=true;try{await fn();}catch(e){message.textContent=e.message;}finally{busy=false;}}},label);}
 async function reopen(){data=await api();lastSnapshot=JSON.stringify(data);dirty=false;clearPlayback();draw();message.textContent=`Saved version ${data.number||1} opened. Saving or playing does not approve it.`;}
 async function refresh(){if(loading||dirty||busy||!data)return;loading=true;try{const next=await api(),key=JSON.stringify(next);if(dirty||busy)return;if(key!==lastSnapshot){data=next;lastSnapshot=key;clearPlayback();draw();message.textContent='The saved cut or its take decisions changed. Review any marked clip before saving or playing.';}}catch(e){message.textContent=e.message;}finally{loading=false;}}
 function identification(item){return el('div',{class:'cut-source'},item?.thumbnail?el('img',{src:item.thumbnail,alt:'Picture or waveform from this take',loading:'lazy',style:'width:160px;max-height:90px;object-fit:contain;background:#171716'}):null,el('p',{},item?.label||'Original take is no longer available. Choose a replacement below.'));}
 function draw(){body.replaceChildren();
  for(const kind of ['picture','audio']){
   const catalog=kind==='picture'?data.pictures:data.sounds,section=el('section',{class:'paper-panel'},el('h3',{},kind==='picture'?'Picture order and trims':'Layered sound'));
   const select=el('select',{'aria-label':kind==='picture'?'Kept picture take':'Kept sound take'});
   catalog.forEach((c,i)=>select.append(el('option',{value:i},c.label)));
   const selected=el('div',{});function selectedTake(){selected.replaceChildren(identification(catalog[Number(select.value)]));}select.onchange=selectedTake;selectedTake();
   section.append(select,button(kind==='picture'?'Add picture':'Add sound',()=>{const c=catalog[Number(select.value)];if(!c)throw Error('Keep a take in its workspace first, then reopen this cut.');
    const rows=data.timeline[kind],start=kind==='picture'?rows.reduce((n,r)=>Math.max(n,r.start_frame+r.source_out_frame-r.source_in_frame),0):0;
    const end=data.timeline.picture.reduce((n,r)=>Math.max(n,r.start_frame+r.source_out_frame-r.source_in_frame),0);
    const duration=kind==='picture'?Math.min(c.frames,24,data.target_frames-start):Math.min(c.frames,end);
    if(duration<1)throw Error(kind==='picture'?'No room remains under the trailer target. Trim or remove a picture before adding another.':'Add picture first, then place sound against it.');
    rows.push({take_id:c.take_id,...(kind==='picture'?{shot_id:c.shot_id}:{}),start_frame:start,source_in_frame:0,source_out_frame:duration,...(kind==='audio'?{level_db:-6,fade_in_frames:0,fade_out_frames:0}:{})});changed();draw();message.textContent=kind==='picture'?'Picture added with up to one second of source, within the remaining target. Set its trim in seconds or frames.':'Sound added within the picture length. Set its position, level and fades.';}),selected);
   data.timeline[kind].forEach((row,i)=>{
    const item=data.clips?.[kind]?.find(c=>c.take_id===row.take_id)||catalog.find(c=>c.take_id===row.take_id);
    const box=el('div',{class:'paper-panel','data-cut-kind':kind},el('h4',{},`${kind==='picture'?'Picture':'Sound'} ${i+1}`),identification(item));
    if(item?.problem)box.append(el('p',{role:'alert'},item.problem));
    const fields=[['Starts at frame','start_frame'],['Source in frame','source_in_frame'],['Source out frame','source_out_frame'],...(kind==='audio'?[['Level in decibels','level_db'],['Fade in frames','fade_in_frames'],['Fade out frames','fade_out_frames']]:[])];
    for(const [label,key] of fields)numberField(box,row,key,label,`${kind} ${i+1}`);
    const replacement=el('select',{'aria-label':`Replacement for ${kind} ${i+1}`});catalog.forEach((c,index)=>replacement.append(el('option',{value:index},c.label)));
    box.append(el('label',{},'Replace this source with a kept take',replacement),button(`Replace ${kind} ${i+1}`,()=>{const c=catalog[Number(replacement.value)];if(!c)throw Error('Keep a replacement take first, then reopen the saved cut.');const duration=Math.min(row.source_out_frame-row.source_in_frame||24,c.frames);Object.assign(row,{take_id:c.take_id,source_in_frame:0,source_out_frame:duration,...(kind==='picture'?{shot_id:c.shot_id}:{})});changed();draw();}),button('Remove this clip',()=>{data.timeline[kind].splice(i,1);changed();draw();}));section.append(box);
    if(kind==='picture')for(const [label,delta] of [['Move earlier',-1],['Move later',1]])box.append(button(label,()=>{const rows=data.timeline.picture,j=i+delta;if(j<0||j>=rows.length)return;[rows[i],rows[j]]=[rows[j],rows[i]];let frame=0;for(const r of rows){r.start_frame=frame;frame+=r.source_out_frame-r.source_in_frame;}changed();draw();}));
   });body.append(section);
  }
  const section=el('section',{class:'paper-panel'},el('h3',{},'Exact text cards'),el('p',{},'Your words stay exactly as typed. Add your own line breaks if needed. Nothing wraps, shrinks or corrects them for you. Keep lettering inside the inner eighty percent of the picture. Each saved card needs your signature before playback.'));
  data.timeline.cards ||= [];
  data.timeline.cards.forEach((row,index)=>{
   const n=index+1,box=el('section',{class:'paper-panel','data-card':row.id}),state=el('p',{'aria-live':'polite'},data.card_states[row.id]||'Needs your signature');
   const touch=()=>{changed();state.textContent='Unsaved change — needs a fresh look and your signature.';};
   const text=el('textarea',{'aria-label':`Exact words for card ${n}`,rows:3,spellcheck:'false',autocapitalize:'off',autocorrect:'off',style:'text-transform:none;white-space:pre'});text.value=row.text;text.oninput=()=>{row.text=text.value;touch();};
   box.append(el('h4',{},`Text card ${n}`),state,el('label',{},'Exact words',text));
   for(const [label,key] of [['Starts at frame','start_frame'],['Lasts for frames','duration_frames'],['Letter size in pixels','font_size']]){
    if(key==='font_size'){
     const size=el('input',{type:'number',step:'1',min:'12',max:'72','aria-label':`Letter size in pixels for card ${n}`});size.value=row.font_size;size.oninput=()=>{row.font_size=Number(size.value);touch();};box.append(el('label',{},label,size));
    }else numberField(box,row,key,label,`card ${n}`,touch);
   }
   for(const [key,label,choices] of [['role','Card kind',[['intertitle','Intertitle'],['end','End card']]],['background','Background',[['black','On black'],['transparent','Over the picture']]]]){
    const select=el('select',{'aria-label':`${label} for card ${n}`});choices.forEach(([value,word])=>select.append(el('option',{value},word)));select.value=row[key];select.onchange=()=>{row[key]=select.value;touch();};box.append(el('label',{},label,select));
   }
   box.append(el('p',{},'Font: Patrick Hand. The supplied font is licensed for this lettering.'),button(`Review and sign card ${n}`,async()=>{if(dirty)throw Error('Save the cut first, then review and sign the card’s exact words, font and timing.');const v=await api(`/cards/${encodeURIComponent(row.id)}/approval`,{revision:data.revision});await onSign(v.gate_request_id);}),
    button(`Remove card ${n}`,()=>{data.timeline.cards.splice(index,1);changed();draw();}));section.append(box);
  });
  section.append(button('Add text card',()=>{const f=data.fonts[0];data.timeline.cards.push({id:crypto.randomUUID(),text:'',font_path:f.path,font_sha256:f.sha256,font_size:32,start_frame:0,duration_frames:24,role:'intertitle',background:'black'});changed();draw();}));body.append(section);
  const versions=el('select',{'aria-label':'Saved cut versions'});for(const v of data.versions||[])versions.append(el('option',{value:v.revision},`Version ${v.number} · ${new Date(v.saved_at).toLocaleString()}`));versions.value=data.revision;
  body.append(el('section',{class:'paper-panel'},el('h3',{},'Restore an earlier cut'),versions,button('Restore selected cut',async()=>{if(dirty)throw Error('Save or reopen your current cut before restoring another version.');data=await api('/restore',{revision:versions.value,current:data.revision});lastSnapshot=JSON.stringify(data);dirty=false;clearPlayback();draw();message.textContent='Earlier cut restored as a new saved version. Every old version remains. Any unusable take is still marked and cannot play.';})));
  updateGaps();
 }
 node.append(el('h2',{},'Cutting view'),el('p',{},'Build from kept takes. Enter seconds or frames: seconds are rounded to the nearest whole frame and shown back to you. Twenty-four frames make one second. Picture clips cannot overlap; sounds can. Generated music remains for the test trailer only. This is a local rough preview, not an approved or release-ready file.'),message,player,
 button('Reopen saved cut',reopen),button('Save cut',async()=>{message.textContent='Checking the kept takes and saving this cut…';data=await api('',{timeline:data.timeline,revision:data.revision});lastSnapshot=JSON.stringify(data);dirty=false;clearPlayback();draw();message.textContent=`Version ${data.number} saved. Nothing was approved.`;}),
 button('Prepare saved playback',async()=>{if(dirty)throw Error('Save the cut before preparing playback.');message.textContent='Preparing picture and layered sound locally…';const v=await api('/preview',{revision:data.revision});player.src=v.url;message.textContent='Saved preview ready. Play, pause or seek below. This is not final approval.';}),gapNote,closeGaps,body);
 active={id:s.project_id,node,refresh,reopen};reopen().catch(e=>message.textContent=e.message);return node;
}
