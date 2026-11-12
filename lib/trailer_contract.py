"""Small trailer envelope and board descriptions; no production orchestration."""
import json
from pathlib import Path

import jsonschema

SCHEMA = Path(__file__).resolve().parents[1] / 'schemas/trailer_contract.schema.json'


def make_contract(seconds):
    contract = {'kind': 'trailer', 'target_duration_seconds': seconds, 'fps': 24,
                'approval_points': {'edit': 'rough_cut', 'compose': 'final_export'}}
    try:
        jsonschema.validate(contract, json.loads(SCHEMA.read_text()))
    except jsonschema.ValidationError as exc:
        raise ValueError('Choose a trailer length greater than zero and no more than two minutes.') from exc
    return contract


STAGE_LABELS = {
    'canon_ingest': 'Story', 'proposal': 'Treatment', 'look_lock': 'Looks',
    'headshots': 'Cast portraits', 'visual_bible': 'Visual references',
    'script': 'Lines and title cards', 'scene_plan': 'Shot list',
    'assets': 'Footage and sound', 'edit': 'Rough cut', 'compose': 'Final assembly',
}
STAGE_LIMITS = {
    'canon_ingest': 'Starting a trailer without commands is not available yet.',
    'proposal': 'Trailer treatment controls are not available yet.',
    'script': 'Line and title-card editing controls are not available yet.',
    'scene_plan': 'Shot-list editing controls are not available yet.',
    'assets': 'Individual footage tools exist. The complete trailer sound workflow is not available yet.',
    'edit': 'Playable rough-cut editing and approval controls are not available yet.',
    'compose': 'Trailer finishing, export and exact-file approval controls are not available yet.',
}


def validate_stage_contract(marker_contract, stage, artifacts):
    """New trailer outputs carry the same envelope; older pins never call this."""
    names = {'script': ['script'], 'scene_plan': ['scene_plan'],
             'edit': ['edit_decisions'], 'compose': ['render_report', 'final_review']}.get(stage, [])
    if not names:
        return
    expected = make_contract((marker_contract or {}).get('target_duration_seconds'))
    for name in names:
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict) or artifact.get('trailer') != expected:
            raise ValueError('Each trailer stage must carry the saved trailer contract unchanged.')
