"""Exactly one synchronous ElevenLabs POST behind the signed audio boundary."""
import os
from decimal import Decimal
from pathlib import Path
from tools.base_tool import ToolResult
from tools.audio import _governed

REFUSAL = 'Audio now needs your signed approval of each request. Audio controls in the app are not available yet.'


def _execute_locked(inputs,kind):
    if not inputs.get('project_dir') or not inputs.get('audio_request_id'):
        return ToolResult(success=False,error=REFUSAL)
    contacted=False
    try:
        if set(inputs)-{'project_dir','audio_request_id','output_path','preferred_provider','force_instrumental'}:
            raise ValueError('Audio inputs changed. Prepare a new request instead of overriding approved material.')
        if inputs.get('preferred_provider','elevenlabs') not in ('auto','elevenlabs'):
            raise ValueError('The approved request uses ElevenLabs. A different provider needs a new approved request.')
        from lib.audio_workspace import check_price,generated_take
        from lib.receipts import verified_generation_receipts, find_approval
        from lib.canonical_json import record_sha256
        from tools.cost_tracker import resume_check
        root=_governed._root(Path(inputs['project_dir']).expanduser())
        record=_governed.read_request(root,inputs['audio_request_id'])
        if record['kind']!=kind:raise ValueError('This approval is for a different kind of audio.')
        if inputs.get('output_path'):
            from tools.audio._legacy_guard import _checked_path
            expected=root/'assets/audio'/(record['request_id']+'.audio')
            if _checked_path(inputs['output_path'])!=expected:
                raise ValueError('The output path differs from the approved take location.')
        # Recovery may finish a crashed download, but never submits again.
        resume_check(root)
        approval=find_approval(root,'audio_spend',record_sha256=record_sha256(record),entity_id=record['request_id'])
        for row in verified_generation_receipts(root):
            if approval and row.get('audio_approval_receipt_id')==approval['receipt_id']:
                return ToolResult(success=True,data={'take':generated_take(root,record,row)})
        check_price(root,record)
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',record['model']) or (kind=='speech' and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',record['voice'])):
            raise ValueError('The approved model or voice identifier is invalid.')
        def submit(payload,reservation):
            nonlocal contacted
            import requests
            key=os.environ.get('ELEVENLABS_API_KEY')
            if not key:raise ValueError('Connect your ElevenLabs account before submitting audio.')
            if kind=='speech':
                url='https://api.elevenlabs.io/v1/text-to-speech/'+payload['voice']
                body={'text':payload['prompt'],'model_id':payload['model']}
            else:
                url='https://api.elevenlabs.io/v1/music'
                body={'prompt':payload['prompt'],'model_id':payload['model'],
                      'music_length_ms':round(payload['duration_seconds']*1000),'force_instrumental':True}
            config=_governed.check_current(root,record)
            rate=Decimal(str(config.data['audio_price_basis']['dollars_per_credit']['value']))
            try:
                contacted=True
                response=requests.post(url,headers={'xi-api-key':key,'Content-Type':'application/json'},json=body,timeout=120,allow_redirects=False)
                response.raise_for_status()
            except Exception:
                raise RuntimeError('The service outcome is unknown; the reservation is held.') from None
            if not 200<=response.status_code<300:raise ValueError('The audio service did not return a successful response. Billing needs review.')
            # ElevenLabs reports consumed credits as character-cost. Missing or
            # invalid billing evidence leaves the durable reservation pending.
            credits=Decimal(response.headers['character-cost'])
            if not credits.is_finite() or credits<0:raise ValueError('The audio service did not report a valid credit charge.')
            charge=(credits*rate).quantize(Decimal('.0001'),rounding='ROUND_CEILING')
            return {'request_id':response.headers['request-id'],'cash_usd':0,'credits_usd':float(charge),'response':response}
        rows=_governed.execute(root,record,submit=submit,download=lambda result:result['response'].content)
        return ToolResult(success=True,data={'take':generated_take(root,record,rows[0]),'receipts':rows})
    except Exception as exc:
        # Never put raw provider messages, credentials or payload echoes on a page.
        from tools.cost_tracker import IndeterminatePaidCallError
        message=str(exc) if isinstance(exc,(ValueError,IndeterminatePaidCallError)) else ('The audio request did not finish. Check its recorded receipt and billing outcome before another request.' if contacted else 'Nothing was sent. Check this production\'s signed settings, approved request and remaining budget.')
        return ToolResult(success=False,error=message)


def execute(inputs,kind):
    if kind=='music' and isinstance(inputs,dict) and inputs.get('force_instrumental') is False:
        return ToolResult(success=False,error='Music requests are always instrumental. No request was sent.')
    if not isinstance(inputs,dict) or not inputs.get('project_dir') or not inputs.get('audio_request_id'):
        from tools.audio._legacy_guard import refusal
        message=refusal(inputs) if isinstance(inputs,dict) else None
        return ToolResult(success=False,error=(message or 'Audio for this production goes only through an approved request.')+' '+REFUSAL)
    from lib.audio_workspace import call_lock
    try:
        with call_lock(Path(inputs['project_dir']).expanduser()):
            return _execute_locked(inputs,kind)
    except (ValueError,OSError,RuntimeError):
        return ToolResult(success=False,error='Audio is unavailable or still running. Check the production and its unresolved requests.')


def estimate(inputs,kind):
    if not isinstance(inputs,dict) or not inputs.get('project_dir') or not inputs.get('audio_request_id'):
        raise ValueError('A firm approved price bound is required. Historical estimates are not spending permission.')
    from lib.audio_workspace import check_price
    root=_governed._root(Path(inputs['project_dir']).expanduser())
    record=_governed.read_request(root,inputs['audio_request_id'])
    if record['kind']!=kind:raise ValueError('This request is for a different kind of audio.')
    _governed.check_current(root,record)
    price=check_price(root,record)
    return price['max_cash_usd']+price['max_credits_usd']
