"""Compute ceiling charges only from Ben's current signed price basis."""
import math
import time
from decimal import Decimal, ROUND_CEILING
from lib.canonical_json import record_sha256
from lib.project_config import load_current_verified_project_config,read_project_config,ProjectConfigError

MISSING_BASIS = 'No audio price basis has been signed for these settings yet. This production needs a signed price basis before audio can be approved.'


def quote(root,kind,prompt,seconds):
    try:
        config=load_current_verified_project_config(root)
    except ProjectConfigError:
        # A newly created trailer deliberately has no spending consent. Reading
        # that draft can explain a refusal; it can never authorize a request.
        data,_=read_project_config(root)
        if not isinstance(data.get('audio_price_basis'),dict):raise ValueError(MISSING_BASIS)
        raise
    basis=config.data.get('audio_price_basis')
    if not isinstance(basis,dict):raise ValueError(MISSING_BASIS)
    if kind not in ('speech','music'):raise ValueError('Choose speech or instrumental music.')
    if not isinstance(prompt,str) or not prompt.strip():raise ValueError('Give the exact words or music brief.')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=120:
        raise ValueError('Audio length must be positive and no more than two minutes.')
    keys=['dollars_per_credit','speech_credits_per_character' if kind=='speech' else 'music_credits_per_minute']
    # Until the catalogue integration can positively identify standard-rate
    # voices, all selected voices require the signed multiplier ceiling.
    if kind=='speech':keys.append('voice_multiplier_ceiling')
    now=time.time();expiry=float('inf');values={}
    for key in keys:
        rate=basis.get(key)
        if not isinstance(rate,dict):raise ValueError('The signed price basis needs a voice multiplier ceiling.' if key=='voice_multiplier_ceiling' else 'The signed price basis is incomplete.')
        value=rate.get('value');start=rate.get('set_at');end=rate.get('expires_at')
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in (value,start,end)) or value<=0 or not start<=now<end:
            raise ValueError('The signed price basis has expired or is not yet valid. Sign a current basis before audio approval.')
        values[key]=Decimal(str(value));expiry=min(expiry,end)
    if kind=='speech':
        credits=Decimal(len(prompt.encode('utf-8')))*values['speech_credits_per_character']*values['voice_multiplier_ceiling']
    else:credits=Decimal(str(seconds))/Decimal(60)*values['music_credits_per_minute']
    credits=credits.to_integral_value(rounding=ROUND_CEILING)
    dollars=(credits*values['dollars_per_credit']).quantize(Decimal('.0001'),rounding=ROUND_CEILING)
    return {'credits':int(credits),'max_credits_usd':float(dollars),'max_cash_usd':0,
            'price_expires_at':expiry,'price_basis_sha256':record_sha256(basis)}
