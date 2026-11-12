"""Refuse legacy ElevenLabs submissions for identified protected productions."""
import json
from pathlib import Path

MESSAGE = 'Audio for this production goes only through an approved request.'

def refusal(inputs):
    from lib.events import infer_project_dir
    from lib import gates
    from lib.project_config import read_project_config, load_verified_project_config
    root = infer_project_dir(inputs)
    if root is None and inputs.get('project_dir'):
        root = Path(inputs['project_dir'])
    if root is None:
        root = infer_project_dir({'output_path': str(Path.cwd() / 'audio-output')})
    if root is None:
        return None  # Unattributed legacy calls retain their existing behavior.
    root = Path(root)
    def read(path):
        try:
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, RecursionError):
            return {}
    marker = read(root/'project.json')
    if marker.get('production_kind') == 'trailer' or marker.get('pipeline_type') == 'authored-film@1.6':
        return MESSAGE
    for path in root.glob('checkpoint_*.json'):
        pin = read(path).get('pipeline') or {}
        if isinstance(pin,dict) and pin.get('name')=='authored-film' and pin.get('version')=='1.6':
            return MESSAGE
    # A surviving signed trailer record remains identity evidence even when
    # its local history is broken. It supplies no spending authority.
    try:
        for line in (root/'approvals.jsonl').read_text().splitlines():
            try:
                row = json.loads(line)
                record = row.get('record') or {}
                if (row.get('project_id')==root.name and row.get('kind')=='pipeline_migration'
                    and record.get('pipeline_name')=='authored-film' and record.get('version')=='1.6'
                    and gates.verify_receipt(row)):
                    return MESSAGE
            except (ValueError, TypeError, AttributeError, RecursionError):
                continue
    except OSError:
        pass
    try:
        data,_ = read_project_config(root)
        if data.get('version')=='1.3' and load_verified_project_config(root).version=='1.3':
            return MESSAGE
    except (OSError, ValueError, RuntimeError):
        pass
    return None
