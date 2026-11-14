import {el} from '/ui/lib.js';
export const TEST_ONLY='For the test trailer only until a person at ElevenLabs confirms otherwise in writing.';
const button=(label,fn)=>el('button',{type:'button',onclick:fn},label);
export function musicForms(data,draft,helpers){return cueForms(data,draft,helpers,'music');}
export function effectForms(data,draft,helpers){return cueForms(data,draft,helpers,'effect');}

function cueForms(data,draft,{api,act,url},kind){
  const effect=kind==='effect',noun=effect?'effect':'music',title=effect?'Sound effects':'Trailer music';
  const keys=['text','seconds',...(effect?['start','shot','loop']:[])],saved=data[effect?'effect_brief':'music_brief'];
  if(!draft.new&&(!draft.loaded||keys.every(k=>draft[k]===draft.saved?.[k]))){
    Object.assign(draft,{loaded:true,briefId:saved?.brief_id||null,text:saved?.text||'',seconds:saved?.duration_seconds??'',
      start:saved?saved.start_frame/24:0,shot:saved?.shot_index??0,loop:saved?.loop??false});
    draft.saved=Object.fromEntries(keys.map(k=>[k,draft[k]]));
  }
  const clean=()=>!!draft.briefId&&keys.every(k=>draft[k]===draft.saved[k]);
  const compose=el('section',{id:`${kind}-compose`,class:'paper-panel'},el('h3',{},title));
  if(!effect)compose.append(el('p',{class:'music-release-note'},TEST_ONLY));
  compose.append(el('p',{},effect?'Describe one effect for a moment in the checked paper cut. Only the exact description, model, length and loop choice leave the computer. Placement stays here; a later cutting view will mix it.':'Instrumental only. Write the exact description that will leave the computer, save it, then prepare one spending card.'));
  function input(label,key,type='text',attrs={}){
    const field=el(type==='textarea'?'textarea':'input',{'aria-label':label,...(type==='textarea'?{rows:4}:{type}),...attrs});
    field.value=draft[key];field.oninput=()=>draft[key]=type==='number'?(field.value===''?'':Number(field.value)):field.value;
    compose.append(el('label',{},label,field));return field;
  }
  input(effect?'Effect description':'Music brief','text','textarea');
  input(effect?'Effect length in seconds':'Music length in seconds','seconds','number',{min:effect?.5:3,max:effect?30:120,step:'any'});
  if(effect){
    const shot=el('select',{'aria-label':'Paper-cut moment'});(data.shots||[]).forEach((s,i)=>shot.append(el('option',{value:i},`Shot ${i+1}: ${s.purpose}`)));
    shot.value=draft.shot;shot.onchange=()=>{draft.shot=Number(shot.value);draft.start=data.shots.slice(0,draft.shot).reduce((n,s)=>n+s.duration_frames,0)/24;start.value=draft.start;};
    compose.append(el('label',{},'Paper-cut moment',shot));const start=input('Start in the trailer, in seconds','start','number',{min:0,step:'any'});
    const loop=el('input',{type:'checkbox','aria-label':'Make this a seamless loop'});loop.checked=draft.loop;loop.onchange=()=>draft.loop=loop.checked;
    compose.append(el('label',{},loop,'Make this a seamless loop'));
  }
  compose.append(el('p',{},effect?'Half a second to thirty seconds, timed in whole frames at 24 frames per second. The effect must fit inside the paper cut.':'Three seconds to two minutes, timed in whole frames at 24 frames per second.'),
    button(`Save ${noun} brief`,()=>act(async()=>{
      const body={text:draft.text,seconds:Number(draft.seconds),brief_id:draft.briefId};
      if(effect)Object.assign(body,{start_frame:Number(draft.start)*24,shot_index:draft.shot,loop:draft.loop});
      const result=await api(url+'/brief',body);draft.new=false;draft.briefId=result.brief_id;draft.saved=Object.fromEntries(keys.map(k=>[k,draft[k]]));return result.message;
    })));
  const prepare=button(`Prepare ${noun} spending card`,()=>act(async()=>{
    if(!clean())throw Error(`Save the current ${noun} brief and timing before preparing its spending card.`);
    await api(url+'/prepare',{brief_id:draft.briefId});
  }));prepare.disabled=!data.usable||!data.account.configured||!data.price_settings_signed;compose.append(prepare);
  if(effect&&!data.price_basis?.effect_credits_per_second)compose.append(el('p',{role:'alert'},'Sign a sound-effect credits-per-second ceiling in the audio price settings first.'));
  if(effect)compose.append(button('Start a separate sound effect',()=>act(async()=>{
    Object.assign(draft,{loaded:true,new:true,briefId:null,text:'',seconds:'',start:0,shot:0,loop:false,saved:{}});
    return 'New effect started. Save its own description and moment; existing takes keep their briefs.';
  })));
  const bring=el('section',{id:`${kind}-import`,class:'paper-panel'},el('h3',{},`Bring your own ${noun}`),
    el('p',{},'Choose a file with the Mac file chooser, then record its source and licence. Bringing it in costs nothing and sends nothing to a service. Listen before signing that you want to keep it.'),
    el('p',{},`The licence note is your statement about where this ${noun} came from. A changed or re-encoded file, or audio made outside Front Lot, may have no generation receipt here.`));
  if(effect)bring.append(el('p',{},'Save the effect brief above first. Its moment, length and loop choice also apply to the imported take. Its measured length must match within one frame.'));
  const article=effect?'an':'a';
  bring.append(button(`Choose ${article} ${noun} file`,()=>act(async()=>{
    const chosen=await api(url+'/import/choose',{});if(chosen.cancelled)return 'No file was chosen. Your previous selection is unchanged.';
    draft.choice=chosen;return 'Audio file chosen. Add its source and licence note before bringing it in.';
  })),el('p',{},draft.choice?`Chosen file: ${draft.choice.name}`:`No ${noun} file chosen.`));
  for(const [label,key] of [[`Where this ${noun} came from`,'origin'],['Licence note','licence']]){
    const field=el('textarea',{'aria-label':label,rows:2});field.value=draft[key]||'';field.oninput=()=>draft[key]=field.value;bring.append(el('label',{},label,field));
  }
  bring.append(button(`Bring in this ${noun}`,()=>act(async()=>{
    if(!draft.choice)throw Error(`Choose ${article} ${noun} file first, then add its source and licence note.`);
    if(effect&&!clean())throw Error('Save the current effect brief and timing before bringing in a file.');
    const body={choice_id:draft.choice.choice_id,source:draft.origin||'',licence_note:draft.licence||''};if(effect)body.brief_id=draft.briefId;
    const result=await api(url+'/import',body);draft.choice=null;return result.message;
  })));
  return [compose,bring];
}
