import {el,CAPABILITY_TOKEN_KEY,thumbURL} from '/ui/lib.js';
let current=null;
const button=(name,fn)=>el('button',{type:'button',onclick:fn},name);
async function api(project,action='',data){
  const r=await fetch(`/api/project/${encodeURIComponent(project)}/footage-setup${action}`,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','x-frontlot-capability':sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||''},body:JSON.stringify(data)});
  let result;try{result=await r.json();}catch{throw Error('Footage setup could not be read. Try again; no approval has been made.');}
  if(!r.ok)throw Error(typeof result.detail==='string'?result.detail:'Footage setup could not be saved. Review the form and try again.');return result;
}
export async function applyLocationApproval(project,id){return api(project,`/look/${encodeURIComponent(id)}/apply`,{});}
export function renderFootageSetup(s,onSign){
  if(s.trailer_setup_pending)return null;
  if(current?.project===s.project_id){current.refresh();return current.node;}
  const project=s.project_id,node=el('section',{class:'paper-editor footage-setup','aria-label':'Footage prerequisites'}),status=el('p',{role:'status'}),error=el('p',{role:'alert'});
  const controls=el('fieldset',{class:'paper-controls'});
  let snapshot=null,busy=false,loading=false,refreshAgain=false,notice='',raw='',style='\n',mixed=false,file=null,lookDirty=false,lookLoaded=false;
  const refreshWaiters=[];let refreshSerial=0;
  const act=async fn=>{if(busy)return;busy=true;controls.disabled=true;error.textContent='';notice='';try{const result=await fn();notice=result?.message||'';}catch(e){error.textContent=e.message;}finally{try{await refresh();}finally{busy=false;controls.disabled=false;}}};
  const group=name=>el('fieldset',{class:'paper-panel'},el('legend',{},name));
  const look=group('Location written in Front Lot'),answers={};
  look.append(el('p',{},'Write twenty to eighty words, no more than six hundred characters, in the exact location description. These are the existing look limits. Supply the required visual answers yourself; nothing is invented or sent to a model.'));
  const fields=[['Location name','name'],['Exact location description','text'],['Establishing view','establishing_view'],['Default time of day','time_of_day_default'],['Palette anchors, one per line','palette_anchors'],['Architecture or terrain','architecture_or_terrain'],['Dressing, one item per line','dressing'],['Weather and light rules','weather_or_light_rules'],['Continuity risks, one per line','continuity_risks'],['Things to avoid, one per line','negative_lines']];
  for(const [label,key] of fields){const input=el(key==='name'?'input':'textarea',{'aria-label':label,rows:key==='text'?7:2});input.oninput=()=>{lookDirty=true;};answers[key]=input;look.append(el('label',{},label,input));}
  // Keep the exact supplied description separately from HTML's normalised
  // textarea. This is the same bounded rule as the story source editor.
  const text=answers.text,endings=value=>new Set(value.match(/\r\n|\r|\n/g)||[]),normal=value=>value.replace(/\r\n|\r/g,'\n');
  const blocked='This description has mixed line endings. Select all and paste the complete replacement text before saving this edit; its line breaks have not been converted.';
  text.oninput=()=>{lookDirty=true;if(endings(raw).size>1){mixed=normal(raw)!==text.value;if(mixed)error.textContent=blocked;return;}raw=text.value.replace(/\n/g,style);mixed=false;};
  function offset(n){let i=0,j=0;while(i<raw.length&&j<n){if(raw[i]==='\r'&&raw[i+1]==='\n')i++;i++;j++;}return i;}
  text.onpaste=e=>{const pasted=e.clipboardData?.getData('text/plain');if(pasted===undefined)return;e.preventDefault();const all=text.selectionStart===0&&text.selectionEnd===text.value.length;
    if(mixed&&!all){error.textContent=blocked;return;}const prefix=all?'':raw.slice(0,offset(text.selectionStart)),suffix=all?'':raw.slice(offset(text.selectionEnd));
    raw=prefix+pasted+suffix;text.value=raw;const styles=endings(raw);style=styles.size===1?[...styles][0]:'\n';mixed=false;lookDirty=true;error.textContent='';const caret=normal(prefix+pasted).length;text.setSelectionRange(caret,caret);};
  const fictional=el('input',{type:'checkbox','aria-label':'This is an invented location, not a person or character.'});look.append(el('label',{},fictional,'This is an invented location, not a person or character.'));
  look.append(button('Prepare location look for signing',()=>act(async()=>{
    if(mixed)throw Error(blocked);
    const data={entity_kind:'location',fictional_subject_attestation:fictional.checked,minor:false,shape_only:false,spoiler:false,depends_on:[]};
    for(const [,key] of fields)data[key]=key==='text'?raw:['palette_anchors','dressing','continuity_risks','negative_lines'].includes(key)?answers[key].value.split(/\r\n|\r|\n/).filter(v=>v.trim()):answers[key].value;
    const result=await api(project,'/look',data);await onSign(result.gate.request_id);
  })));
  const existing=el('div',{}),pending=el('div',{}),lookChoice=el('select',{'aria-label':'Approved location look'}),shotChoice=el('select',{'aria-label':'Paper-cut shot'}),pictureChoice=el('select',{'aria-label':'Signed picture'});
  const picture=group('Machine-generated reference picture'),chooser=el('input',{type:'file',accept:'.png,.jpg,.jpeg,image/png,image/jpeg','aria-label':'Choose a picture file'});
  chooser.onchange=()=>act(async()=>{const picked=chooser.files?.[0];if(!picked)return;if(picked.size>64*1024*1024)throw Error('Choose a picture no larger than 64 megabytes.');
    const bytes=new Uint8Array(await picked.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));file={name:picked.name,base64:btoa(binary)};return {message:'Picture chosen. The original will stay unchanged on your Mac.'};});
  const tool=el('input',{'aria-label':'Tool that made this picture'}),machine=el('input',{type:'checkbox','aria-label':'This picture is machine-generated.'}),noPerson=el('input',{type:'checkbox','aria-label':'This picture depicts no real person.'});
  const usePicture=button('Use signed picture for selected shot',()=>act(async()=>{const selected=snapshot.approved_looks[Number(lookChoice.value)];return api(project,'/picture/use',{receipt_id:pictureChoice.value,shot_id:shotChoice.value,look:selected?.ref||null});}));usePicture.disabled=true;
  picture.append(el('p',{},'Choose a PNG or JPEG, up to 64 megabytes. Only a machine-generated picture depicting no real person is allowed. Personal photographs, real-person inspiration and other origins are refused. Importing signs its origin; the exact picture-and-look pairing is signed in each footage request.'),
    el('label',{},'Approved location look',lookChoice),el('label',{},'Choose a picture file',chooser),el('label',{},'Tool that made this picture',tool),el('label',{},machine,'This picture is machine-generated.'),el('label',{},noPerson,'This picture depicts no real person.'),
    button('Prepare picture origin for signing',()=>act(async()=>{const selected=snapshot.approved_looks[Number(lookChoice.value)];const result=await api(project,'/picture',{file,look:selected?.ref||null,origin_class:'imported_synthetic',machine_generated:machine.checked,no_real_person:noPerson.checked,origin_tool:tool.value});await onSign(result.gate.request_id);})),
    el('label',{},'Signed picture',pictureChoice),el('label',{},'Paper-cut shot',shotChoice),el('p',{},'The use button becomes available after the picture origin is signed and a current location look and saved shot are available.'),usePicture);
  const prices=group('Fal footage prices and permissions'),priceFields={};
  prices.append(el('p',{},'One supported model: Kling o3 Pro reference video through Fal. Bring your own confirmed maximum cash rate and maximum dollar replacement value of credits, both per second, from your Fal account. Do not use an estimate or make a purchase to discover a price. Front Lot supplies no default prices. Both amounts count within the existing shared production ceiling.'));
  for(const [label,key,type] of [['Highest cash dollars per second','cash_usd_per_second','number'],['Highest credit replacement dollars per second','credits_usd_per_second','number'],['Footage prices expire','expiry','datetime-local']]){
    const input=el('input',{type,'aria-label':label,step:type==='number'?'0.0001':null,min:type==='number'?'0':null});priceFields[key]=input;prices.append(el('label',{},label,input));}
  const allowPrompts=el('input',{type:'checkbox','aria-label':'Allow separately approved prompts to be sent to Fal.'}),allowPictures=el('input',{type:'checkbox','aria-label':'Allow separately approved reference pictures to be sent to Fal.'});
  prices.append(el('label',{},allowPrompts,'Allow separately approved prompts to be sent to Fal.'),el('label',{},allowPictures,'Allow separately approved reference pictures to be sent to Fal.'),
    button('Prepare footage price settings for signing',()=>act(async()=>{
      if(priceFields.cash_usd_per_second.value===''||priceFields.credits_usd_per_second.value==='')throw Error('Fill both account-confirmed price figures. Zero is allowed for one of them; no figure is guessed.');
      const result=await api(project,'/prices',{cash_usd_per_second:Number(priceFields.cash_usd_per_second.value),credits_usd_per_second:Number(priceFields.credits_usd_per_second.value),expires_at:Date.parse(priceFields.expiry.value)/1000,allow_prompts:allowPrompts.checked,allow_reference_images:allowPictures.checked});await onSign(result.request_id);
    })));
  const select=(input,items)=>{const old=input.value;input.replaceChildren(...items.map(v=>el('option',{value:v.value},v.label)));if(items.some(v=>v.value===old))input.value=old;};
  async function refresh(){if(loading){refreshAgain=true;return new Promise(resolve=>refreshWaiters.push({after:refreshSerial+1,resolve}));}loading=true;refreshAgain=false;const serial=++refreshSerial;try{snapshot=await api(project);status.textContent=notice||snapshot.message;if(snapshot.location_error)error.textContent=snapshot.location_error;
    if(snapshot.location&&!lookLoaded&&!lookDirty){const doc=snapshot.location;for(const [,key] of fields)answers[key].value=key==='name'?doc.name:key==='text'?doc.look_spec.prompt_safe_description:Array.isArray(doc.look_spec[key])?doc.look_spec[key].join('\n'):doc.look_spec[key];raw=doc.look_spec.prompt_safe_description;const styles=endings(raw);style=styles.size===1?[...styles][0]:'\n';fictional.checked=doc.look_spec.fictional_subject_attestation;lookLoaded=true;}
    select(lookChoice,snapshot.approved_looks.map((v,i)=>({value:String(i),label:v.name})));select(shotChoice,snapshot.shots.map((v,i)=>({value:v.id,label:`Shot ${i+1}: ${v.purpose}`})));select(pictureChoice,snapshot.pictures.map(v=>({value:v.receipt_id,label:`Machine-generated picture · ${v.tool}`})));
    usePicture.disabled=!snapshot.approved_looks.length||!snapshot.pictures.length||!snapshot.shots.length;
    look.hidden=snapshot.external_material;existing.replaceChildren();if(snapshot.external_material&&!snapshot.existing_sources.length)existing.append(el('p',{},'Existing WriterOS or Wayfinder look material was found. This form does not replace it. Supply its resolved location look through that existing source; it can then be reviewed here.'));
    for(const source of snapshot.existing_sources)existing.append(button(`Review ${source.source==='writeros'?'WriterOS':'Wayfinder'} location: ${source.name}`,()=>act(async()=>{const result=await api(project,'/look/existing',{source:source.source,entity_id:source.entity_id});await onSign(result.gate.request_id);})));
    pending.replaceChildren();for(const request of snapshot.pending)pending.append(button(request.kind==='look_lock'?'Review pending location look':'Review pending picture origin',()=>act(()=>onSign(request.request_id))));
  }catch(e){error.textContent=e.message;}finally{loading=false;for(let i=refreshWaiters.length-1;i>=0;i--)if(refreshWaiters[i].after<=serial)refreshWaiters.splice(i,1)[0].resolve();if(refreshAgain){refreshAgain=false;queueMicrotask(refresh);}}}
  node.append(el('h2',{},'First location, reference picture and footage prices'),el('p',{},'Location looks only. No person or character look can be created here. Every source, picture origin and set of prices needs its own signature. Nothing here sends a prompt or picture to a service, buys a clip or completes a whole stage.'),status,error,existing,look,picture,prices,pending);
  controls.append(...node.childNodes);node.append(controls);
  current={project,node,refresh};refresh();return node;
}
export function renderPrereqSigning(detail,actions,onReturn,lookCard,project,stale){
  const p=detail.packet||{},doc=p.frontlot_location,isLook=detail.request?.kind==='look_lock';
  const box=el('section',{class:'viewer-inner paper-editor','aria-label':isLook?'Location source approval':'Picture origin approval'},el('h2',{},isLook?'Review location written in Front Lot':'Review machine-generated picture origin'),button('Return to footage setup',onReturn));
  if(isLook&&!doc){box.append(el('p',{role:'alert'},p.error||'This location source could not be read. Return to footage setup and prepare its current wording again.'));return box;}
  if(isLook){box.append(el('p',{},'Written in Front Lot for this production. This signs the exact location description and its visual answers. It approves no picture, upload, purchase or whole stage.'),el('h3',{},doc.name),el('pre',{class:'exact-location',style:'white-space:pre-wrap;overflow-wrap:anywhere'},doc.look_spec.prompt_safe_description),lookCard(doc.look_spec,'Visual answers for this location'));}
  else{box.append(el('p',{},p.attestation||'The picture origin could not be read.'),el('p',{},`Tool that made it: ${p.origin_tool||'Not supplied'}.`),el('p',{},'The original stays on your Mac. This signs the picture’s origin only, not permission to send it to a service. Choose its approved location look on the footage card; each footage signature binds that pairing.'),p.visual?.path?el('img',{src:thumbURL(project,p.visual.path,1024),alt:'Machine-generated reference picture for origin review',style:'max-width:100%;max-height:65vh'}):null);}
  if(p.error)box.append(el('p',{role:'alert'},p.error));
  box.append(stale?el('p',{},'This request has been decided. Return to footage setup to see its current state.'):actions);return box;
}
