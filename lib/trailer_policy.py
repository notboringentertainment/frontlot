"""Signed trailer definitions and Ben's trailer-test decision."""
import re

CURRENT_VERSION='1.7'
CURRENT_REF='authored-film@'+CURRENT_VERSION
DECISION='trim for the trailer test'
DROPPED_REVIEWS=(
    'Treatment with three options', 'Looks for the complete cast', 'Cast portraits',
    'Visual references with an approved poster', 'Separate lines and title cards',
    'Separate shot list', 'Whole footage and sound collection',
)
REVIEW_LABELS={'canon_ingest':'Story','script':'Paper cut','edit':'Rough cut',
               'colour_mix':'Colour and sound','compose':'Final file'}


def is_trailer(value):
    """New versions inherit the restrictive trailer identity, never bare flags."""
    if isinstance(value,str):
        name,_,version=value.partition('@')
    elif isinstance(value,dict):name,version=value.get('name'),value.get('version')
    else:name,version=getattr(value,'name',None),getattr(value,'version',None)
    if name!='authored-film' or not isinstance(version,str):return False
    match=re.fullmatch(r'(\d+)\.(\d+)',version)
    return bool(match and tuple(map(int,match.groups()))>=(1,6))


def trimmed(value):
    version=value.partition('@')[2] if isinstance(value,str) else value.get('version') if isinstance(value,dict) else getattr(value,'version',None)
    return is_trailer(value) and version==CURRENT_VERSION


def signed_decision():
    return {'decision_by':'Ben','decision':DECISION,
            'dropped_whole_stage_reviews':list(DROPPED_REVIEWS),
            'required_reviews':list(REVIEW_LABELS.values()),
            'every_protected_line_must_be_heard_verbatim':True,
            'individual_purchase_and_media_approvals_remain_required':True}


def request_update(root):
    import json,uuid
    from pathlib import Path
    from lib.trailer_contract import signed_trailer_pin,require_contract
    from lib.pipeline_pin import prepare_migration_request
    root=Path(root);pin=signed_trailer_pin(root)
    if pin is None:raise ValueError('Sign this trailer’s creation before reviewing its contract.')
    if trimmed(pin):raise ValueError('This trailer already uses the signed trailer-test contract.')
    require_contract(root)
    path=prepare_migration_request(root,root.name,'authored-film',CURRENT_VERSION,
        request_id='trailer-test-'+uuid.uuid4().hex,
        summary='Ben decided: “trim for the trailer test”. Review the five remaining signed reviews and the seven whole-stage reviews being set aside. Every protected line must still be heard verbatim. No stage or spending is approved by this contract signature.')
    return json.loads(path.read_text())
