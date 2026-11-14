// Focused trailer panels. The server owns timing, history and story checks.
import { el, CAPABILITY_TOKEN_KEY } from "/ui/lib.js";

async function request(url, data) {
  const response = await fetch(url, data === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json",
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
  name.input.maxLength=200;length.input.step="any";length.input.min=1;length.input.max=120;
  ceiling.input.step="0.01";ceiling.input.min=0;ceiling.input.max=1000000;
  const key=crypto.randomUUID(), message=el("p",{role:"status"});
  const submit=el("button",{type:"submit"},"Create trailer and review signing");
  const form=el("form",{onsubmit:async(e)=>{e.preventDefault();submit.disabled=true;
    try { const r=await request("/api/trailers",{title:name.input.value,seconds:Number(length.input.value),ceiling:Number(ceiling.input.value),request_key:key}); location.assign(`/p/${r.project_id}`); }
    catch(error){message.textContent=error.message;submit.disabled=false;}
  }},el("p",{},"Creating a trailer costs nothing. You will sign its creation separately; no stage or spending is approved. Separate signed spending settings are still required before any paid work."),
  el("p",{},"Your name becomes the trailer title. Its folder gets a unique name beginning with ‘trailer’ so punctuation in a title is safe and similarly named folders cannot collide."),name.node,length.node,ceiling.node,submit,message);
  panel.append(form);return panel;
}
const kinds=["hook","escalation","reversal","invitation"], names=["Hook","Escalation","Reversal","Final invitation"];
let editor=null, lastProject=null, wasPending=false;
function emptyPaper(){return {beats:kinds.map(kind=>({kind,text:""})),lines:[],cards:[],shots:[]};}
function savedTime(v){return new Date(v.saved_at).toLocaleString() + (v.time_source==="file" ? " (file time)" : "");}

export function renderPaperCut(s) {
  if(editor && lastProject===s.project_id && wasPending===Boolean(s.trailer_setup_pending)) {
    editor.refresh(s.paper_cut);return editor.node;
  }
  lastProject=s.project_id;wasPending=Boolean(s.trailer_setup_pending);
  const node=el("section",{class:"paper-editor","aria-label":"Trailer paper cut"});
  const folder=el("details",{},el("summary",{},"Where this trailer is kept"),el("p",{},`Its folder is called ${s.project_id}. This unique folder name keeps punctuation in your title safe and prevents folder names colliding.`));
  if(s.trailer_setup_pending) {
    node.append(el("h2",{},"Start this trailer"),folder,el("p",{},s.trailer_creation?.message || "This trailer has not been signed."));
    if(s.trailer_creation?.needs_repair) {
      const status=el("p",{role:"status"});
      node.append(el("button",{type:"button",onclick:async(e)=>{e.target.disabled=true;try{
        const result=await request(`/api/project/${encodeURIComponent(s.project_id)}/trailer-creation-request`,{});
        status.textContent=result.message;
      }catch(error){status.textContent=error.message;e.target.disabled=false;}}},"Make the creation signing request again"),status);
    }
    // Pending creation status can change through repair or signing.
    editor=null;return node;
  }
  let targetFrames=Math.round(s.trailer.target_duration_seconds*24);
  let state=s.paper_cut || {}, revision=state.current?.edit_revision_sha256 || null;
  let draft=structuredClone(state.current?.paper_cut || emptyPaper()), dirty=false, preserved=null, playing=null;
  const api=`/api/project/${encodeURIComponent(s.project_id)}/paper-cut`;
  const status=el("p",{role:"status"},"Planning only. Nothing here approves a stage or buys media.");
  const checks=el("div",{"aria-live":"polite"}), conflict=el("div",{}), content=el("div",{}), history=el("section",{class:"paper-panel"}), details=el("div",{});
  node.append(el("h2",{},"Story and paper cut"),folder,status,checks,conflict,
    el("p",{},"Supply and sign the story in Story workspace, then compare this saved paper cut against it. You can always save a draft here; later stages must wait for its story checks."),content);
  const button=(text,fn)=>el("button",{type:"button",onclick:fn},text);
  const panel=(title)=>{const n=el("section",{class:"paper-panel"},el("h3",{},title));content.append(n);return n;};
  const protectedPanels=[];
  let updateTiming=()=>{};
  const rowWarnings=[];
  function updateRowWarnings(){for(const show of rowWarnings)show();}
  function draw() {
    if(playing){clearInterval(playing);playing=null;}
    content.replaceChildren();protectedPanels.length=0;rowWarnings.length=0;
    const beats=panel("Beat sequence");
    draft.beats.forEach((b,i)=>{const f=field(names[i],b.text,"textarea");f.input.oninput=()=>{b.text=f.input.value;dirty=true;};beats.append(f.node);});
    function rows(title,key,shot=false) {
      const box=panel(title), list=el("div",{});box.append(list);
      if(!shot){const protectedBox=el("div",{});box.prepend(protectedBox);protectedPanels.push(protectedBox);}
      const drawRows=()=>{list.replaceChildren();draft[key].forEach((row,index)=>{
        const item=el("fieldset",{},el("legend",{},`${shot?"Shot":key==="lines"?"Line":"Card"} ${index+1}`));
        if(!shot){const note=el("div",{class:"short-phrase-warning"});item.append(note);
          const show=()=>{note.replaceChildren();if(state.current?.paper_cut?.[key]?.[index]?.text!==row.text)return;
            const prefix=`${key==="lines"?"Line":"Card"} ${index+1} resembles part of a protected line:`;
            for(const text of state.story_check?.warnings||[])if(text.startsWith(prefix))note.append(el("p",{role:"status"},text));};
          rowWarnings.push(show);show();}
        const fields=shot?[["Purpose (private plan)","purpose","textarea"],["Screen time in frames","duration_frames","number"],["References, separated by commas","references"],["Difficulty","difficulty"]]:[["Exact wording","text","textarea"],["Starts at frame","start_frame","number"],["Screen time in frames","duration_frames","number"],["Story material","source_ref"]];
        fields.forEach(([label,k,type])=>{
          if(k==="source_ref") {
            const select=el("select",{},el("option",{value:""},"Unassigned in this draft"));
            const options=state.sources || [];
            options.forEach(x=>select.append(el("option",{value:x.value},x.label)));
            if(row[k] && !options.some(x=>x.value===row[k]))select.append(el("option",{value:row[k]},"Story material unavailable — choose again"));
            select.value=row[k]||"";select.onchange=()=>{row[k]=select.value;dirty=true;};
            item.append(el("label",{},el("span",{},label),select));return;
          }
          const f=field(label,Array.isArray(row[k])?row[k].join(", "):row[k],type);
          if(type==="number"){f.input.step=1;f.input.min=k==="start_frame"?0:1;}
          f.input.oninput=()=>{row[k]=type==="number"?Number(f.input.value):k==="references"?f.input.value.split(",").map(x=>x.trim()).filter(Boolean):f.input.value;dirty=true;updateTiming();updateRowWarnings();};item.append(f.node);
        });
        const up=button("Move earlier",()=>{[draft[key][index-1],draft[key][index]]=[row,draft[key][index-1]];dirty=true;drawRows();updateTiming();});up.disabled=index===0;
        item.append(up,button("Remove from this draft",()=>{draft[key].splice(index,1);dirty=true;drawRows();updateTiming();}));list.append(item);
      });};
      box.append(button(shot?"Add shot":key==="lines"?"Add spoken line":"Add title card",()=>{draft[key].push(shot?{purpose:"",duration_frames:24,references:[],difficulty:""}:{text:"",start_frame:0,duration_frames:24,source_ref:""});dirty=true;drawRows();updateTiming();}));drawRows();
    }
    rows("Exact spoken lines","lines");rows("Exact title cards","cards");rows("Timed shot list","shots",true);
    const timing=panel("Temporary timed sequence"), timingText=el("p",{}), preview=el("div",{class:"paper-preview"});
    const cursor=el("input",{type:"range",min:0,max:targetFrames-1,value:0,"aria-label":"Sequence frame"});
    const show=()=>{
      let start=0;const frame=Number(cursor.value);const shot=draft.shots.find(x=>{const hit=frame>=start&&frame<start+x.duration_frames;start+=x.duration_frames;return hit;});
      const cards=draft.cards.filter(x=>frame>=x.start_frame&&frame<x.start_frame+x.duration_frames).map(x=>x.text);
      const lines=draft.lines.filter(x=>frame>=x.start_frame&&frame<x.start_frame+x.duration_frames).map(x=>x.text);
      const saved=state.current?.paper_cut?.shots?.find(x=>x.id===shot?.id);
      const available=shot && saved && JSON.stringify(saved)===JSON.stringify(shot) ? state.footage_availability?.[shot.id] : null;
      preview.replaceChildren(el("strong",{},available || "UNFINISHED PLACEHOLDER"),el("p",{},available==="Accepted footage available"?"The saved sequence uses an accepted take for this shot. This sketch does not play the clip.":shot?"Planned shot only — no usable footage":"No shot planned here"),el("p",{},cards.join(" · ")),el("p",{},lines.length?`Spoken text only: ${lines.join(" · ")}`:""),el("small",{},`Frame ${frame} · ${(frame/24).toFixed(2)} seconds`));
    };
    updateTiming=()=>{const total=draft.shots.reduce((n,x)=>n+x.duration_frames,0),target=targetFrames;cursor.max=target-1;if(Number(cursor.value)>target-1)cursor.value=target-1;
      timingText.textContent=total===target?"The shots match the target length.":`Shots total ${(total/24).toFixed(2)} seconds; target ${(target/24).toFixed(2)} seconds. ${Math.abs(total-target)} frames ${total>target?"over":"short"}.`;show();};
    cursor.oninput=show;
    const play=button("Play placeholders",()=>{if(playing){clearInterval(playing);playing=null;play.textContent="Play placeholders";}else{play.textContent="Pause";playing=setInterval(()=>{cursor.value=(Number(cursor.value)+1)%(Number(cursor.max)+1);show();},1000/24);}});
    timing.append(el("p",{},"This timing sketch identifies shots with accepted footage and those still using placeholders. It cannot finish a stage. Timed playback and export of real media are not built yet. Watch full takes on their shot cards. Shot purposes remain private planning notes."),timingText,preview,cursor,play);updateTiming();
  }
  function showVersion(value, label) {
    const box=el("div",{},el("h4",{},label));
    if(value.saved_at)box.append(el("p",{},new Date(value.saved_at).toLocaleString()));
    if(value.paper_cut)for(const [key,title] of [["beats","Beats"],["lines","Spoken lines"],["cards","Title cards"],["shots","Shots"]]) {
      box.append(el("h4",{},title));
      value.paper_cut[key].forEach((row,i)=>{
        const reference=row.source_ref ? (state.sources||[]).find(x=>x.value===row.source_ref)?.label || "Story material unavailable" : "Unassigned in this draft";
        box.append(el("p",{},`${i+1}. ${row.text || row.purpose}`));
        if(key==="lines"||key==="cards")box.append(el("p",{},`Starts at frame ${row.start_frame}; lasts ${row.duration_frames} frames. Story material: ${reference}.`));
        if(key==="shots")box.append(el("p",{},`Lasts ${row.duration_frames} frames. References: ${row.references.join(", ") || "None yet"}. Difficulty: ${row.difficulty}.`));
      });
    }
    if(value.cuts?.length)box.append(el("p",{},`This version also keeps ${value.cuts.length} rough-cut shots. Their timing is preserved when restoring a paper cut.`));
    return box;
  }
  async function refreshFromServer(){update(await request(api));}
  function update(next) {
    if(!next)return;
    state=next;
    updateRowWarnings();
    if(Number.isInteger(state.target_frames)){targetFrames=state.target_frames;updateTiming();}
    for(const box of protectedPanels)box.replaceChildren(el("h4",{},"Protected wording from the story"),el("p",{},"Mechanical comparison cannot recognise every rewording. Compare your lines and cards with these originals."),...(state.protected_lines||[]).map(text=>el("blockquote",{},text)));
    checks.replaceChildren();
    if(state.error)checks.append(el("p",{role:"alert"},state.error));
    if(state.needs_review)checks.append(el("p",{role:"alert"},state.current?.paper_cut_pending?state.current.timing_message:"The story, script or timing changed. Dependent work needs a fresh look."));
    if(state.story_check){checks.append(el("p",{},state.story_check.message));
      for(const text of [...state.story_check.errors,...state.story_check.warnings.filter(x=>! /^(Line|Card) \d+ resembles part of a protected line:/.test(x))])checks.append(el("p",{role:"alert"},text));}
    conflict.replaceChildren();
    if((state.current?.edit_revision_sha256 || null)!==revision) {
      conflict.append(el("p",{},"Another window saved a newer version. Your unsaved draft is still here."),button("Open the latest saved version",()=>{
        details.replaceChildren(showVersion(state.current,"Latest saved version"),button("Use this latest version and keep my unsaved draft",()=>{
          preserved=structuredClone(draft);draft=structuredClone(state.current?.paper_cut || emptyPaper());revision=state.current?.edit_revision_sha256 || null;dirty=false;draw();update(state);
          status.textContent="Latest saved version loaded. Your unsaved draft is kept below.";
        }));
      }));
    }
    if(preserved)conflict.append(button("Return to my unsaved draft",()=>{draft=preserved;preserved=null;dirty=true;draw();update(state);status.textContent="Your unsaved draft is back. Saving will make a new version after the latest one.";}));
    // Rebuild only story selectors, leaving every typed word mounted in place.
    content.querySelectorAll("select").forEach(select=>{
      const chosen=select.value;select.replaceChildren(el("option",{value:""},"Unassigned in this draft"),...(state.sources||[]).map(x=>el("option",{value:x.value},x.label)));
      if(chosen && !(state.sources||[]).some(x=>x.value===chosen))select.append(el("option",{value:chosen},"Story material unavailable — choose again"));select.value=chosen;
    });
    history.replaceChildren(el("h3",{},"Saved versions"));
    for(const version of state.history || [])history.append(button(`Open version ${version.number} · ${savedTime(version)}${version.id===state.current?.edit_revision_sha256?" · Current":""}`,async()=>{
      try{const value=await request(`${api}/${version.id}`);
        details.replaceChildren(showVersion(value,`Saved version ${version.number} · ${savedTime(version)}`),button("Restore this as a new version",async()=>{
          try{const result=await request(`${api}/restore`,{revision:version.id,expected_revision:revision});
            if(dirty)preserved=structuredClone(draft);draft=structuredClone(result.paper_cut || emptyPaper());revision=result.edit_revision_sha256;dirty=false;draw();await refreshFromServer();status.textContent="Restored as a new saved version. Every previous version is kept.";
          }catch(error){status.textContent=error.message;await refreshFromServer();}
        }));
      }catch(error){status.textContent=error.message;}
    }));
  }
  draw();
  const save=button("Save a new version",async()=>{save.disabled=true;try{
    const result=await request(api,{paper:draft,expected_revision:revision});revision=result.edit_revision_sha256;
    draft.shots.forEach((shot,i)=>{shot.id=result.paper_cut.shots[i].id;});dirty=false;
    status.textContent=`Saved. ${result.timing_message} No stage is approved.`;await refreshFromServer();
  }catch(error){status.textContent=error.message;try{await refreshFromServer();}catch{ /* Keep the original save failure visible. */ }}finally{save.disabled=false;}});
  node.append(save,button("Check paper cut against approved story",async()=>{try{
    if(dirty)throw Error('Save your changed draft as a new version before comparing it with the approved story.');
    const result=await request(`/api/project/${encodeURIComponent(s.project_id)}/story/check`,{});
    revision=result.paper_cut.edit_revision_sha256;await refreshFromServer();status.textContent=result.message;
  }catch(error){status.textContent=error.message;}}),button("Refresh story checks and saved versions",async()=>{try{await refreshFromServer();status.textContent="Story checks and versions refreshed. Your draft text is kept.";}catch(error){status.textContent=error.message;}}),
    el("p",{},"Previous versions are kept in the order saved. Opening a version does not change your draft. Restoring makes another version."),history,details);
  update(state);
  window.addEventListener("beforeunload",e=>{if(dirty){e.preventDefault();e.returnValue="";}});
  editor={node,refresh:update};return node;
}
