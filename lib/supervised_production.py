"""A supervised shot notebook. Production choices never write canon or receipts.

The assistant records Ben's words once. Existing paid tools still own reference
verification, provider submissions, generation receipts and reconciliation.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lib.pathsafe import sha256_file
from lib.state_io import append_jsonl, read_jsonl

SUPPORTED_TOOLS = {'kling_reference_video', 'seedance_video', 'seedream_image'}


def shot_dir(root, shot_id):
    root = Path(root).resolve()
    if not isinstance(shot_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', shot_id):
        raise ValueError('shot_id must contain only letters, numbers, underscores and hyphens')
    path = root / 'production' / 'shots' / shot_id
    path.resolve().relative_to(root)
    return path


def history(root, shot_id):
    return read_jsonl(shot_dir(root, shot_id) / 'history.jsonl')


def _append(root, shot_id, event):
    from tools.cost_tracker import reservation_lock
    with reservation_lock(Path(root)):
        directory = shot_dir(root, shot_id)
        directory.mkdir(parents=True, exist_ok=True)
        event = {'event_id': uuid.uuid4().hex, 'at': datetime.now(timezone.utc).isoformat(), **event}
        append_jsonl(directory / 'history.jsonl', event)
    return event


def _note(user_note):
    if not isinstance(user_note, str) or not user_note.strip():
        raise ValueError('Record the actual conversational instruction in user_note')
    return user_note


def _number(value, name, *, integral=False):
    if (isinstance(value, bool) or not isinstance(value, int if integral else (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f'{name} must be a finite nonnegative {"integer" if integral else "number"}')
    return value


def _copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open('xb') as out, Path(source).open('rb') as incoming:
            shutil.copyfileobj(incoming, out)
    except FileExistsError:
        if sha256_file(destination) != sha256_file(source):
            raise ValueError(f'Existing preserved file differs: {destination}')


def prepare(root, spec, *, user_note):
    """Save a new brief revision; the shot id retains all its previous spend."""
    root = Path(root).resolve()
    brief = json.loads(json.dumps(spec, allow_nan=False))
    shot_id = brief['shot_id']
    directory = shot_dir(root, shot_id)
    _note(user_note)
    if not brief.get('direction') or not brief.get('source_paths'):
        raise ValueError('direction and source_paths (canon and relevant look tickets) are required')
    _number(brief.get('spend_allowance_usd'), 'spend_allowance_usd')
    _number(brief.get('max_video_takes'), 'max_video_takes', integral=True)
    if not brief.get('allowed_tools') or not set(brief['allowed_tools']) <= SUPPORTED_TOOLS:
        raise ValueError('allowed_tools must name supported governed paid tools')
    from lib.receipts import normalize_look_ref, normalize_reference
    from lib import pathsafe
    looks = [normalize_look_ref(ref) for ref in brief.get('look_refs', [])]
    if not looks:
        raise ValueError('Select the established look_refs for the shot')
    entities = {ref['entity_id'] for ref in looks}
    refs = [normalize_reference(ref) for ref in brief.get('reference_manifest', [])]
    for ref in refs:
        file = pathsafe.resolve_input(ref.get('path'), root)
        if ref.get('visual_bible_entity_id') not in entities or ref['asset_id'] != sha256_file(file):
            raise ValueError('Each reference must match its selected entity and actual file hash')
        ref['path'] = file.relative_to(root).as_posix()
    brief['look_refs'], brief['reference_manifest'] = looks, refs
    snapshots = []
    for raw in brief['source_paths']:
        source = Path(raw)
        source = (root / source).resolve() if not source.is_absolute() else source.resolve()
        digest = sha256_file(source)
        copy = directory / 'sources' / digest
        _copy(source, copy)
        snapshots.append({'path': str(source), 'sha256': digest,
                          'snapshot_path': copy.relative_to(root).as_posix()})
    brief['source_snapshots'] = snapshots
    return _append(root, shot_id, {'kind': 'brief', 'brief': brief, 'user_note': user_note})


def read_brief(root, shot_id):
    brief = None
    for row in history(root, shot_id):
        if row['kind'] == 'brief':
            brief = {**row['brief'], 'revision_id': row['event_id'], 'stopped': False}
        elif row['kind'] == 'stop' and brief is not None:
            brief['stopped'] = True
    return brief


def stop(root, shot_id, *, user_note):
    if read_brief(root, shot_id) is None:
        raise ValueError('Unknown shot')
    return _append(root, shot_id, {'kind': 'stop', 'user_note': _note(user_note)})


def request(root, shot_id, *, prompt, output_path, **settings):
    """Build inputs with the exact saved references. No provider call occurs."""
    brief = read_brief(root, shot_id)
    if brief is None or brief['stopped']:
        raise ValueError('Shot is missing or stopped')
    output = Path(output_path)
    if not output.is_absolute():
        output = Path(root) / output
    output.resolve().relative_to(Path(root).resolve())
    if output.exists():
        raise ValueError('Choose a new output_path; preserve the existing take')
    return {**settings, 'project_dir': str(Path(root).resolve()), 'shot_id': shot_id,
            'asset_class': 'supervised_shot', 'prompt': prompt, 'output_path': str(output),
            'look_refs': brief['look_refs'], 'reference_manifest': brief['reference_manifest'],
            'reference_image_paths': [str(Path(root) / ref['path']) for ref in brief['reference_manifest']]}


def attach(root, shot_id, source, *, user_note, cost_usd=None, provider_job_id=None,
           reservation_id=None, prompt=None, references=None):
    """Preserve a take. External metadata is reported, never a fabricated receipt."""
    root, source = Path(root).resolve(), Path(source).resolve()
    brief = read_brief(root, shot_id)
    if brief is None:
        raise ValueError('Prepare the shot first')
    if cost_usd is not None:
        _number(cost_usd, 'cost_usd')
    _note(user_note)
    digest = sha256_file(source)
    duration = None
    if source.suffix.lower() in {'.mp4', '.mov', '.webm', '.mkv'}:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                '-of', 'json', str(source)], capture_output=True, text=True, check=True)
        duration = float(json.loads(probe.stdout)['format']['duration'])
        _number(duration, 'duration')
    elif source.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}:
        from PIL import Image
        with Image.open(source) as image:
            image.verify()
    else:
        raise ValueError('Attach a supported video or image file')
    if reservation_id:
        from tools.cost_tracker import load_reservations
        reservation = load_reservations(root).get(reservation_id)
        if (not reservation or reservation.get('scope') != f'shot:{shot_id}'
                or reservation.get('state') != 'completed'):
            raise ValueError('reservation_id must name a completed paid call for this shot')
        hint = reservation.get('output_hint') or {}
        if not hint.get('output_path') or sha256_file(Path(hint['output_path'])) != digest:
            raise ValueError('Take does not match the completed reservation output')
        cost_usd, provider_job_id = reservation.get('actual_usd'), reservation.get('provider_request_id')
    destination = shot_dir(root, shot_id) / 'takes' / (digest + source.suffix.lower())
    _copy(source, destination)
    return _append(root, shot_id, {
        'kind': 'take', 'take_id': uuid.uuid4().hex, 'path': destination.relative_to(root).as_posix(),
        'sha256': digest, 'duration_seconds': duration, 'cost_usd': cost_usd,
        'provider_job_id': provider_job_id, 'reservation_id': reservation_id,
        'provenance': 'reservation' if reservation_id else 'external_reported',
        'prompt': prompt, 'references': references, 'brief_revision_id': brief['revision_id'],
        'user_note': user_note,
    })


def select(root, shot_id, take_id, *, user_note, start=None, end=None):
    take = next((row for row in history(root, shot_id)
                 if row['kind'] == 'take' and row['take_id'] == take_id), None)
    if take is None:
        raise ValueError('Unknown take')
    if sha256_file(Path(root) / take['path']) != take['sha256']:
        raise ValueError('Preserved take changed on disk')
    if start is not None or end is not None:
        _number(start, 'start'); _number(end, 'end')
        if take['duration_seconds'] is None or not start < end <= take['duration_seconds']:
            raise ValueError('Selection must be within the take duration')
    from lib.run_common import write_decision
    note = _note(user_note)
    event = _append(root, shot_id, {'kind': 'selection', 'take_id': take_id,
                    'start': start, 'end': end, 'user_note': note, 'canon_status': 'unchanged'})
    write_decision(Path(root), stage='assets', category='revision', subject=f'Shot {shot_id} selection',
                   reason=note, selected=take_id, user_approved=True)
    return event


def propose_change(root, shot_id, text):
    """Local unratified input for the existing notes -> wayfinder process."""
    if read_brief(root, shot_id) is None:
        raise ValueError('Unknown shot')
    note = _note(text)
    notes = Path(root) / 'notes'
    notes.mkdir(exist_ok=True)
    path = notes / f'production-proposal-{uuid.uuid4().hex}.md'
    with path.open('x') as out:
        out.write(f'# Unratified production proposal\n\nShot: {shot_id}\n\n{note}\n\n'
                  'Status: unratified. Route through story-wayfinder before changing the Canon Note.\n')
    return _append(root, shot_id, {'kind': 'canon_proposal', 'status': 'unratified',
                    'path': path.relative_to(root).as_posix(), 'text': note})


def _source_warnings(root, brief):
    warnings = []
    for snapshot in brief['source_snapshots']:
        path = Path(snapshot['path'])
        if not path.is_file() or sha256_file(path) != snapshot['sha256']:
            warnings.append(f'Source changed; review this shot: {path}')
    from lib.look_ingest import verify_look_refs
    for ref in brief['look_refs']:
        try:
            verify_look_refs(root, [ref])
        except Exception as exc:
            warnings.append(f"Look {ref['entity_id']} needs review: {exc}")
    return warnings


def inspect_shot(root, shot_id):
    brief = read_brief(root, shot_id)
    if brief is None:
        raise ValueError('Unknown shot')
    warnings = _source_warnings(root, brief)
    rows = history(root, shot_id)
    revisions = {r['event_id']: r['brief'] for r in rows if r['kind'] == 'brief'}
    takes = [{**r, 'source_warnings': _source_warnings(root, revisions[r['brief_revision_id']])}
             for r in rows if r['kind'] == 'take']
    return {'brief': brief, 'warnings': warnings,
            'takes': takes,
            'selections': [r for r in rows if r['kind'] == 'selection'],
            'proposals': [r for r in rows if r['kind'] == 'canon_proposal']}
