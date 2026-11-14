"""Trailer facts and verification through the existing signed approval ledger."""
import hashlib
import json
import math
import subprocess
from pathlib import Path

import jsonschema
from lib.canonical_json import record_sha256

SCHEMA = Path(__file__).resolve().parents[1] / 'schemas/trailer_contract.schema.json'
FPS = 24
MIN_SECONDS = 1
TOLERANCE_SECONDS = 1 / FPS
DAMAGED = 'This trailer is damaged: its saved trailer facts are missing or invalid. Nothing can be completed.'
HISTORY_DAMAGED = "This trailer's approval history can't be verified, so nothing can be completed. Nothing has been changed."
SIGNED_REQUIRED = 'This stage needs your signed approval. Approval controls are not available yet.'


def make_contract(seconds):
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds) or not MIN_SECONDS <= seconds <= 120
            or not math.isclose(seconds * FPS, round(seconds * FPS), abs_tol=1e-8, rel_tol=0)):
        raise ValueError('Choose a trailer length from one second to two minutes, using whole frames at twenty-four frames per second.')
    return {'kind': 'trailer', 'target_duration_seconds': seconds, 'fps': FPS,
            'approval_points': {'edit': 'rough_cut', 'compose': 'final_export'}}


def require_contract(root):
    try:
        marker = json.loads((Path(root) / 'project.json').read_text())
        value = marker['trailer']
        expected = make_contract(value['target_duration_seconds'])
        if value != expected:
            raise ValueError(DAMAGED)
        return value
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError(DAMAGED) from None


STAGE_LABELS = {
    'canon_ingest': 'Story', 'proposal': 'Treatment', 'look_lock': 'Looks',
    'headshots': 'Cast portraits', 'visual_bible': 'Visual references',
    'script': 'Lines and title cards', 'scene_plan': 'Shot list',
    'assets': 'Footage and sound', 'edit': 'Rough cut', 'compose': 'Final assembly',
}
STAGE_LIMITS = {name: 'Signed stage approval controls are not available yet.' for name in STAGE_LABELS}
STAGE_LIMITS.update({
    'assets': 'The complete trailer footage, sound and signed stage approval controls are not available yet.',
    'edit': '',
    'compose': '',
})


def _duration(value, target):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Trailer timing must use finite numbers.')
    if value > 120 or value > target + TOLERANCE_SECONDS + 1e-9:
        raise ValueError('The trailer is longer than its target. At most one frame of timing tolerance is allowed, and never more than two minutes.')


def _whole_frames(value):
    if not math.isclose(value * FPS, round(value * FPS), abs_tol=1e-8, rel_tol=0):
        raise ValueError('Trailer script and shot-list timing must use whole frames at twenty-four frames per second.')


def _frame_times(value, target):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith('_seconds'):
                _duration(item, target)
                _whole_frames(item)
            else:
                _frame_times(item, target)
    elif isinstance(value, list):
        for item in value:
            _frame_times(item, target)


def edit_duration(edit):
    if edit.get('cutting_view'):
        return max((r['start_frame']+r['duration_frames'] for r in edit['timeline']['picture']),default=0)/FPS
    return sum((cut['out_seconds'] - cut['in_seconds']) / cut.get('speed', 1)
               for cut in edit.get('cuts', []) if cut.get('layer', 'primary') == 'primary')


def validate_stage_contract(marker_contract, stage, artifacts, *, partial=False):
    try:
        expected = make_contract((marker_contract or {}).get('target_duration_seconds'))
        if expected != marker_contract:
            raise ValueError(DAMAGED)
    except (ValueError, AttributeError):
        raise ValueError(DAMAGED) from None
    names = {'script': ['script'], 'scene_plan': ['scene_plan'],
             'edit': ['edit_decisions'], 'compose': ['render_report', 'final_review']}.get(stage, [])
    target = expected['target_duration_seconds']
    for name in names:
        if partial and name not in artifacts:
            continue
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict) or artifact.get('trailer') != expected:
            raise ValueError('Each trailer stage must carry the saved trailer contract unchanged.')
        if name in {'script', 'scene_plan'}:
            _frame_times(artifact, target)
        if 'total_duration_seconds' in artifact:
            _duration(artifact['total_duration_seconds'], target)
        for section in artifact.get('sections', []) + artifact.get('scenes', []):
            _duration(section.get('end_seconds'), target)
        if name == 'edit_decisions':
            if artifact.get('unfinished') or any(e.get('kind') == 'placeholder' for e in (artifact.get('timeline') or {}).get('picture', [])):
                raise ValueError('This sequence is unfinished. Placeholders cannot complete a stage.')
            if artifact.get('render_runtime') != 'remotion':
                raise ValueError('Trailer edits must use Remotion for timed composition.')
            # Existing second-based cut contract; the later cutting view supplies frame timelines.
            end = 0
            for cut in artifact.get('cuts', []):
                a, b = cut.get('in_seconds'), cut.get('out_seconds')
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a,b)) or a < 0 or b <= a:
                    raise ValueError('Trailer trims must have valid source times.')
                speed = cut.get('speed', 1)
                if isinstance(speed, bool) or not isinstance(speed, (int,float)) or not math.isfinite(speed) or speed <= 0:
                    raise ValueError('Trailer playback speed must be a positive number.')
                if cut.get('layer', 'primary') == 'primary':
                    end += (b-a)/speed
            _duration(end, target)
        for output in artifact.get('outputs', []):
            _duration(output.get('duration_seconds'), target)
        probe = (artifact.get('checks') or {}).get('technical_probe') or {}
        if 'duration_seconds' in probe:
            _duration(probe['duration_seconds'], target)


def edit_digest(edit):
    return record_sha256({k:v for k,v in edit.items() if k not in {'edit_revision_sha256', 'revision_history', 'revision_times'}})


def _local_file(root, value):
    if not isinstance(value, str) or not value:
        raise ValueError('The saved edit or exported file is missing.')
    path = (Path(root) / value).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError('The saved edit and exported file must be inside this production.')
    return path


def _file_digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _export_duration(path):
    result = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                             '-of','json',str(path)], capture_output=True, text=True, timeout=30, check=True)
    return float(json.loads(result.stdout)['format']['duration'])


def approval_record(root, pin, stage, artifacts):
    """The existing artifact-review signer must approve this exact record.

    The board's delivery controls use the same gate-side constructor and
    signature machinery as every other governed approval.
    """
    contract = require_contract(root)
    validate_stage_contract(contract, stage, artifacts)
    record = {'stage': stage, 'pipeline': pin.to_dict(), 'trailer': contract,
              'artifacts_sha256': record_sha256(artifacts)}
    paper_path = Path(root)/'artifacts'/'edit_decisions.json'
    if paper_path.is_file():
        paper = json.loads(paper_path.read_text())
        if 'paper_cut' in paper and stage in {'script','scene_plan','assets','edit','compose'}:
            from lib.trailer_edit import paper_state
            state = paper_state(root)
            if state.get('needs_review') or not state.get('usable_downstream'):
                raise ValueError('The paper cut needs a fresh look and must be checked against the approved story before this stage can use it.')
            record['paper_cut_revision'] = paper['edit_revision_sha256']
    # A changed earlier checkpoint invalidates this approval as well.
    from lib.pipeline_loader import get_stage_order, load_pipeline
    predecessors = get_stage_order(load_pipeline(pin.ref))
    record['predecessors'] = {s: _file_digest(Path(root)/f'checkpoint_{s}.json')
                              for s in predecessors[:predecessors.index(stage)]
                              if (Path(root)/f'checkpoint_{s}.json').is_file()}
    if stage in {'edit', 'compose'}:
        saved = json.loads(_local_file(root, 'artifacts/edit_decisions.json').read_text())
        if saved.get('cutting_view') or (saved.get('timeline') or {}).get('picture') or (saved.get('timeline') or {}).get('audio'):
            from lib.cutting_view import validate_cut,editable
            validate_cut(root,editable(saved['timeline']),verify_files=True)
        else:
            from lib.footage_requests import validate_cut_sources
            validate_cut_sources(root,saved)
        validate_stage_contract(contract, 'edit', {'edit_decisions': saved})
        digest = edit_digest(saved)
        if saved.get('edit_revision_sha256') != digest:
            raise ValueError('The rough-cut fingerprint does not match the saved edit revision.')
        record['edit_revision_sha256'] = digest
        if stage == 'edit' and artifacts.get('edit_decisions') != saved:
            raise ValueError('The rough cut does not match the saved edit revision.')
        if stage == 'compose':
            report, review = artifacts.get('render_report') or {}, artifacts.get('final_review') or {}
            outputs = report.get('outputs') or []
            if len(outputs) != 1:
                raise ValueError('Approve one exact exported trailer file at a time.')
            output = _local_file(root, outputs[0].get('path'))
            if _local_file(root, review.get('output_path')) != output:
                raise ValueError('The final review must describe the exact exported file.')
            sha = _file_digest(output)
            for a in (report, review):
                if a.get('edit_revision_sha256') != digest or a.get('export_sha256') != sha:
                    raise ValueError('The export fingerprints do not match the saved edit and actual file.')
            try:
                measured = _export_duration(output)
                _duration(measured, contract['target_duration_seconds'])
                claims = [outputs[0].get('duration_seconds'),
                          ((review.get('checks') or {}).get('technical_probe') or {}).get('duration_seconds')]
                claims += [a['total_duration_seconds'] for a in (report, review) if 'total_duration_seconds' in a]
                for claimed in claims:
                    _duration(claimed, contract['target_duration_seconds'])
                    if abs(measured - claimed) > TOLERANCE_SECONDS + 1e-9:
                        raise ValueError('The exported file length does not match its review records within one frame.')
            except (OSError, subprocess.SubprocessError, KeyError, TypeError) as exc:
                raise ValueError('The exported trailer could not be measured.') from exc
            if abs(measured - edit_duration(saved)) > TOLERANCE_SECONDS + 1e-9:
                raise ValueError('The exported file length does not match the saved edit timeline within one frame.')
            record['export_sha256'] = sha
    return record


def require_stage_approval(root, pin, stage, artifacts):
    from lib.receipts import verified_approvals
    # Refuse a bare flag before attempting artifact/media reads.
    rows = [r for r in verified_approvals(root, 'artifact_review')
            if r.get('stage') == stage and r.get('scope') == 'trailer-stage'
            and r.get('artifact_type') == 'trailer_stage' and r.get('artifact_version') == '1.0'
            and r.get('migration_status') == 'reviewed']
    if not rows:
        raise ValueError(SIGNED_REQUIRED)
    if stage == 'compose' and (artifacts.get('final_review') or {}).get('status') != 'pass':
        raise ValueError('The final review must pass before this trailer can be completed.')
    # Ledger order is authoritative: an older approved revision never revives.
    rows = rows[-1:]
    response = rows[0].get('user_response')
    if not (response == 'approve' or isinstance(response, dict) and response.get('answer') == 'approved'):
        raise ValueError(SIGNED_REQUIRED)
    record = approval_record(root, pin, stage, artifacts)
    digest = record_sha256(record)
    if not any(r.get('record') == record and r.get('record_sha256') == digest
               and r.get('artifact_digest') == digest for r in rows):
        raise ValueError('This stage needs fresh review and signed approval because its trailer facts or materials changed.')


def project_checkpoint(root, pin, cp):
    if pin.name != 'authored-film' or pin.version != '1.6' or cp.get('status') != 'completed':
        return cp
    try:
        if cp.get('human_approved') is not True or cp.get('pipeline') != pin.to_dict() or cp.get('pipeline_type') not in {'authored-film', pin.ref} or cp.get('project_id') != Path(root).name:
            raise ValueError('The stage does not belong to this signed trailer.')
        require_stage_approval(root, pin, cp.get('stage'), cp.get('artifacts') or {})
        return cp
    except Exception:
        # Reads never rewrite history. A forged or stale completion cannot advance a run.
        return dict(cp, status='awaiting_human', human_approved=False,
                    approval_error='This stage needs fresh review and signed approval.')


def signed_trailer_pin(root):
    """Discover the trailer from the signer-owned chain even if marker text was edited."""
    from lib.receipts import verified_approvals
    from lib.pipeline_pin import PinnedPipeline, pinned_pipeline
    from lib.pipeline_loader import manifest_digest
    rows = verified_approvals(root, 'pipeline_migration')
    if any(r.get('kind') == 'pipeline_migration' and r.get('record', {}).get('pipeline_name') == 'authored-film'
           and r.get('record', {}).get('version') == '1.6' for r in rows):
        # Verify the complete migration supersession chain, including a historical downgrade.
        pinned_pipeline(root, 'authored-film')
        row = next(r for r in rows if r.get('record', {}).get('pipeline_name') == 'authored-film'
                   and r.get('record', {}).get('version') == '1.6')
        record = row['record']
        if record.get('manifest_digest') != manifest_digest('authored-film@1.6'):
            raise ValueError(HISTORY_DAMAGED)
        return PinnedPipeline('authored-film', '1.6', record['manifest_digest'], row['receipt_id'])
    return None


def checkpoint_trailer_evidence(root):
    """An explicit trailer manifest identifies damage; it never grants approval."""
    for path in Path(root).glob('checkpoint_*.json'):
        try:
            checkpoint=json.loads(path.read_text())
        except (OSError,ValueError,RecursionError):
            continue
        if not isinstance(checkpoint,dict):continue
        pipeline=checkpoint.get('pipeline')
        if pipeline=='authored-film@1.6':return True
        if isinstance(pipeline,dict) and pipeline.get('name')=='authored-film' and pipeline.get('version')=='1.6':return True
    return False


def refuse_demo_overwrite(root):
    try:
        marker = json.loads((Path(root) / "project.json").read_text())
    except (OSError, ValueError):
        marker = {}
    if signed_trailer_pin(root) is not None or marker.get("production_kind") == "trailer":
        raise ValueError('A developer demonstration cannot overwrite or approve a trailer production.')
