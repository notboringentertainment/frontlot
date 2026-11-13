import {el} from '/ui/lib.js';
export const TEST_ONLY='For the test trailer only until a person at ElevenLabs confirms otherwise in writing.';
const button=(label,fn)=>el('button',{type:'button',onclick:fn},label);
export function musicForms(data,draft,{api,act,url}){
  const saved=data.music_brief;
  if(!draft.loaded || (draft.text===draft.savedText&&draft.seconds===draft.savedSeconds)){
    draft.loaded=true;draft.briefId=saved?.brief_id||null;draft.text=saved?.text||'';draft.seconds=saved?.duration_seconds??'';
    draft.savedText=draft.text;draft.savedSeconds=draft.seconds;
  }
  const compose=el('section',{id:'music-compose',class:'paper-panel'},el('h3',{},'Trailer music'),
    el('p',{class:'music-release-note'},TEST_ONLY),
    el('p',{},'Instrumental only. Write the exact description that will leave the computer, save it, then prepare one spending card.'));
  const text=el('textarea',{'aria-label':'Music brief',rows:4});text.value=draft.text;text.oninput=()=>draft.text=text.value;
  const seconds=el('input',{'aria-label':'Music length in seconds',type:'number',min:3,max:120,step:'any'});seconds.value=draft.seconds;seconds.oninput=()=>draft.seconds=seconds.value===''?'':Number(seconds.value);
  compose.append(el('label',{},'Music brief',text),el('label',{},'Music length in seconds',seconds),el('p',{},'Three seconds to two minutes, timed in whole frames at 24 frames per second.'),
    button('Save music brief',()=>act(async()=>{
      const result=await api(url+'/brief',{text:draft.text,seconds:Number(draft.seconds),brief_id:draft.briefId});
      draft.briefId=result.brief_id;draft.savedText=draft.text;draft.savedSeconds=draft.seconds;return result.message;
    })));
  const prepare=button('Prepare music spending card',()=>act(async()=>{
    if(!draft.briefId||draft.text!==draft.savedText||draft.seconds!==draft.savedSeconds)throw Error('Save the current music brief and length before preparing its spending card.');
    await api(url+'/prepare',{brief_id:draft.briefId});
  }));prepare.disabled=!data.usable||!data.account.configured||!data.price_settings_signed;compose.append(prepare);
  const bring=el('section',{id:'music-import',class:'paper-panel'},el('h3',{},'Bring your own music'),
    el('p',{},'Choose a file with the Mac file chooser, then record its source and licence. Bringing it in costs nothing and sends nothing to a service. Listen before signing that you want to keep it.'),
    el('p',{},'The licence note is your statement about where this music came from. A changed or re-encoded file, or music made outside Front Lot, may have no generation receipt here.'));
  bring.append(button('Choose a music file',()=>act(async()=>{
    const chosen=await api(url+'/import/choose',{});
    if(chosen.cancelled)return 'No file was chosen. Your previous selection is unchanged.';
    draft.choice=chosen;return 'Music file chosen. Add its source and licence note before bringing it in.';
  })),el('p',{},draft.choice?`Chosen file: ${draft.choice.name}`:'No music file chosen.'));
  for(const [label,key] of [['Where this music came from','origin'],['Licence note','licence']]){
    const input=el('textarea',{'aria-label':label,rows:2});input.value=draft[key]||'';input.oninput=()=>draft[key]=input.value;bring.append(el('label',{},label,input));
  }
  bring.append(button('Bring in this music',()=>act(async()=>{
    if(!draft.choice)throw Error('Choose a music file first, then add its source and licence note.');
    const result=await api(url+'/import',{choice_id:draft.choice.choice_id,source:draft.origin||'',licence_note:draft.licence||''});draft.choice=null;return result.message;
  })));
  return [compose,bring];
}
