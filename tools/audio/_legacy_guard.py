"""Legacy audio is allowed only for one positively verified older production."""
import json
import os
from pathlib import Path

MESSAGE = 'Audio for this production goes only through an approved request.'
NO_PRODUCTION = 'Audio now needs an identified production with verified signed settings.'
UNVERIFIED = 'This production\'s signed settings or approval history cannot be verified. No audio was sent.'
BAD_PATH = 'Audio paths must stay inside one production and must not use links.'

def _checked_path(value):
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError(BAD_PATH)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor.parent if part == '..' else cursor / part
        if cursor.is_symlink():
            # macOS system aliases are not production-controlled links.
            allowed = {Path('/tmp'): Path('/private/tmp'), Path('/var'): Path('/private/var')}
            if cursor not in allowed or cursor.resolve() != allowed[cursor]:
                raise ValueError(BAD_PATH)
    return Path(os.path.abspath(path)).resolve()

def _enclosing_root(path):
    directory = path if path.is_dir() else path.parent
    for candidate in (directory, *directory.parents):
        if any((candidate / name).exists() for name in ('project.json', 'project.yaml', 'approvals.jsonl')):
            return candidate
    return None

def _read_marker(path):
    _checked_path(path)
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(UNVERIFIED)
    return value

def refusal(inputs):
    """No verification failure or missing attribution can authorize a POST."""
    from lib.project_config import load_current_verified_project_config
    from lib.run_common import resolve_project_root
    from lib.trailer_contract import signed_trailer_pin
    if not isinstance(inputs, dict):
        return NO_PRODUCTION
    try:
        explicit = _checked_path(inputs['project_dir']) if inputs.get('project_dir') else None
        output = _checked_path(inputs.get('output_path') or (Path.cwd() / 'legacy-audio'))
        cwd = _checked_path(Path.cwd())
        roots = set()
        for path in (explicit, output, cwd):
            if path is None:
                continue
            found = _enclosing_root(path)
            if path == explicit and found is None:
                return NO_PRODUCTION
            if found is not None:
                roots.add(found)
        if not roots:
            return NO_PRODUCTION
        if len(roots) != 1:
            return BAD_PATH
        root = roots.pop()
        for path in (explicit, output):
            if path is not None:
                path.relative_to(root)
        # Signed files copied under the same name still verify. Only the
        # registered direct child of PROJECTS_DIR can use the legacy route,
        # and not even a configured registry may hide an enclosing production.
        if _enclosing_root(root.parent) is not None:
            return BAD_PATH
        if _checked_path(resolve_project_root(root.name)) != root:
            return BAD_PATH
    except (OSError, ValueError, TypeError, RuntimeError):
        return BAD_PATH
    try:
        marker = _read_marker(root / 'project.json')
        if marker.get('production_kind') == 'trailer' or marker.get('pipeline_type') == 'authored-film@1.6':
            return MESSAGE
        for path in root.glob('checkpoint_*.json'):
            pin = _read_marker(path).get('pipeline') or {}
            if pin == 'authored-film@1.6':
                return MESSAGE
            if isinstance(pin, dict) and pin.get('name') == 'authored-film' and pin.get('version') == '1.6':
                return MESSAGE
        for name in ('project.yaml', 'approvals.jsonl'):
            _checked_path(root / name)
        config = load_current_verified_project_config(root)
        if config.version == '1.3' or signed_trailer_pin(root) is not None:
            return MESSAGE
        if config.version not in ('1.0', '1.1', '1.2'):
            return UNVERIFIED
    except Exception:
        # Never convert a missing signature, damaged chain or unreadable
        # configuration into permission to use the old paid route.
        return UNVERIFIED
    return None
