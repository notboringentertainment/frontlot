import {el,CAPABILITY_TOKEN_KEY} from '/ui/lib.js';
let current=null;
const button=(name,fn)=>el('button',{type:'button',onclick:fn},name);
async function api(project,action='',data){
  const r=await fetch(`/api/project/${encodeURIComponent(project)}/story${action}`,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','x-frontlot-capability':sessionStorage.getItem(CAPABILITY_TOKEN_KEY)||''},body:JSON.stringify(data)});
  const value=await r.json();if(!r.ok)throw Error(value.detail||'The story could not be saved. Your earlier source is kept.');return value;
}
export async function applyStoryApproval(project){return api(project,'/apply',{});}
export function renderStory(s,onSign){
  if(s.trailer_setup_pending)return null;
  if(current?.project===s.project_id){current.refresh();return current.node;}
  const project=s.project_id,node=el('section',{class:'story-panel paper-editor','aria-label':'Trailer story'});
  const status=el('p',{role:'status','aria-live':'polite'}),message=el('p',{role:'alert'}),annotations=el('div',{}),actions=el('div',{});
  const text=el('textarea',{'aria-label':'Exact story text',rows:10,spellcheck:'false'});
  let rawText='',file=null,protectedLines=[],spoilers=[],dirty=false,loading=false,refreshAgain=false,busy=false,snapshot=null;
  const edited=()=>{dirty=true;file=null;rawText=text.value;};text.oninput=edited;
  text.onpaste=e=>{const pasted=e.clipboardData?.getData('text/plain');if(pasted===undefined)return;e.preventDefault();rawText=text.value.slice(0,text.selectionStart)+pasted+text.value.slice(text.selectionEnd);text.value=rawText;dirty=true;file=null;};
  const act=async fn=>{if(busy)return;busy=true;message.textContent='';try{await fn();}catch(e){message.textContent=e.message;}finally{busy=false;await refresh();}};
  const chooser=el('input',{type:'file',accept:'.txt,.md,text/plain,text/markdown','aria-label':'Choose a text file'});
  chooser.onchange=()=>act(async()=>{
    const picked=chooser.files?.[0];if(!picked)return;
    if(picked.size>128*1024)throw Error('Choose a plain UTF-8 text story no larger than 128 kilobytes. Nothing was saved.');
    const bytes=new Uint8Array(await picked.arrayBuffer());
    let decoded;try{decoded=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);}catch{throw Error('This file is not readable UTF-8 text. Choose a plain text or Markdown file.');}
    let binary='';for(let start=0;start<bytes.length;start+=8192)binary+=String.fromCharCode(...bytes.subarray(start,start+8192));
    file={name:picked.name,base64:btoa(binary)};rawText=decoded;text.value=decoded;dirty=true;
    message.textContent='File chosen. Its original bytes will be copied; the original file will not be changed. Save it for review.';
  });
  function drawAnnotations(){
    annotations.replaceChildren();
    for(const [values,label] of [[protectedLines,'Protected wording'],[spoilers,'Spoiler passage']]){
      values.forEach((value,i)=>{const input=el('textarea',{'aria-label':`${label} ${i+1}`,rows:2});input.value=value;input.oninput=()=>{values[i]=input.value;dirty=true;};annotations.append(el('label',{},`${label} ${i+1}`,input),button(`Remove ${label.toLowerCase()} ${i+1}`,()=>{values.splice(i,1);dirty=true;drawAnnotations();}));});
    }
  }
  function drawActions(){
    actions.replaceChildren();
    if(snapshot?.gate_id&&!snapshot.approved)actions.append(button('Review and sign this story',()=>act(async()=>{if(dirty)throw Error('Save your changed story for review first.');await onSign(snapshot.gate_id);})));
    if(snapshot?.signed_unapplied)actions.append(button('Apply signed story approval',()=>act(async()=>{await applyStoryApproval(project);})));
  }
  async function refresh(){
    if(loading){refreshAgain=true;return;}loading=true;refreshAgain=false;
    try{
      snapshot=await api(project);status.textContent=snapshot.message;
      if(snapshot.current&&!dirty){rawText=snapshot.current.text;text.value=rawText;file=null;protectedLines=[...snapshot.current.protected];spoilers=[...snapshot.current.spoilers];drawAnnotations();}
      drawActions();
    }catch(e){message.textContent=e.message;}finally{loading=false;if(refreshAgain){refreshAgain=false;queueMicrotask(refresh);}}
  }
  node.append(el('h2',{},'Story source and approval'),
    el('p',{},'Paste the story this trailer is drawn from, or choose a plain UTF-8 text or Markdown file, up to 128 kilobytes. Your exact source is kept. Nothing is rewritten, summarised or sent to a service. Saving and comparing approve nothing.'),status,message,
    el('label',{},'Exact story text',text),el('label',{},'Choose a text file',chooser),
    el('p',{},'No story facts are inferred. You can explicitly protect exact wording or identify spoiler passages below. Each must occur exactly in your source. The local comparison cannot recognise every rewording.'),
    button('Add protected wording',()=>{protectedLines.push('');dirty=true;drawAnnotations();}),
    button('Add spoiler passage',()=>{spoilers.push('');dirty=true;drawAnnotations();}),annotations,
    button('Save story for review',()=>act(async()=>{
      const data={protected:protectedLines,spoilers,...(file?{file}:{text:rawText})};
      await api(project,'',data);dirty=false;
    })),actions);
  current={project,node,refresh};refresh();return node;
}
export function renderStorySigning(detail,actions,onReturn,stale){
  const source=detail.packet?.story;
  const node=el('section',{class:'viewer-inner paper-editor','aria-label':'Story approval'},el('h2',{},'Review exact story'),button('Return to story',onReturn));
  if(!source){node.append(el('p',{role:'status'},detail.packet?.error||(stale?'This story request has been decided. Return to story to see the current source and its approval.':'The exact story evidence is unavailable. Return to story and prepare its review again.')));return node;}
  node.append(el('p',{},`${source.byte_length} bytes · ${source.characters} characters. The signature binds these exact bytes and the protected passages below.`),
    el('p',{},'Approving makes this the story source for this trailer only. It permits local paper-cut comparisons. It buys nothing, sends nothing to a service, and approves no look, sound, footage or later stage.'),
    el('pre',{class:'story-exact',style:'white-space:pre-wrap;overflow-wrap:anywhere;max-height:45vh;overflow:auto'},source.text));
  for(const [values,label] of [[source.protected,'Protected wording'],[source.spoilers,'Spoiler passages']])node.append(el('h3',{},label),...values.map(v=>el('blockquote',{},v)),values.length?null:el('p',{},'None designated.'));
  node.append(el('details',{},el('summary',{},'Source fingerprint'),el('code',{},source.sha256)),stale?el('p',{},'This request has been decided. Return to the story to continue.'):actions);
  return node;
}
