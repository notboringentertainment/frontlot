import {el} from '/ui/lib.js';

const fields=[
  ['Dollar replacement value per credit','dollars_per_credit',
    'This is the dollar cost of replacing one credit. In ElevenLabs, open your profile, then Subscription and its credit-purchase or usage-billing quote; divide the quoted dollars by the quoted credits. If no replacement quote is offered, ask ElevenLabs Support before signing. Do not substitute a plan average or buy credits to fill this form.',
    'https://elevenlabs.io/docs/overview/administration/billing'],
  ['Highest speech credits per character','speech_credits_per_character',
    'This is the highest base credit charge for each letter, space or punctuation mark, before a voice’s extra rate. In ElevenLabs, open Text to Speech and select Multilingual v2; check that model’s API rate on the linked pricing page. Website rates can differ. Ask Support if your account has no firm maximum.',
    'https://elevenlabs.io/pricing/api'],
  ['Highest music credits per minute','music_credits_per_minute',
    'This is the highest credit charge for one minute, including any minimum charge. In ElevenLabs, open Music and hover over the remaining-credit number to inspect the selected length and number of versions without pressing Generate. Confirm the Music v1 API ceiling with Support if only an estimate or a different model is shown.',
    'https://elevenlabs.io/docs/help-center/product/core-capabilities/music/how-much-does-eleven-music-cost'],
  ['Highest voice rate multiplier','voice_multiplier_ceiling',
    'Some voices cost more than the base rate; a two-times multiplier doubles it. In ElevenLabs, open Voices, then Voice Library and read each chosen voice’s multiplier tag; Text to Speech also shows a notice. Enter a ceiling covering every voice you intend to use. Ask Support if a rate is unclear.',
    'https://elevenlabs.io/docs/help-center/product/voices/voice-library/what-are-custom-rates-and-credit-multipliers'],
  ['Highest sound-effect credits per second','effect_credits_per_second',
    'This is the maximum credit charge for one second of an explicitly timed effect. In ElevenLabs, open Sound Effects and check the API rate for Sound Effects v3 in the linked guide; confirm the ceiling for your account before signing. Leave it blank to keep effect generation unavailable. Front Lot never guesses a price or uses automatic duration.',
    'https://elevenlabs.io/docs/overview/capabilities/sound-effects'],
  ['Prices expire','expiry',
    'This is when Front Lot must stop using these figures; it is your instruction, not an ElevenLabs setting. In your ElevenLabs profile, open Subscription to check renewal. Choose no later than the earliest confirmed rate expiry or known price change.',
    'https://elevenlabs.io/docs/overview/administration/billing'],
];

function localDate(seconds){
  const d=new Date(seconds*1000),pad=n=>String(n).padStart(2,'0');
  if(!Number.isFinite(d.getTime()))return '';
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function renderPriceSettings(data,state,{api,act,onSign,url}){
  state.values??={};
  if(data.price_basis&&!state.dirty&&state.digest!==data.price_config_sha256){
    state.values={};
    for(const [,key] of fields)if(key!=='expiry')state.values[key]=String(data.price_basis[key]?.value??'');
    state.values.expiry=localDate(Math.min(...Object.values(data.price_basis).map(rate=>rate.expires_at)));
    state.digest=data.price_config_sha256;
  }
  const box=el('details',{class:'paper-panel'},el('summary',{},'Audio account consent and price ceilings'),
    el('p',{},'Use figures confirmed for your account. Front Lot supplies no default prices. Signing these settings buys no take; every paid request still needs its own signature. Saving a draft pauses paid work until it is signed or discarded.'),
    el('p',{},'Public plan averages and generation estimates do not establish a guaranteed maximum for your account. If a firm ceiling is not shown, ask ElevenLabs Support for written confirmation before signing. Do not press Generate to obtain a price.'),
    el('p',{class:'price-saved-state'},data.price_settings_signed?'These saved figures are signed. To change them, edit the figures and sign a new settings request.':'Sign the price settings first. The figures below are not spending permission until you sign.'));
  box.open=!!state.open;box.ontoggle=()=>{if(box.isConnected)state.open=box.open;};
  for(const [label,key,help,source] of fields){
    const id='audio-price-help-'+key,input=el('input',{type:key==='expiry'?'datetime-local':'number','aria-label':label,'aria-describedby':id,step:'any'});
    input.value=state.values[key]??'';input.oninput=()=>{state.values[key]=input.value;state.dirty=true;};
    box.append(el('div',{class:'audio-price-field'},el('label',{},label,input),el('p',{id,class:'price-help'},help,' ',el('a',{href:source,target:'_blank',rel:'noopener noreferrer'},'ElevenLabs guide'))));
  }
  box.append(el('button',{type:'button',onclick:()=>act(async()=>{
    const body={expires_at:Date.parse(state.values.expiry)/1000};for(const [,key] of fields)if(key!=='expiry'&&(key!=='effect_credits_per_second'||state.values[key]))body[key]=Number(state.values[key]);
    const request=await api(url+'/prices',body);state.dirty=false;state.digest=null;await onSign(request.request_id);
  })},'Prepare price settings for signing'),
  el('button',{type:'button',onclick:()=>act(async()=>{
    const result=await api(url+'/prices/restore',{});state.dirty=false;state.digest=null;return result.message;
  })},'Discard draft and restore signed settings'));
  return box;
}
