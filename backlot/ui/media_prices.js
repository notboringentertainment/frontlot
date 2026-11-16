import {el} from '/ui/lib.js';
const money=v=>'$'+Number(v).toFixed(4);
export function priceReview(p){
  const box=el('section',{class:'paper-panel'},el('h3',{},'One production ceiling, both sets of prices'),
    el('p',{},`Whole trailer ceiling: ${money(p.production_ceiling)}. Signing these settings sends nothing and buys nothing. Every purchase still needs its own signature.`));
  const details=el('details',{class:'paper-panel',open:p.audio_prices&&p.footage_prices?'':null},el('summary',{},'Details of these settings'),el('p',{},'Both saved sets of figures and the other production settings below are covered by this same signature.'));
  if(p.audio_prices){
    const q=p.audio_price_summary;
    box.append(el('h3',{},'ElevenLabs audio'),el('p',{},'This lets Front Lot send your voice words and instrumental music briefs to ElevenLabs after you sign each paid request.'),el('p',{},q.longest_line_dollars===null?'No spoken line is saved yet.':`For the longest saved spoken line: at most ${money(q.longest_line_dollars)}.`),
      el('p',{},`For one minute of instrumental music: at most ${money(q.music_minute_dollars)}.`),
      el('p',{},q.effect_second_dollars===null?'No sound-effect figures are set.':`For one second of sound effects: at most ${money(q.effect_second_dollars)}.`),
      el('p',{},`These audio figures expire ${new Date(q.expires_at*1000).toLocaleString()}.`));
    const labels={dollars_per_credit:'Dollar replacement value per credit',speech_credits_per_character:'Highest speech credits per character',music_credits_per_minute:'Highest music credits per minute',voice_multiplier_ceiling:'Highest voice rate multiplier',effect_credits_per_second:'Highest sound-effect credits per second'};
    for(const [key,rate] of Object.entries(p.audio_prices))details.append(el('p',{},`${labels[key]}: ${rate.value}. Set ${new Date(rate.set_at*1000).toLocaleString()}; expires ${new Date(rate.expires_at*1000).toLocaleString()}.`));
  }else box.append(el('p',{},'No ElevenLabs audio figures are set. Audio purchases stay unavailable.'));
  if(p.footage_prices){
    const f=p.footage_prices;
    box.append(el('h3',{},'Fal footage · Kling o3 Pro reference video'),
      el('p',{},`Highest cash: ${money(f.cash_usd_per_second)} per second. Highest credit replacement value: ${money(f.credits_usd_per_second)} per second. Both count within the same ceiling.`),
      el('p',{},`For a five-second clip: at most ${money((f.cash_usd_per_second+f.credits_usd_per_second)*5)}. Every clip gets its own exact spending card.`),
      el('p',{},`These footage figures expire ${new Date(f.expires_at*1000).toLocaleString()}.`));
  }else box.append(el('p',{},'No Fal footage figures are set. Footage purchases stay unavailable.'));
  const words={prompts:'text prompts',reference_images:'reference pictures',generated_sheet_images:'generated character sheets',speech_text:'spoken words',music_prompt:'music descriptions',effect_prompt:'sound-effect descriptions'};
  box.append(el('h3',{},'What each service may receive'));
  for(const item of p.outbound_permissions||[])box.append(el('p',{},`${item.provider==='fal'?'Fal':item.provider==='elevenlabs'?'ElevenLabs':item.provider}: separately approved ${item.content_classes.map(v=>words[v]||v).join(', ')}.`));
  details.append(el('p',{},`Run limit: ${p.run_minutes} minutes. Cast ceiling: ${p.cast_ceiling.characters} characters and ${p.cast_ceiling.locations} places.`),el('p',{},p.picture_judge?`Picture review service: ${p.picture_judge.judge_provider} / ${p.picture_judge.judge_model}.`:'No paid picture judge is selected.'));
  box.append(details);
  return box;
}
export function renderPriceSigning(detail,actions,onReturn,stale){
  const p=detail.packet,box=el('section',{class:'viewer-inner paper-editor','aria-label':'Price settings approval'},
    el('h2',{},'Review prices and permissions'),el('button',{type:'button',onclick:onReturn},'Return to footage setup'));
  if(p?.media_prices)box.append(priceReview(p));
  else box.append(el('p',{role:'alert'},p?.error||'These settings could not be read. Prepare their review again.'));
  box.append(stale?el('p',{},'This settings request has been decided. Return to its form to continue.'):actions);return box;
}
