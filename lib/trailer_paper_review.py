"""One exact paper-cut review through the existing artifact signing gate."""
import copy
import hashlib
from pathlib import Path

from lib import receipts, trailer_edit
from lib.canonical_json import record_sha256

CHANGED='The paper cut or approved story changed. Reopen Paper cut workspace, save and compare the paper cut, then review it again.'


def material(root):
    from lib.footage_requests import require_trailer
    root=Path(root);require_trailer(root)
    saved=trailer_edit.read_revision(root)
    if not saved or not saved.get('paper_cut'):
        raise ValueError('Save the complete paper cut in Paper cut workspace before reviewing it.')
    inputs=trailer_edit._inputs(root)
    check=trailer_edit._validate_paper(root,saved['paper_cut'])
    if saved.get('inputs')!=inputs or check['status']!='checked':
        raise ValueError('Compare the saved paper cut against the approved story in Paper cut workspace before signing it.')
    # The paper content is the authority here, not a later edit revision.
    # Keep the literal strings as well as their byte fingerprints.
    paper=copy.deepcopy(saved['paper_cut'])
    return {'project_id':root.name,'trailer':saved['trailer'],'paper_cut':paper,
            'story_inputs':inputs,'story_check':check,
            'word_fingerprints':{key:[hashlib.sha256(row['text'].encode('utf-8')).hexdigest() for row in paper[key]]
                                 for key in ('beats','lines','cards')}}


def state(root):
    rows=[row for row in receipts.verified_approvals(root,'artifact_review')
          if row.get('scope')=='trailer-paper' and row.get('stage')=='script']
    if not rows:return 'Needs your signature'
    try:record=material(root)
    except (ValueError,OSError,KeyError):return 'Needs a fresh look'
    row=rows[-1]
    return 'Signed by you' if row.get('record')==record else 'Needs a fresh look'


def request_review(root,revision):
    from lib.media_spending import gate_request
    saved=trailer_edit.read_revision(root)
    if not saved or saved['edit_revision_sha256']!=revision:raise ValueError(CHANGED)
    record=material(root)
    return gate_request(root,'artifact_review','paper-cut',record,
        'Review the complete saved paper cut: beats, exact spoken lines and cards, shot list, timing and story warnings. This buys nothing and completes no stage.',
        scope='trailer-paper',stage='script')


def signing_material(root,request):
    record=material(root)
    if (request.get('scope')!='trailer-paper' or request.get('stage')!='script'
            or request.get('entity_id')!='paper-cut' or request.get('project_id')!=Path(root).name
            or request.get('approval_record')!=record or request.get('record_sha256')!=record_sha256(record)):
        raise ValueError(CHANGED)
    return record


def packet(root,request):
    try:
        record=signing_material(root,request)
        from lib.canon_enforcement import _load_canon
        canon=_load_canon(Path(root).parent,Path(root).name,{}) or {}
        return {'paper_review':record,'protected_lines':[r['line'] for r in canon.get('protected_lines',[])],
                'sources':trailer_edit.paper_state(root)['sources']}
    except (ValueError,OSError,KeyError):return {'packet_error':True,'error':CHANGED}
