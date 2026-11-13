// Focused footage controls. Every write calls the existing signed operations.
import {el, CAPABILITY_TOKEN_KEY, mediaURL, spendingCard} from "/ui/lib.js";
let data=null,project=null,hooks=null,globalError="",observing=false;
const drafts=new Map(),completed=new Set();
export async function footageAction(action,body) {
  const response=await fetch(`/api/project/${encodeURIComponent(project)}/footage/${action}`,{
    method:"POST",headers:{"Content-Type":"application/json","x-frontlot-capability":sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||""},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.detail||"The action did not finish. Reopen this shot and try again.");
  return result;
}
export function setFootageState(id,next,callbacks) {
  if(project!==id){drafts.clear();completed.clear();globalError="";}
  if(data?.remaining_usd!==next?.remaining_usd)for(const d of drafts.values()){d.quote=null;d.quoteStarted=false;}
  project=id;data=next;hooks=callbacks;
}
export async function afterFootageSignature(id,kind) {
  if(!["footage_spend","footage_take","audio_resolution"].includes(kind)||completed.has(id))return;
  try {
    globalError="";
    await footageAction("signed",{gate_id:id});completed.add(id);
    hooks.returnToShots();await hooks.refresh();
  }catch(error){globalError=error.message;hooks.returnToShots();await hooks.refresh();}
}
export async function footageDecision(shot,take,decision) {
  const result=await footageAction("decision",{shot_id:shot.id,take_id:take.take_id,decision});
  await hooks.refresh();await hooks.selectGate(result.gate.request_id);
}
function input(label,value,attrs={}) {
  const field=el("input",{...attrs,"aria-label":label});field.value=value;
  return {field,node:el("label",{},el("span",{},label),field)};
}
function button(label,action,disabled=false){return el("button",{type:"button",disabled:disabled?"":null,onclick:action},label);}
function draft(shot){
  if(!drafts.has(shot.id))drafts.set(shot.id,{length:Math.max(3,Math.min(15,Math.ceil(shot.duration_seconds))),model:data.models[0].id,looks:{},quote:null,error:"",busy:false});
  return drafts.get(shot.id);
}
async function quote(shot,d){
  const version=d.quoteVersion=(d.quoteVersion||0)+1;
  try{const value=await footageAction("quote",{shot_id:shot.id,model:d.model,duration_seconds:d.length});if(version!==d.quoteVersion)return;d.quote=value;d.error="";}
  catch(error){if(version!==d.quoteVersion)return;d.quote=null;d.error=error.message;}
  hooks.render();
}
async function perform(d,action){d.busy=true;d.error="";hooks.render();try{await action();}catch(error){d.error=error.message;}finally{d.busy=false;await hooks.refresh();}}

function shotPanel(s,shot){
  const d=draft(shot),latest=shot.requests.at(-1);
  const panel=el("article",{class:"paper-panel footage-shot","aria-label":`${shot.label} footage`},
    el("h3",{},shot.label),el("p",{class:"footage-status",role:"status"},shot.status),
    el("p",{},shot.purpose),el("p",{},`Paper-cut screen time: ${shot.duration_seconds} seconds.`));
  if(shot.brief)panel.append(el("p",{},`Shot allowance: $${shot.brief.spend_allowance_usd.toFixed(4)}; up to ${shot.brief.max_video_takes} takes. Each clip needs its own signature.`));
  const model=el("select",{"aria-label":"Footage model",onchange:()=>{d.model=model.value;quote(shot,d);}},
    ...data.models.map(m=>el("option",{value:m.id},m.label)));model.value=d.model;
  const length=input("Clip length in seconds",d.length,{type:"number",min:3,max:15,step:1});
  length.field.addEventListener("input",()=>{d.length=length.field.value===""?null:Number(length.field.value);d.quote=null;});
  length.field.addEventListener("change",()=>{d.length=length.field.value===""?null:Number(length.field.value);quote(shot,d);});
  panel.append(el("label",{},el("span",{},"Model"),model),el("p",{},"Only this model has a signed footage operation here. Other models are not built yet."),length.node);
  for(const [index,path] of shot.references.entries()){
    const old=shot.brief?.reference_manifest.find(r=>r.path===path);
    if(!d.looks[path])d.looks[path]=data.looks.find(l=>l.entity_id===old?.visual_bible_entity_id)?.key||(data.looks.length===1?data.looks[0].key:"");
    const choice=el("select",{"aria-label":`Approved look for reference ${index+1}`,onchange:()=>{d.looks[path]=choice.value;hooks.render();}},
      el("option",{value:""},"Choose its approved look"),...data.looks.map(l=>el("option",{value:l.key},l.name)));choice.value=d.looks[path];
    panel.append(el("figure",{},el("img",{src:mediaURL(s.project_id,path),alt:`Reference picture ${index+1}`,style:"max-width:240px;max-height:180px"}),el("figcaption",{},`Reference picture ${index+1}`)),choice);
  }
  const selected=new Set(Object.values(d.looks));
  for(const look of data.looks.filter(l=>selected.has(l.key)))panel.append(hooks.lookCard(look.payload,"Approved look behind the picture"));
  if(!d.quote&&!d.quoteStarted){d.quoteStarted=true;queueMicrotask(()=>quote(shot,d));}
  if(d.quote)panel.append(spendingCard({highest_charge:d.quote.highest_usd,highest_cash:d.quote.max_cash_usd,
    highest_credits:d.quote.max_credits_usd,remaining:d.quote.remaining_usd,expiry:new Date(d.quote.price_expires_at*1000).toLocaleString()}));
  panel.append(button("Refresh price",()=>quote(shot,d),d.busy));
  const create=async(retake)=>perform(d,async()=>{
    const result=await footageAction("create",{shot_id:shot.id,model:d.model,duration_seconds:d.length,looks:d.looks,...(retake?{retake_of:retake}:{} )});
    await hooks.refresh();await hooks.selectGate(result.gate.request_id);
  });
  const activeRequest=shot.requests.some(r=>r.running||(!r.approved&&r.gate_id)||(r.reservation&&["submitting","submitted","pending_billing"].includes(r.reservation.state)));
  const blocked=d.busy||activeRequest||!d.quote||!data.story_ready||!shot.references.length||shot.references.some(p=>!d.looks[p]);
  panel.append(button(latest?"Create a retake request":"Create footage request",()=>create(latest?.id),blocked));
  if(!shot.references.length)panel.append(el("p",{role:"note"},"Add reference pictures in the paper-cut shot before creating a request."));
  if(shot.references.length&&!data.looks.length)panel.append(el("p",{role:"note"},"No look is approved yet. Approve its look outside these panels before creating a footage request."));
  if(data.refusal)panel.append(el("p",{role:"note"},data.refusal));
  for(const req of shot.requests){
    if(!req.approved&&req.gate_id)panel.append(button("Review and sign request",()=>hooks.selectGate(req.gate_id),d.busy));
    if(req.approved&&!req.running&&(!req.reservation||(req.reservation.state==="completed"&&!shot.takes.some(t=>t.reservation_id===req.reservation.reservation_id))))panel.append(button("Make signed clip",()=>perform(d,()=>footageAction("dispatch",{request_id:req.id})),d.busy));
    if(req.running)panel.append(el("p",{role:"status"},"Generating this one clip. Keep this board open to watch it arrive; no retry will be made."));
    if(req.error)panel.append(el("p",{role:"alert"},req.error));
    if(req.reservation&&["submitting","submitted","pending_billing"].includes(req.reservation.state)&&!req.running){
      d.reviews||=new Map();
      if(!d.reviews.has(req.id))d.reviews.set(req.id,{charged:false,cash:"0",credits:"0",note:""});
      const review=d.reviews.get(req.id);
      const charged=el("input",{type:"checkbox","aria-label":"The service charged this request"});
      charged.checked=review.charged;charged.onchange=()=>{review.charged=charged.checked;};
      const cash=input("Cash charged in dollars",review.cash,{type:"number",min:0,step:.0001});
      const credits=input("Credit replacement value in dollars",review.credits,{type:"number",min:0,step:.0001});
      const note=input("What you checked in the service account",review.note,{type:"text"});
      for(const [key,control] of [["cash",cash],["credits",credits],["note",note]])control.field.oninput=()=>{review[key]=control.field.value;};
      panel.append(el("p",{},"Spending is blocked. Check the service account, then sign its outcome. This will never submit the old request again."),
        el("label",{},charged,"The service charged this request"),cash.node,credits.node,note.node,
        button("Review account outcome",()=>perform(d,async()=>{const result=await footageAction("resolve",{reservation_id:req.reservation.reservation_id,charged:review.charged,cash:Number(review.cash),credits:Number(review.credits),note:review.note});await hooks.refresh();await hooks.selectGate(result.gate.request_id);}),d.busy));
    }
  }
  for(const take of shot.takes){
    panel.append(el("p",{},take.status==="Accepted"?(take.selected?"Kept":"Signed keep needs applying"):take.status),hooks.take(s,shot,take));
    if(take.status==="Accepted"&&!take.selected)panel.append(button("Use signed keep in the cut",()=>perform(d,()=>footageAction("apply",{shot_id:shot.id,take_id:take.take_id,decision:"accept"})),d.busy));
    if(take.declined)panel.append(el("p",{},"Declined. This decision is final for this take; create a retake request for another clip."));
    if(take.status==="Needs a fresh look")panel.append(el("p",{},"The shot or its inputs changed. This take cannot enter the cut. Review the current shot and create a fresh request."));
  }
  if(d.error)panel.append(el("p",{role:"alert"},d.error));
  return panel;
}
export function renderFootageControls(s){
  if(!data)return el("p",{},"Loading footage controls…");
  return el("section",{class:"footage-controls","aria-label":"Footage workspace"},el("h2",{},"Footage"),
    el("p",{},"Story and look approval happens outside these panels for now; those approval controls are not built here. Signing price figures and changing the production ceiling also happen outside these panels. Every clip and every keep or decline decision needs your signature. Keeping a take completes no stage."),
    data.error||globalError?el("p",{role:"alert"},data.error||globalError):null,...(data.shots||[]).map(shot=>shotPanel(s,shot)));
}

// Polling only observes. It never starts or retries a request.
setInterval(async()=>{
  if(!project||!hooks||document.hidden||hooks.isRefreshing()||observing)return;
  observing=true;
  try{
    const response=await fetch(`/api/project/${encodeURIComponent(project)}/footage`);
    if(!response.ok)return;
    const next=await response.json();
    if(JSON.stringify(next)!==JSON.stringify(data)&&!hooks.isRefreshing())await hooks.refresh();
  }catch{/* A failed read grants no authority and will be read again. */}
  finally{observing=false;}
},1500);
