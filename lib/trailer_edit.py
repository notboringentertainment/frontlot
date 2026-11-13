"""Local trailer drafts and the single, versioned edit record. No generation."""
import copy
import fcntl
import json
import math
import os
import secrets
import shutil
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from lib.canonical_json import record_sha256
from lib.trailer_contract import make_contract, require_contract, signed_trailer_pin, edit_digest


def _creation_values(title, seconds, ceiling):
    contract = make_contract(seconds)
    if (not isinstance(title, str) or not title.strip() or len(title) > 200
            or any(unicodedata.category(c).startswith('C') for c in title)):
        raise ValueError('Give the trailer a name of up to two hundred characters, on one line without control characters.')
    if (isinstance(ceiling, bool) or not isinstance(ceiling, (float, int))
            or not math.isfinite(ceiling) or not 0 <= ceiling <= 1_000_000):
        raise ValueError('Choose a total spending ceiling between zero and one million dollars.')
    if Decimal(str(ceiling)) * 100 != (Decimal(str(ceiling)) * 100).to_integral_value():
        raise ValueError('Give the spending ceiling in whole dollars and cents.')
    return {'title': title.strip(), 'trailer': contract, 'spending_ceiling_usd': ceiling}


def _name_key(title):
    return unicodedata.normalize('NFKC', title).strip().casefold()


def _creation_request(root):
    from lib.pipeline_pin import prepare_migration_request
    facts = creation_facts(root)
    prepare_migration_request(root, root.name, 'authored-film', '1.6', trailer_creation=True,
        summary=f"Start {facts['title']}, lasting {facts['trailer']['target_duration_seconds']:g} seconds, with a total ceiling of ${facts['spending_ceiling_usd']:.2f}. This signs creation only. It approves no stage and authorises no spending. Separate signed spending settings are still required before any paid work.")


def create_trailer(base, title, seconds, ceiling, request_key=None):
    """Stage the complete unsigned production on the same disk, then publish once.

    A request token covers double clicks and retries. Independently arriving
    same-name requests that overlap are coalesced under the creation lock.
    """
    arrived = time.time_ns()
    facts = _creation_values(title, seconds, ceiling)
    if request_key is not None and (not isinstance(request_key, str) or not 1 <= len(request_key) <= 100):
        raise ValueError('The start request is invalid. Close this panel and try again.')
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    with (base/'.trailer-create.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for child in base.iterdir():
            if child.name.startswith('.') or not child.is_dir():
                continue
            try:
                marker = json.loads((child/'project.json').read_text())
            except (OSError, ValueError):
                continue
            if _name_key(str(marker.get('title', ''))) != _name_key(facts['title']):
                continue
            same = all(marker.get(k) == v for k, v in facts.items())
            repeated = request_key and marker.get('creation_request_key') == request_key
            overlapping = marker.get('creation_completed_ns', 0) >= arrived
            if same and (repeated or overlapping):
                return child
            raise ValueError('That name is already in use. Choose another trailer name.')
        from lib.checkpoint import init_project
        staging = base/'.trailer-staging'
        staging.mkdir(exist_ok=True)
        if staging.is_symlink():
            raise ValueError('The trailer could not be created. The staging folder is unsafe.')
        # These are exclusively app-owned abandoned creation transactions.
        for abandoned in staging.iterdir():
            if abandoned.name.startswith('trailer-') and abandoned.is_dir() and not abandoned.is_symlink():
                shutil.rmtree(abandoned)
        slug = 'trailer-' + secrets.token_hex(8)
        root = staging/slug
        try:
            init_project(slug, title=facts['title'], pipeline_type='authored-film@1.6',
                         pipeline_dir=staging, target_duration_seconds=seconds)
            marker = json.loads((root/'project.json').read_text())
            marker.update(spending_ceiling_usd=ceiling, trailer_creation_pending=True,
                          creation_request_key=request_key, creation_completed_ns=time.time_ns())
            (root/'project.json').write_text(json.dumps(marker, indent=2))
            # Valid settings without granting consent or inventing a judge.
            # Signed settings with provider consent remain a separate gate.
            (root/'project.yaml').write_text(
                f"version: '1.3'\nbudget_usd_cap: {ceiling}\n"
                "wall_time_minutes: 60\ncast_cap: {characters: 1, locations: 1}\n"
                "default_video_endpoint: not-selected\nprovider_egress: []\n")
            _creation_request(root)
            # Completion is stamped after the signing request exists.
            marker['creation_completed_ns'] = time.time_ns()
            (root/'project.json').write_text(json.dumps(marker, indent=2))
            os.replace(root, base/slug)
            _sync_dir(base)
        except Exception as exc:
            if root.exists():
                shutil.rmtree(root)
            elif (base/slug).exists():
                shutil.rmtree(base/slug)
            raise ValueError('The trailer could not be created. Nothing was added. Please try again.') from exc
        return base/slug


def creation_state(root):
    try:
        creation_facts(root)
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        return {'needs_repair': False, 'cannot_complete': True,
                'message': 'This unfinished draft is missing or has damaged creation details and cannot be completed. Start a new trailer under another name from the film list.'}
    requests = Path(root)/'.gate-requests'
    present = False
    for p in requests.glob('*.json'):
        try:
            request = json.loads(p.read_text())
            if 'trailer_creation' in (request.get('approval_record') or {}):
                present = True
        except (OSError, ValueError, AttributeError):
            continue
    return {'needs_repair': not present, 'message': (
        'This is an unfinished trailer draft. Its creation signing request is missing. Make that request again to continue.'
        if not present else 'Review the trailer name, length and ceiling under Needs you, then sign creation. No stage or spending is approved.')}


def repair_creation(root):
    root = Path(root)
    with (root/'.trailer-creation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if signed_trailer_pin(root) is not None:
            raise ValueError('This trailer has already been signed.')
        state = creation_state(root)
        if state.get('cannot_complete'):
            raise ValueError(state['message'])
        if state['needs_repair']:
            _creation_request(root)
    return creation_state(root)


def _inputs(root):
    result = {'project.json': record_sha256(json.loads((root/'project.json').read_text()))}
    for stage in ('canon_ingest', 'proposal', 'script'):
        p = root/f'checkpoint_{stage}.json'
        if not p.exists():
            result[p.name] = 'ABSENT'
        else:
            value = json.loads(p.read_text())
            # Approval bookkeeping is not a creative change. Artifact bytes are.
            result[p.name] = record_sha256(value.get('artifacts', value) if isinstance(value, dict) else value)
    return result


@contextmanager
def _edit_lock(root):
    directory = Path(root)/'artifacts'
    for p in (directory, directory/'edit-history', directory/'edit_decisions.json', directory/'.paper-cut.lock'):
        if p.is_symlink():
            raise ValueError('The paper cut must stay inside this production.')
    directory.mkdir(exist_ok=True)
    with (directory/'.paper-cut.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Also recover from a killed writer: no active writer can own these now.
        for pattern in ('.paper-*.tmp', '.edit_decisions.json.*.tmp'):
            for p in directory.glob(pattern):
                if p.is_file() and not p.is_symlink():
                    p.unlink()
        yield directory


def _checked_record(value):
    if not isinstance(value, dict) or value.get('edit_revision_sha256') != edit_digest(value):
        raise ValueError('The saved edit could not be read. Its earlier versions have been kept. Restore a safe copy before editing.')
    return value


def _read_current(root):
    p = Path(root)/'artifacts'/'edit_decisions.json'
    return _checked_record(json.loads(p.read_text())) if p.exists() else None


def _committed_versions(root, current):
    if not current:
        return []
    history = current.get('revision_history', [])
    if not isinstance(history, list):
        raise ValueError('The saved versions could not be read. Nothing was overwritten.')
    # Old files are reachable only through the committed previous-version chain.
    if not history:
        previous = current.get('previous_revision')
        seen = {current['edit_revision_sha256']}
        while previous and previous not in seen:
            seen.add(previous)
            if not isinstance(previous, str) or len(previous) != 64 or any(c not in '0123456789abcdef' for c in previous):
                break
            p = Path(root)/'artifacts'/'edit-history'/f'{previous}.json'
            if not p.is_file() or p.is_symlink():
                break
            old = _checked_record(json.loads(p.read_text()))
            history.insert(0, old)
            previous = old.get('previous_revision')
    return [_checked_record(x) for x in history] + [current]


def read_revision(root, revision=None):
    with _edit_lock(root):
        current = _read_current(root)
        if revision is None:
            return current
        if not isinstance(revision, str) or len(revision) != 64 or any(c not in '0123456789abcdef' for c in revision):
            raise ValueError('That saved version could not be found.')
        versions = _committed_versions(root, current)
        for index, version in enumerate(versions):
            if version['edit_revision_sha256'] == revision:
                value = copy.deepcopy(version)
                if 'revision_number' in value:
                    value['revision_history'] = [dict(v) for v in versions[:index]]
                    for prior in value['revision_history']:
                        prior.pop('revision_history', None)
                return value
        raise ValueError('That saved version could not be found.')


def validate_timeline(timeline, target_frames):
    if timeline.get('fps') != 24:
        raise ValueError('Trailer timing uses twenty-four frames per second.')
    end = 0
    for entry in timeline.get('picture', []):
        start, duration = entry.get('start_frame'), entry.get('duration_frames')
        if any(type(v) is not int for v in (start, duration)) or start < end or duration < 1:
            raise ValueError('Shots need whole-frame lengths and cannot overlap.')
        end = start + duration
    if end > 2880:
        raise ValueError('The sequence cannot exceed two minutes.')
    delta = end-target_frames
    return ('The sequence matches the target.' if delta == 0 else
            f'The sequence is {abs(delta)} frames {"over" if delta > 0 else "short of"} the target.')


def _story_check(root, paper):
    from lib.checkpoint import read_checkpoint
    from lib.canon_enforcement import check_trailer_text
    cp = read_checkpoint(root.parent, root.name, 'canon_ingest')
    canon = (cp or {}).get('artifacts', {}).get('canon_packet')
    if not cp or cp.get('status') != 'completed' or not isinstance(canon, dict):
        return {'status': 'unchecked', 'errors': [], 'warnings': [],
                'message': 'This draft is not yet checked against the approved story. Later stages cannot use it.'}
    errors, warnings = check_trailer_text(paper, canon)
    return {'status': 'failed' if errors else 'checked', 'errors': errors, 'warnings': warnings,
            'message': 'Fix the story checks below before later stages can use this draft.' if errors else 'Compared with the protected lines for exact and near-exact wording and with recorded spoiler limits. This cannot recognise every rewording or reveal; review the protected wording beside your draft.'}


def _validate_paper(root, paper):
    if (not isinstance(paper, dict) or set(paper) != {'beats', 'lines', 'cards', 'shots'}
            or any(not isinstance(paper[k], list) or any(not isinstance(r, dict) for r in paper[k]) for k in paper)):
        raise ValueError('Save the beats, spoken lines, title cards and shots together.')
    if [b.get('kind') for b in paper['beats']] != ['hook', 'escalation', 'reversal', 'invitation']:
        raise ValueError('Include a hook, escalation, reversal and final invitation in that order.')
    for b in paper['beats']:
        if not isinstance(b.get('text'), str) or not b['text'].strip():
            raise ValueError('Write each beat in words.')
    for row in paper['lines'] + paper['cards']:
        if not isinstance(row.get('text'), str) or not row['text'].strip():
            raise ValueError('Give each line and card its exact wording.')
        for key in ('start_frame', 'duration_frames'):
            if type(row.get(key)) is not int or row[key] < (1 if key == 'duration_frames' else 0):
                raise ValueError('Lines and cards need whole-frame timing.')
        if row['start_frame']+row['duration_frames'] > round(require_contract(root)['target_duration_seconds']*24):
            raise ValueError('A line or card ends after the target length.')
        if row.get('source_ref') is not None and not isinstance(row['source_ref'], str):
            raise ValueError('Choose a story reference for each line or card, or leave it unassigned in this draft.')
    if not paper['shots']:
        raise ValueError('Add at least one shot to the timed list.')
    for shot in paper['shots']:
        if type(shot.get('duration_frames')) is not int or shot['duration_frames'] < 1:
            raise ValueError('Each shot needs a positive whole-frame screen time.')
        if (not all(isinstance(shot.get(k), str) and shot[k].strip() for k in ('purpose', 'difficulty'))
                or not isinstance(shot.get('references'), list) or any(not isinstance(x, str) for x in shot['references'])):
            raise ValueError('Each shot needs its purpose, references and difficulty.')
    check = _story_check(root, paper)
    if check['errors']:
        raise ValueError(' '.join(check['errors']))
    return check


def _sync_dir(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _commit(root, value, old, inputs=None):
    directory = root/'artifacts'
    # Flat history inside the authoritative file commits with the new revision.
    # There is never an archive entry for a save that did not happen.
    versions = _committed_versions(root, old)
    value = copy.deepcopy(value)
    from lib.footage_requests import set_aside_declined
    set_aside_declined(root,value)
    value.pop('edit_revision_sha256', None)
    value.pop('revision_history', None)
    value['previous_revision'] = (old or {}).get('edit_revision_sha256')
    value['saved_at'] = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    value['revision_number'] = len(versions)+1
    # Older rough cuts predate saved_at. Preserve their last file-write time
    # before replacing that file, explicitly marked as file time in the UI.
    times = dict((old or {}).get('revision_times', {}))
    for prior in versions:
        digest = prior['edit_revision_sha256']
        if not prior.get('saved_at') and digest not in times:
            path = directory/'edit_decisions.json' if prior is old else directory/'edit-history'/f'{digest}.json'
            if path.is_file():
                times[digest] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    value['revision_times'] = times
    value['edit_revision_sha256'] = edit_digest(value)
    value['revision_history'] = [copy.deepcopy(v) for v in versions]
    for item in value['revision_history']:
        item.pop('revision_history', None)
    temp = directory/f'.paper-{secrets.token_hex(8)}.tmp'
    try:
        with temp.open('x') as f:
            json.dump(value, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if inputs is not None and inputs != _inputs(root):
            raise ValueError('The story or trailer settings changed while saving. Review them and save again.')
        os.replace(temp, directory/'edit_decisions.json')
        _sync_dir(directory)
    finally:
        if temp.exists():
            temp.unlink()
    return value


_UNSPECIFIED = object()


def save_edit_revision(root, value, expected_revision=_UNSPECIFIED):
    """Shared persistence boundary for later cutting/export code as well as drafts."""
    root = Path(root)
    if not isinstance(value, dict):
        raise ValueError('Save the edit as a complete record.')
    with _edit_lock(root):
        old = _read_current(root)
        if expected_revision is not _UNSPECIFIED:
            _expect(old, expected_revision)
        return _commit(root, value, old)


def _expect(old, expected):
    if (old or {}).get('edit_revision_sha256') != expected:
        raise ValueError('The paper cut changed in another window. Open the latest saved version below before saving; your unsaved draft is still here.')


def _paper_value(root, paper, old, inputs):
    check = _validate_paper(root, paper)
    paper = copy.deepcopy(paper)
    for index, shot in enumerate(paper['shots']):
        # Keep identities when a caller resubmits a panel without its hidden id.
        previous_shots = (old or {}).get('paper_cut', {}).get('shots', [])
        prior = previous_shots[index].get('id') if index < len(previous_shots) else None
        shot.setdefault('id', prior or 'shot-'+secrets.token_hex(8))
    ids = [s['id'] for s in paper['shots']]
    if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('Each shot must keep its own identity. Open the latest saved version and try again.')
    contract = require_contract(root)
    picture = []
    start = 0
    for shot in paper['shots']:
        picture.append({'id': shot['id'], 'kind': 'placeholder', 'label': 'Unfinished placeholder',
                        'start_frame': start, 'duration_frames': shot['duration_frames']})
        start += shot['duration_frames']
    timeline = {'fps': 24, 'picture': picture, 'audio': [], 'cards': paper['cards']}
    value = {'version': '1.0', 'render_runtime': 'remotion', 'trailer': contract, 'cuts': [],
             'paper_cut': paper, 'timeline': timeline, 'unfinished': True, 'inputs': inputs,
             'story_check': check, 'timing_message': validate_timeline(timeline, round(contract['target_duration_seconds']*24))}
    if old:
        previous = old.get('paper_cut')
        baseline = None
        if previous:
            position = 0
            pictures = []
            for shot in previous['shots']:
                pictures.append({'id':shot['id'],'kind':'placeholder','label':'Unfinished placeholder',
                                 'start_frame':position,'duration_frames':shot['duration_frames']})
                position += shot['duration_frames']
            baseline = {'fps':24,'picture':pictures,'audio':[],'cards':previous['cards']}
        bookkeeping = {'edit_revision_sha256','previous_revision','saved_at','revision_number','revision_history','revision_times','paper_cut_pending','restored_from'}
        later = bool(old.get('cuts') or (old.get('timeline') or {}) != baseline or set(old)-set(value)-bookkeeping)
        owned = value
        value = copy.deepcopy(old)
        value.update(owned)
        if later:
            value['timeline'] = copy.deepcopy(old.get('timeline', timeline))
            value['cuts'] = copy.deepcopy(old.get('cuts', []))
            value['paper_cut_pending'] = True
            value['timing_message'] = 'The paper cut changed. Later picture, sound, card timing and other edit work have been kept and need a fresh look. The version before this save is available in Saved versions.'
    return value


def save_revision(root, paper, expected_revision):
    root = Path(root)
    if signed_trailer_pin(root) is None:
        raise ValueError('Sign trailer creation before saving its paper cut.')
    with _edit_lock(root):
        old = _read_current(root)
        _expect(old, expected_revision)
        inputs = _inputs(root)
        value = _paper_value(root, paper, old, inputs)
        if old and all(old.get(k) == value.get(k) for k in ('paper_cut', 'inputs', 'story_check', 'trailer')):
            return old
        return _commit(root, value, old, inputs)


def restore_revision(root, revision, expected_revision):
    root = Path(root)
    if signed_trailer_pin(root) is None:
        raise ValueError('Sign trailer creation before restoring its paper cut.')
    with _edit_lock(root):
        old = _read_current(root)
        _expect(old, expected_revision)
        source = next((v for v in _committed_versions(root, old) if v['edit_revision_sha256'] == revision), None)
        if source is None:
            raise ValueError('That saved version could not be found.')
        inputs = _inputs(root)
        if 'paper_cut' in source:
            value = _paper_value(root, source['paper_cut'], old, inputs)
        else:
            value = copy.deepcopy(source)
        value['restored_from'] = revision
        return _commit(root, value, old, inputs)


def _legacy_time(root, version):
    path = root/'artifacts'/'edit-history'/f"{version['edit_revision_sha256']}.json"
    if not path.is_file():
        path = root/'artifacts'/'edit_decisions.json'
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def paper_state(root):
    root = Path(root)
    try:
        with _edit_lock(root):
            current = _read_current(root)
            versions = _committed_versions(root, current)
        from lib.canon_enforcement import _load_canon
        canon = _load_canon(root.parent, root.name, {}) or {}
        sources = [{'value': x['id'], 'label': x.get('summary') or x.get('decision') or 'Story material'}
                   for x in canon.get('locks', [])+canon.get('structure', {}).get('beats', []) if x.get('id') and not x.get('spoiler')]
        check = _story_check(root, current['paper_cut']) if current and 'paper_cut' in current else None
        changed = bool(current and 'paper_cut' in current and current.get('inputs') != _inputs(root))
        pending = bool(current and current.get('paper_cut_pending'))
        needs_review = changed or pending or bool(check and check['status'] == 'failed')
        history = [{'id': v['edit_revision_sha256'], 'number': n, 'saved_at': v.get('saved_at') or (current or {}).get('revision_times', {}).get(v['edit_revision_sha256']) or _legacy_time(root, v),
                    'time_source': 'recorded' if v.get('saved_at') else 'file', 'kind': 'Paper cut' if 'paper_cut' in v else 'Rough cut'} for n, v in enumerate(versions, 1)]
        return {'target_frames': round(require_contract(root)['target_duration_seconds']*24),
                'protected_lines': [x.get('line','') for x in canon.get('protected_lines',[]) if x.get('line')],
                'sources': sources, 'current': current, 'needs_review': needs_review, 'inputs_changed': changed,
                'story_check': check, 'usable_downstream': bool(current and check and check['status'] == 'checked' and not needs_review),
                'history': history}
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return {'current': None, 'needs_review': True, 'usable_downstream': False,
                'error': 'The paper cut could not be read. Earlier versions have been kept. Restore a safe copy before editing.'}


def creation_facts(root):
    marker = json.loads((Path(root)/'project.json').read_text())
    contract = require_contract(root)
    facts = _creation_values(marker.get('title'), contract['target_duration_seconds'], marker.get('spending_ceiling_usd'))
    return facts
