"""The writer's final listening comparison, separate from technical checks.

The literal answers live in the existing signed artifact-review receipt, not
in the editable export report. No machine claims it understood spoken words.
"""
import copy
from pathlib import Path
from lib.canonical_json import record_sha256


def questions(root):
    from lib.canon_enforcement import _entity_names
    from lib.trailer_contract import signed_trailer_pin,require_stage_approval
    from lib.story_ingest import material
    import json
    root=Path(root)
    checkpoint=json.loads((root/'checkpoint_canon_ingest.json').read_text())
    artifacts=checkpoint['artifacts'];canon=artifacts['canon_packet']
    require_stage_approval(root,signed_trailer_pin(root),'canon_ingest',artifacts)
    material(root,canon)
    return {'story_sha256':record_sha256(canon),
            'protected_lines':[row['line'] for row in canon.get('protected_lines',[])],
            'locks':[{'id':row['id'],'wording':row.get('decision') or row.get('summary') or row['id'],
                      'spoiler':row.get('spoiler') is True} for row in canon.get('locks',[])],
            'entities':sorted(_entity_names(canon)),
            'tone':canon.get('tone') or {'tone_words':['Compare with the exact approved story.']}}


def validate(root,value):
    from lib.canon_enforcement import _check_canon_pass
    from lib import trailer_edit,audio_workspace as audio
    import jsonschema
    import json
    root=Path(root);asked=questions(root)
    if not isinstance(value,dict) or set(value)!={'story_sha256','canon_pass'}:
        raise ValueError('Watch the final file and complete its final story comparison, including every protected line heard verbatim, before signing.')
    if value['story_sha256']!=asked['story_sha256']:
        raise ValueError('The approved story changed. Reopen the final story comparison and review the new version.')
    canon=json.loads((root/'checkpoint_canon_ingest.json').read_text())['artifacts']['canon_packet']
    check=value['canon_pass']
    try:
        schema=json.loads((Path(__file__).resolve().parents[1]/'schemas/artifacts/final_review.schema.json').read_text())
        jsonschema.validate(check,schema['properties']['checks']['properties']['canon_pass'])
        _check_canon_pass({'checks':{'canon_pass':check}},canon,True)
    except (ValueError,jsonschema.ValidationError):
        raise ValueError('The final story comparison is incomplete. Confirm each story finding and that every protected line is heard verbatim; fix the cut if it is not.') from None
    # A checked box cannot put a missing line in the cut. This verifies only
    # the exact approved source words; Ben must hear every word in the file,
    # since placement and measurement alone cannot certify intelligibility.
    saved=trailer_edit.read_revision(root);words=[]
    for row in (saved or {}).get('timeline',{}).get('audio',[]):
        take=audio.require_accepted(root,row['take_id'])
        if take['kind']=='speech':words.append(audio.approved_source_request(root,take)['prompt'])
    for line in asked['protected_lines']:
        if not any(line in text for text in words):
            raise ValueError('This protected line has no kept spoken take in the cut: '+line+' Add its exact words as voice, keep and place the take, then review the new cut.')
    return copy.deepcopy(value)
