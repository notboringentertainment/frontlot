import {el,CAPABILITY_TOKEN_KEY} from '/ui/lib.js';
let active;
export function renderCutting(s){
 if(active?.id===s.project_id)return active.node;
 const url=`/api/project/${encodeURIComponent(s.project_id)}/cut`,node=el('section',{class:'paper-editor','aria-label':'Cutting view'}),message=el('p',{role:'status'}),body=el('div',{}),player=el('video',{controls:'',preload:'metadata',style:'width:100%;max-height:400px','aria-label':'Play saved cut'});
 let data,dirty=false,busy=false;
 async function api(path='',value){const r=await fetch(url+path,value===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','x-frontlot-capability':sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||''},body:JSON.stringify(value)});const v=await r.json();if(!r.ok)throw Error(v.detail||'Reopen the cut and try again.');return v;}
 const button=(label,fn)=>el('button',{type:'button',onclick:async()=>{if(busy)return;busy=true;try{await fn();}catch(e){message.textContent=e.message;}finally{busy=false;}}},label);
 async function reopen(){data=await api();dirty=false;draw();message.textContent=`Saved version ${data.number||1} opened. Saving or playing does not approve it.`;}
 function draw(){body.replaceChildren();
  for(const kind of ['picture','audio']){
   const catalog=kind==='picture'?data.pictures:data.sounds,section=el('section',{class:'paper-panel'},el('h3',{},kind==='picture'?'Picture order and trims':'Layered sound'));
   const select=el('select',{'aria-label':kind==='picture'?'Kept picture take':'Kept sound take'});
   catalog.forEach((c,i)=>select.append(el('option',{value:i},c.label)));
   section.append(select,button(kind==='picture'?'Add picture':'Add sound',()=>{const c=catalog[Number(select.value)];if(!c)throw Error('Keep a take in its workspace first, then reopen this cut.');
    const rows=data.timeline[kind],start=kind==='picture'?rows.reduce((n,r)=>Math.max(n,r.start_frame+r.source_out_frame-r.source_in_frame),0):0;
    rows.push({take_id:c.take_id,...(kind==='picture'?{shot_id:c.shot_id}:{}),start_frame:start,source_in_frame:0,source_out_frame:c.frames,...(kind==='audio'?{level_db:-6,fade_in_frames:0,fade_out_frames:0}:{})});dirty=true;draw();}));
   data.timeline[kind].forEach((row,i)=>{
    const box=el('div',{class:'paper-panel'},el('h4',{},`${kind==='picture'?'Picture':'Sound'} ${i+1}`));
    const fields=[['Starts at frame','start_frame'],['Source in frame','source_in_frame'],['Source out frame','source_out_frame'],...(kind==='audio'?[['Level in decibels','level_db'],['Fade in frames','fade_in_frames'],['Fade out frames','fade_out_frames']]:[])];
    for(const [label,key] of fields){const input=el('input',{type:'number','aria-label':`${label} for ${kind} ${i+1}`,step:key==='level_db'?'.1':'1'});input.value=row[key];input.oninput=()=>{row[key]=Number(input.value);dirty=true;};box.append(el('label',{},label,input));}
    box.append(button('Remove this clip',()=>{data.timeline[kind].splice(i,1);dirty=true;draw();}));section.append(box);
    if(kind==='picture')for(const [label,delta] of [['Move earlier',-1],['Move later',1]])box.append(button(label,()=>{const rows=data.timeline.picture,j=i+delta;if(j<0||j>=rows.length)return;[rows[i],rows[j]]=[rows[j],rows[i]];let frame=0;for(const r of rows){r.start_frame=frame;frame+=r.source_out_frame-r.source_in_frame;}dirty=true;draw();}));
   });body.append(section);
  }
 }
 node.append(el('h2',{},'Cutting view'),el('p',{},'Build from kept takes. Timing uses frames: twenty-four frames make one second. Picture clips cannot overlap; sounds can. Generated music remains for the test trailer only. This is a local rough preview, not an approved or release-ready file.'),message,player,
 button('Reopen saved cut',reopen),button('Save cut',async()=>{data=await api('',{timeline:data.timeline,revision:data.revision});dirty=false;draw();message.textContent=`Version ${data.number} saved. Nothing was approved.`;}),
 button('Prepare saved playback',async()=>{if(dirty)throw Error('Save the cut before preparing playback.');message.textContent='Preparing picture and layered sound locally…';const v=await api('/preview',{revision:data.revision});player.src=v.url;message.textContent='Saved preview ready. Play, pause or seek below. This is not final approval.';}),body);
 active={id:s.project_id,node};reopen().catch(e=>message.textContent=e.message);return node;
}
