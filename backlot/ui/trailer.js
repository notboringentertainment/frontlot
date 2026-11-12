// Focused paper-cut forms. Draft text stays mounted across board refreshes.
import { el, CAPABILITY_TOKEN_KEY } from "/ui/lib.js";

async function post(url, data) {
  const response = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json",
    "x-frontlot-capability":sessionStorage.getItem(CAPABILITY_TOKEN_KEY) || ""}, body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "The save did not finish. Your previous version is safe.");
  return result;
}
function field(label, value, type="text") {
  const input = el(type === "textarea" ? "textarea" : "input", type === "textarea" ? {} : {type});
  input.value = value ?? "";
  return {input, node:el("label",{},el("span",{},label),input)};
}
export function startTrailerForm() {
  const panel=el("details",{class:"paper-panel"},el("summary",{},"Start a trailer"));
  const name=field("Trailer name",""), length=field("Target length in seconds",28,"number"), ceiling=field("Total spending ceiling in dollars",50,"number");
  length.input.step="any";ceiling.input.step="0.01";
  const message=el("p",{role:"status"});
  const submit=el("button",{type:"submit"},"Create trailer and review signing");
  const form=el("form",{onsubmit:async(e)=>{e.preventDefault();submit.disabled=true;
    try { const r=await post("/api/trailers",{title:name.input.value,seconds:Number(length.input.value),ceiling:Number(ceiling.input.value)}); location.assign(`/p/${r.project_id}`); }
    catch(error){message.textContent=error.message;submit.disabled=false;}
  }},el("p",{},"Creating a trailer costs nothing. You will sign its creation separately; no stage or spending is approved."),name.node,length.node,ceiling.node,submit,message);
  panel.append(form);return panel;
}
let mounted=null, draft=null, revision=null, dirty=false, status=null, pending=false;
const kinds=["hook","escalation","reversal","invitation"];
const names=["Hook","Escalation","Reversal","Final invitation"];
export function renderPaperCut(s) {
  if (mounted && pending && !s.trailer_setup_pending) mounted=null;
  pending=Boolean(s.trailer_setup_pending);
  if (mounted) {
    const fresh=s.paper_cut?.current;
    if ((fresh?.edit_revision_sha256 || null)!==revision || s.paper_cut?.needs_review)
      status.textContent="The saved material changed. Your text is kept here; reload before saving if another window saved it. Dependent work needs a fresh look.";
    return mounted;
  }
  const saved=s.paper_cut?.current;
  revision=saved?.edit_revision_sha256 || null;
  draft=structuredClone(saved?.paper_cut || {beats:kinds.map(kind=>({kind,text:""})),lines:[],cards:[],shots:[]});
  mounted=el("section",{class:"paper-editor", "aria-label":"Trailer paper cut"});
  status=el("p",{role:"status"},s.paper_cut?.needs_review ? "The story or timing changed. This paper cut and dependent work need a fresh look." : "Planning only. Nothing here approves a stage or buys media.");
  mounted.append(el("h2",{},"Story and paper cut"),status,startTrailerForm());
  if(s.trailer_setup_pending) {mounted.append(el("p",{},"Sign trailer creation under Needs you before saving."));return mounted;}
  const panel=(title)=>{const n=el("section",{class:"paper-panel"},el("h3",{},title));mounted.append(n);return n;};
  const beats=panel("Beat sequence");
  draft.beats.forEach((b,i)=>{const f=field(names[i],b.text,"textarea");f.input.oninput=()=>{b.text=f.input.value;dirty=true;};beats.append(f.node);});
  function rows(title,key,shot=false) {
    const box=panel(title), list=el("div",{});box.append(list);
    const draw=()=>{list.replaceChildren();draft[key].forEach((row,index)=>{
      const item=el("fieldset",{},el("legend",{},`${shot?"Shot":key==="lines"?"Line":"Card"} ${index+1}`));
      const fields=shot?[["Purpose","purpose"],["Screen time in frames","duration_frames","number"],["References, separated by commas","references"],["Difficulty","difficulty"]]:[["Exact wording","text","textarea"],["Starts at frame","start_frame","number"],["Screen time in frames","duration_frames","number"],["Story reference","source_ref"]];
      fields.forEach(([label,k,type])=>{
        if(k==="source_ref") {
          const select=el("select",{},el("option",{value:""},"Choose approved story material"),...(s.paper_cut?.sources||[]).map(x=>el("option",{value:x.value},x.label)));
          select.value=row[k]||"";select.onchange=()=>{row[k]=select.value;dirty=true;};
          item.append(el("label",{},el("span",{},"Story material"),select));return;
        }
        const f=field(label,Array.isArray(row[k])?row[k].join(", "):row[k],type);f.input.oninput=()=>{row[k]=type==="number"?Number(f.input.value):k==="references"?f.input.value.split(",").map(x=>x.trim()).filter(Boolean):f.input.value;dirty=true;updateTiming();};item.append(f.node);});
      const up=el("button",{type:"button",disabled:index===0?"":null,onclick:()=>{[draft[key][index-1],draft[key][index]]=[row,draft[key][index-1]];dirty=true;draw();updateTiming();}},"Move earlier");
      item.append(up,el("button",{type:"button",onclick:()=>{draft[key].splice(index,1);dirty=true;draw();updateTiming();}},"Remove from this draft"));list.append(item);
    });};
    box.append(el("button",{type:"button",onclick:()=>{draft[key].push(shot?{purpose:"",duration_frames:24,references:[],difficulty:""}:{text:"",start_frame:0,duration_frames:24,source_ref:""});dirty=true;draw();updateTiming();}},shot?"Add shot":key==="lines"?"Add spoken line":"Add title card"));draw();
  }
  rows("Exact spoken lines","lines");rows("Exact title cards","cards");rows("Timed shot list","shots",true);
  const timing=panel("Temporary timed sequence"), timingText=el("p",{}), preview=el("div",{class:"paper-preview"}), cursor=el("input",{type:"range",min:0,max:Math.round(s.trailer.target_duration_seconds*24)-1,value:0,"aria-label":"Sequence frame"});
  let playing=null;
  const show=()=>{let start=0;const frame=Number(cursor.value);const shot=draft.shots.find(x=>{const hit=frame>=start&&frame<start+x.duration_frames;start+=x.duration_frames;return hit;});
    const cards=draft.cards.filter(x=>frame>=x.start_frame&&frame<x.start_frame+x.duration_frames).map(x=>x.text);
    const lines=draft.lines.filter(x=>frame>=x.start_frame&&frame<x.start_frame+x.duration_frames).map(x=>x.text);
    preview.replaceChildren(el("strong",{},"UNFINISHED PLACEHOLDER"),el("p",{},shot?.purpose||"No shot planned here"),el("p",{},cards.join(" · ")),el("p",{},lines.length?`Spoken text only: ${lines.join(" · ")}`:""),el("small",{},`Frame ${frame} · ${(frame/24).toFixed(2)} seconds`));};
  const updateTiming=()=>{const total=draft.shots.reduce((n,x)=>n+x.duration_frames,0),target=Math.round(s.trailer.target_duration_seconds*24);timingText.textContent=total===target?"The shots match the target length.":`Shots total ${(total/24).toFixed(2)} seconds; target ${(target/24).toFixed(2)} seconds. ${Math.abs(total-target)} frames ${total>target?"over":"short"}.`;show();};
  cursor.oninput=show;
  const play=el("button",{type:"button",onclick:()=>{if(playing){clearInterval(playing);playing=null;play.textContent="Play placeholders";}else{play.textContent="Pause";playing=setInterval(()=>{cursor.value=(Number(cursor.value)+1)%(Number(cursor.max)+1);show();},1000/24);}}},"Play placeholders");
  timing.append(el("p",{},"This is a timing sketch with no footage or sound. It cannot finish a stage. Playback and export of real media are not built yet."),timingText,preview,cursor,play);updateTiming();
  const save=el("button",{type:"button",onclick:async()=>{save.disabled=true;try{const result=await post(`/api/project/${encodeURIComponent(s.project_id)}/paper-cut`,{paper:draft,expected_revision:revision});revision=result.edit_revision_sha256;draft.shots.forEach((shot,i)=>{shot.id=result.paper_cut.shots[i].id;});dirty=false;status.textContent=`Saved a new version. ${result.timing_message} No stage is approved.`;}catch(e){status.textContent=e.message;}finally{save.disabled=false;}}},"Save a new version");
  mounted.append(save,el("p",{},"Previous versions are kept. Changing the story, script or target length makes dependent work need a fresh look."));
  const history=panel("Previous versions");
  (s.paper_cut?.history||[]).forEach((id,i)=>history.append(el("button",{type:"button",onclick:async()=>{const r=await fetch(`/api/project/${encodeURIComponent(s.project_id)}/paper-cut/${id}`).then(x=>x.json());const read=el("div",{},el("h4",{},`Saved version ${i+1}`));for(const [key,label] of [["beats","Beats"],["lines","Spoken lines"],["cards","Title cards"],["shots","Shots"]])read.append(el("h4",{},label),...r.paper_cut[key].map(x=>el("p",{},x.text||x.purpose)));history.append(read);}},`Read saved version ${i+1}`)));
  window.addEventListener("beforeunload",e=>{if(dirty){e.preventDefault();e.returnValue="";}});
  return mounted;
}
