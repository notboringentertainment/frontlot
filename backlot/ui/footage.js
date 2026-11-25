// Focused footage controls. Every write calls the existing signed operations.
import {el, CAPABILITY_TOKEN_KEY, mediaURL, spendingCard,keepEditorPosition,actionButton} from "/ui/lib.js";
import {savedPaperCut,paperCutChangedHere,changeSavedPaperCut} from '/ui/trailer.js';
import {setFootageProgress} from '/ui/next_step.js';
let data=null,project=null,hooks=null,globalError="",observing=false,readRetries=0;
const drafts=new Map(),completed=new Set();
const feedback=new Map();
let controlsNode=null,controlsKey='';
let readEpoch=0;
export const footageReadEpoch=()=>readEpoch;
export async function refreshFootageControls(){
  const epoch=++readEpoch;
  const response=await fetch(`/api/project/${encodeURIComponent(project)}/footage`);
  if(!response.ok)throw Error('The shot could not be refreshed. Reopen Footage before preparing another request.');
  const next=await response.json();
  if(epoch===readEpoch)setFootageState(project,next,hooks);
}
export async function footageAction(action,body) {
  const response=await fetch(`/api/project/${encodeURIComponent(project)}/footage/${action}`,{
    method:"POST",headers:{"Content-Type":"application/json","x-frontlot-capability":sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||""},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.detail||"The action did not finish. Reopen this shot and try again.");
  return result;
}
export function setFootageState(id,next,callbacks) {
  if(project!==id){drafts.clear();completed.clear();feedback.clear();globalError="";readRetries=0;data=null;}
  if(next?.error){if(!data?.error)readRetries=3;}else readRetries=0;
  if(data?.remaining_usd!==next?.remaining_usd||JSON.stringify(data?.quote_context)!==JSON.stringify(next?.quote_context))for(const d of drafts.values()){d.quote=null;d.quoteStarted=false;d.quoteVersion=(d.quoteVersion||0)+1;}
  project=id;data=next;hooks=callbacks;
  setFootageProgress(id,next);
}
export async function afterFootageSignature(id,kind) {
  if(!["footage_spend","footage_take","audio_resolution"].includes(kind)||completed.has(id))return;
  try {
    globalError="";
    const result=await footageAction("signed",{gate_id:id});
    if(result.paper_cut)paperCutChangedHere(project,result.paper_cut);
    completed.add(id);
    hooks.returnToShots(result.paper_cut);await hooks.refresh();
  }catch(error){globalError=error.message;hooks.returnToShots();await hooks.refresh();}
}
export async function footageDecision(shot,take,decision) {
  if(decision==='accept')try{savedPaperCut(project);}catch(error){globalError=error.message;hooks.render();return;}
  const result=await footageAction("decision",{shot_id:shot.id,take_id:take.take_id,decision});
  await hooks.refresh();await hooks.selectGate(result.gate.request_id);
}
function input(label,value,attrs={}) {
  const field=el("input",{...attrs,"aria-label":label});field.value=value;
  return {field,node:el("label",{},el("span",{},label),field)};
}
function makeButton(label,action,disabled=false,key=label){return actionButton(label,action,{disabled:disabled?"":null,feedback,key});}
const button=makeButton;
function draft(shot){
  if(!drafts.has(shot.id))drafts.set(shot.id,{length:Math.max(3,Math.min(15,Math.ceil(shot.duration_seconds))),model:data.models[0].id,looks:{},quote:null,error:"",busy:false});
  return drafts.get(shot.id);
}
async function quote(shot,d){
  if(!sessionStorage.getItem(CAPABILITY_TOKEN_KEY)){
    d.quote=null;d.error="This window is read-only. Open the writer’s board to review prices and prepare a request.";d.updateQuote?.();return;
  }
  const version=d.quoteVersion=(d.quoteVersion||0)+1;
  try{const value=await footageAction("quote",{shot_id:shot.id,model:d.model,duration_seconds:d.length});if(version!==d.quoteVersion)return;d.quote=value;d.error="";}
  catch(error){if(version!==d.quoteVersion)return;d.quote=null;d.error=error.message;}
  d.updateQuote?.();
  return d.error||(d.quote?'Price reviewed. The maximum charge and remaining ceiling are shown here.':'');
}
async function perform(d,action){d.busy=true;d.error="";hooks.render();let result;try{result=await action();}catch(error){d.error=error.message;}finally{d.busy=false;await hooks.refresh();}return d.error||result?.message;}

function shotPanel(s,shot){
  const button=(label,action,disabled=false,key=label)=>makeButton(label,action,disabled,shot.id+':'+key);
  const d=draft(shot),latest=shot.requests.at(-1);
  const panel=el("article",{class:"paper-panel footage-shot","aria-label":`${shot.label} footage`},
    el("h3",{},shot.label),el("p",{class:"footage-status",role:"status"},shot.status),
    el("p",{},shot.purpose),el("p",{},`Paper-cut screen time: ${shot.duration_seconds} seconds.`));
  if(shot.brief)panel.append(el("p",{},`Shot allowance: $${shot.brief.spend_allowance_usd.toFixed(4)}; up to ${shot.brief.max_video_takes} takes. Each clip needs its own signature.`));
  const model=el("select",{"aria-label":"Footage model",onchange:()=>{d.model=model.value;quote(shot,d);}},
    ...data.models.map(m=>el("option",{value:m.id},m.label)));model.value=d.model;
  const length=input("Clip length in seconds",d.length,{type:"number",min:3,max:15,step:1});
  length.field.addEventListener("input",()=>{d.length=length.field.value===""?null:Number(length.field.value);d.quote=null;d.error="";d.quoteVersion=(d.quoteVersion||0)+1;d.updateQuote?.();queueMicrotask(()=>quote(shot,d));});
  length.field.addEventListener("change",()=>{d.length=length.field.value===""?null:Number(length.field.value);quote(shot,d);});
  panel.append(el("label",{},el("span",{},"Model"),model),el("p",{},"Only this model has a signed footage operation here. Other models are not built yet."),length.node);
  for(const [index,path] of shot.references.entries()){
    const old=shot.brief?.reference_manifest.find(r=>r.path===path);
    if(!data.looks.some(l=>l.key===d.looks[path]))d.looks[path]=data.looks.find(l=>l.entity_id===old?.visual_bible_entity_id)?.key||(data.looks.length===1?data.looks[0].key:"");
    const choice=el("select",{"aria-label":`Approved look for reference ${index+1}`,onchange:()=>{d.looks[path]=choice.value;hooks.render();}},
      el("option",{value:""},"Choose its approved look"),...data.looks.map(l=>el("option",{value:l.key},l.name)));choice.value=d.looks[path];
    panel.append(el("figure",{},el("img",{src:mediaURL(s.project_id,path),alt:`Reference picture ${index+1}`,style:"max-width:240px;max-height:180px"}),el("figcaption",{},`Reference picture ${index+1}`)),choice);
  }
  const selected=new Set(Object.values(d.looks));
  for(const look of data.looks.filter(l=>selected.has(l.key)))panel.append(hooks.lookCard(look.payload,"Approved look behind the picture"));
  if(!d.quote&&!d.quoteStarted){d.quoteStarted=true;queueMicrotask(()=>quote(shot,d));}
  const price=el("div",{"aria-live":"polite"});panel.append(price);
  panel.append(button("Refresh price",()=>quote(shot,d),d.busy));
  const create=async(retake)=>perform(d,async()=>{
    const result=await footageAction("create",{shot_id:shot.id,model:d.model,duration_seconds:d.length,looks:d.looks,...(retake?{retake_of:retake}:{} )});
    await refreshFootageControls();await hooks.selectGate(result.gate.request_id);
  });
  const activeRequest=shot.requests.some(r=>r.running||(!r.approved&&r.gate_id)||(r.reservation&&["submitting","submitted","pending_billing"].includes(r.reservation.state)));
  const blocked=()=>d.busy||activeRequest||!d.quote||d.quote.highest_usd>d.quote.remaining_usd||!data.story_ready||!shot.references.length||shot.references.some(p=>!d.looks[p]);
  const requestButton=button(shot.takes.length?"Create a retake request":"Create footage request",()=>create(latest?.id),blocked());panel.append(requestButton);
  d.updateQuote=()=>{
    price.replaceChildren();
    if(d.quote)price.append(spendingCard({highest_charge:d.quote.highest_usd,highest_cash:d.quote.max_cash_usd,
      highest_credits:d.quote.max_credits_usd,remaining:d.quote.remaining_usd,expiry:new Date(d.quote.price_expires_at*1000).toLocaleString()}));
    if(d.quote&&d.quote.highest_usd>d.quote.remaining_usd)price.append(el('p',{role:'alert'},'This clip does not fit the remaining production ceiling. Choose an affordable length, resolve held charges, or stop. This form cannot raise the ceiling.'));
    if(d.error)price.append(el("p",{role:"alert"},d.error));
    requestButton.disabled=blocked();
  };
  d.updateQuote();
  if(!shot.references.length)panel.append(el("p",{role:"note"},"Add reference pictures in the paper-cut shot before creating a request."));
  if(shot.references.length&&!data.looks.length)panel.append(el("p",{role:"note"},"No current approved look is available. Open Footage setup to review and sign the location look before creating a footage request."));
  if(data.refusal)panel.append(el("p",{role:"note"},data.refusal));
  for(const req of shot.requests){
    if(req.waiting_reason)panel.append(el('p',{role:'status'},req.waiting_reason));
    if(req.signing_refusal)panel.append(el('p',{role:'alert'},req.signing_refusal),
      button('Set aside unsigned request',()=>perform(d,()=>footageAction('set-aside',{request_id:req.id})),d.busy,req.id+':aside'),
      el('p',{},`Then use ${shot.takes.length?'Create a retake request':'Create footage request'} for a fresh spending card. Setting this aside sends, spends and approves nothing.`));
    else if(!req.approved&&req.gate_id&&!req.waiting_reason)panel.append(button("Review and sign request",()=>hooks.selectGate(req.gate_id),d.busy,req.id+':sign'));
    if(req.approved&&!req.running&&!req.signed_outcome&&(!req.reservation||(req.reservation.state==="completed"&&!shot.takes.some(t=>t.reservation_id===req.reservation.reservation_id))))panel.append(button("Make signed clip",()=>perform(d,()=>footageAction("dispatch",{request_id:req.id})),d.busy,req.id+':make'));
    if(req.signed_outcome&&req.reservation&&["completed","failed"].includes(req.reservation.state))panel.append(el("p",{},"Your signed account outcome is recorded. It settles the money and cannot make a clip. Create a retake request and sign it separately for another clip."));
    if(req.running)panel.append(el("p",{role:"status"},"Generating this one clip. Keep this board open to watch it arrive; no retry will be made."));
    if(req.error)panel.append(el("p",{role:"alert"},req.error));
    if(req.reservation&&["submitting","submitted","pending_billing"].includes(req.reservation.state)&&!req.running){
      if(req.outcome_error){panel.append(el("p",{role:"alert"},req.outcome_error));continue;}
      if(req.signed_outcome){
        const outcome=req.signed_outcome;
        panel.append(el("p",{},"Your account outcome is signed and still needs applying. No second signature or service call is needed."),
          el("p",{},outcome.charged?`Signed charge: cash $${outcome.cash_usd.toFixed(4)} and credit replacement value $${outcome.credits_usd.toFixed(4)}.`:"Signed outcome: nothing was charged."),
          el("p",{},outcome.note),button("Apply the signed account outcome",()=>perform(d,()=>footageAction("apply-resolution",{reservation_id:req.reservation.reservation_id})),d.busy,req.id+':apply'));
        continue;
      }
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
        button("Review account outcome",()=>perform(d,async()=>{const result=await footageAction("resolve",{reservation_id:req.reservation.reservation_id,charged:review.charged,cash:Number(review.cash),credits:Number(review.credits),note:review.note});await hooks.refresh();await hooks.selectGate(result.gate.request_id);}),d.busy,req.id+':account-review'));
    }
  }
  for(const take of shot.takes){
    panel.append(el("p",{},take.status==="Accepted"?(take.selected?"Kept":"Signed keep needs applying"):take.status),hooks.take(s,shot,take));
    if(take.status==="Accepted"&&!take.selected)panel.append(button("Use signed keep in the cut",()=>perform(d,()=>changeSavedPaperCut(project,()=>footageAction("apply",{shot_id:shot.id,take_id:take.take_id,decision:"accept"}))),d.busy));
    if(take.declined)panel.append(el("p",{},"Declined. This decision is final for this take; create a retake request for another clip."));
    if(take.status==="Needs a fresh look")panel.append(el("p",{},"The shot or its inputs changed. This take cannot enter the cut. Review the current shot and create a fresh request."));
  }
  return panel;
}
export function renderFootageControls(s){
  if(!data)return el("p",{},"Loading footage controls…");
  const storySigned=s.stages?.find(x=>x.name==='canon_ingest')?.status==='completed';
  setFootageProgress(s.project_id,data);
  const key=()=>JSON.stringify([project,data,globalError,readRetries,storySigned,hooks.takeFeedback?.(),[...drafts].map(([id,d])=>[id,d.looks,d.busy])]);
  if(controlsNode&&controlsKey===key())return controlsNode;
  controlsNode??=el("section",{class:"footage-controls","aria-label":"Footage workspace"});
  keepEditorPosition(controlsNode,()=>controlsNode.replaceChildren(...[el("h2",{},"Footage"),
    el("p",{},(storySigned?"The story is signed. Check the saved paper cut. ":"Supply and sign the story in Story workspace, then check the saved paper cut. ")+"Use Footage setup to sign a location look, import a machine-generated reference picture and sign footage price figures. Changing the production ceiling still happens outside these panels. Every clip and every keep or decline decision needs your signature. Keeping a take completes no stage."),
    data.error||globalError?el("p",{role:"alert"},data.error||globalError):null,
    data.error?el('p',{role:'status'},readRetries>0?'Trying up to three more reads. Nothing will be sent or bought.':'Automatic reads have stopped. Use Try reading footage again to retry; no purchase will be repeated.'):null,
    data.error?button("Try reading footage again",()=>{readRetries=3;hooks.refresh();}):null,...(data.shots||[]).map(shot=>shotPanel(s,shot))].filter(child=>child!=null)));
  controlsKey=key();return controlsNode;
}

function observationFailed(){
  if(data?.error)return;
  data={error:'The footage panel could not be read. Nothing was sent or bought.',shots:[]};
  readRetries=3;hooks.render();
}

// Polling only observes. It never starts or retries a request.
setInterval(async()=>{
  if(!project||!hooks||document.hidden||hooks.isRefreshing()||observing)return;
  // Observe waiting cards too: a sound purchase can change their ceiling.
  // These reads never replay a live call or submit anything.
  const retrying=!!data?.error;
  if(!(data?.shots||[]).some(shot=>shot.requests.some(request=>request.running||!request.approved&&request.gate_id))&&!(retrying&&readRetries>0))return;
  if(retrying)readRetries--;
  observing=true;
  const epoch=readEpoch;
  try{
    const response=await fetch(`/api/project/${encodeURIComponent(project)}/footage`);
    if(!response.ok){observationFailed();return;}
    const next=await response.json();
    if(epoch===readEpoch&&JSON.stringify(next)!==JSON.stringify(data)&&!hooks.isRefreshing())await hooks.refresh();
  }catch{observationFailed();}
  finally{observing=false;if(retrying&&readRetries===0&&data?.error)hooks.render();}
},1500);
