"""Real governance and paid tools; only provider IO is simulated.

Optional FRONT_LOT_REPLAY_IMAGE/VIDEO/CANON use copies of existing material.
All approvals and generation records here are isolated test fixtures, never
production receipts or assertions about the original material's provenance.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from lib import supervised_production as production, receipts
from lib.pathsafe import sha256_file
from tests.lib.look_lock_helpers import (
    PROJECT, CHAR, activate_look, character_look, look_refs_for, pin_project,
)
from tests.tools._authored_film_helpers import make_verified_project, write_receipted_png
from tools.video import _shared
from tools.video.kling_reference_video import KlingReferenceVideo
from tools.cost_tracker import load_reservations


def test_supervised_loop_without_stage_completion(tmp_path, monkeypatch):
    monkeypatch.setenv('FAL_KEY', 'simulation-only')
    root = make_verified_project(tmp_path, monkeypatch, PROJECT)
    pin_project(root, '1.5')
    look = character_look()
    activate_look(root, look)
    image_source = os.environ.get('FRONT_LOT_REPLAY_IMAGE')
    image = write_receipted_png(root, 'assets/reference.png',
                payload=Path(image_source).read_bytes() if image_source else None)
    note_source = os.environ.get('FRONT_LOT_REPLAY_CANON')
    canon = root / 'canon-note.md'
    canon.write_bytes(Path(note_source).read_bytes() if note_source else b'The established story is authoritative.')
    canon_before = canon.read_bytes()
    ticket = root / 'resolved-look.md'
    ticket.write_text('Resolved look fixture, upstream version one.')
    look_refs = look_refs_for(look)
    spec = {'shot_id': 'turning-test', 'direction': 'Choke harder, hold three seconds.',
            'spend_allowance_usd': 1.68, 'max_video_takes': 2,
            'allowed_tools': ['kling_reference_video'], 'look_refs': look_refs,
            'reference_manifest': [{'path': 'assets/reference.png', 'asset_id': image['sha256'],
                                   'role': 'hero', 'visual_bible_entity_id': CHAR}],
            'source_paths': ['canon-note.md', 'resolved-look.md']}
    production.prepare(root, spec, user_note='Simulation of an agreed two-take allowance.')
    # A second, unrelated shot must survive a source change to the first.
    production.prepare(root, {**spec, 'shot_id': 'unrelated', 'source_paths': ['canon-note.md']},
                       user_note='Unrelated fixture for source isolation.')
    uploads, submissions = [], []
    monkeypatch.setattr(_shared, 'upload_image_fal', lambda path: uploads.append(path) or 'https://simulation.invalid/ref.png')
    def submit(model_id, payload, **kwargs):
        submissions.append(payload)
        return {'request_id': f'simulation-job-{len(submissions)}'}
    monkeypatch.setattr(_shared, 'fal_queue_submit', submit)
    monkeypatch.setattr(_shared, 'fal_queue_wait', lambda *a, **k: {'video': {'url': 'https://simulation.invalid/clip.mp4'}})
    video_source = os.environ.get('FRONT_LOT_REPLAY_VIDEO')
    def download(url, destination, **kwargs):
        if video_source:
            source = os.environ.get('FRONT_LOT_REPLAY_VIDEO_REVISION', video_source) if len(submissions) > 1 else video_source
            shutil.copyfile(source, destination)
        else:
            Path(destination).write_bytes(f'isolated video fixture {len(submissions)}'.encode())
    monkeypatch.setattr(_shared, 'fal_download', download)
    if not video_source:
        monkeypatch.setattr(_shared, 'verify_video_file', lambda *a, **k: {'duration_seconds': 5.0})
        # Only the generic unit fixture lacks real video bytes. The Ace replay
        # probes the actual copied clip at both tool completion and attachment.
        import subprocess
        monkeypatch.setattr(production.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, '{"format":{"duration":"5.0"}}'))

    def run(name, prompt):
        inputs = production.request(root, 'turning-test', prompt=prompt,
                    output_path=f'assets/{name}.mp4', duration='5', aspect_ratio='16:9')
        result = KlingReferenceVideo().execute(inputs)
        assert result.success, result.error
        return production.attach(root, 'turning-test', result.data['output_path'],
                    user_note='Simulated generated take.', reservation_id=result.data['reservation_id'],
                    prompt=prompt, references=result.metadata['references_applied'])

    first = run('first', 'Hold still for three seconds, then awaken.')
    production.select(root, 'turning-test', first['take_id'], start=0, end=4,
                      user_note='Keep the first four seconds.')
    production.prepare(root, {**spec, 'direction': 'Keep four seconds; revise the remaining performance.'},
                       user_note='Keep the first four seconds; choke harder in the revision.')
    second = run('revision', 'Revise only the remaining performance; choke harder.')
    blocked = KlingReferenceVideo().execute(production.request(root, 'turning-test',
                    prompt='Another attempt', output_path='assets/blocked.mp4', duration='5'))
    assert not blocked.success and 'allowance' in blocked.error
    assert len(uploads) == len(submissions) == 2  # Third call refused before egress.
    assert submissions[1]['prompt'] != submissions[0]['prompt']
    assert submissions[1]['image_urls'] == submissions[0]['image_urls']
    report = production.inspect_shot(root, 'turning-test')
    assert not report['warnings']
    assert report['selections'][0]['end'] == 4
    assert report['takes'][0]['provider_job_id'] == 'simulation-job-1'
    assert all(row['scope'] == 'shot:turning-test' for row in load_reservations(root).values())
    assert not (root / 'checkpoint_scene_plan.json').exists()
    assert not (root / 'checkpoint_visual_bible.json').exists()
    assert len(report['takes']) == 2
    proposal = production.propose_change(root, 'turning-test', 'Try a new ritual, for exploration only.')
    assert proposal['status'] == 'unratified' and canon.read_bytes() == canon_before
    ticket.write_text('Changed upstream look fixture.')
    assert any('resolved-look.md' in w for w in production.inspect_shot(root, 'turning-test')['warnings'])
    assert production.inspect_shot(root, 'unrelated')['warnings'] == []
    assert (root / first['path']).is_file() and (root / second['path']).is_file()
    production.stop(root, 'turning-test', user_note='Stop spending.')
    with pytest.raises(ValueError, match='stopped'):
        production.request(root, 'turning-test', prompt='No', output_path='assets/no.mp4')
    # Imported media keeps missing provenance truthful, independently of the
    # synthetic provider receipts above. No original record is rewritten.
    external = production.attach(root, 'unrelated', root / second['path'], user_note='External import with unknown metadata.')
    assert external['cost_usd'] is None and external['provider_job_id'] is None
    assert canon.read_bytes() == canon_before
    print(json.dumps({'replay_project': str(root), 'simulated_submissions': len(submissions),
                      'real_paid_submissions': 0, 'selected_range': [0, 4],
                      'source_images_copied': bool(image_source), 'source_video_copied': bool(video_source),
                      'source_canon_unchanged': True, 'unratified_proposal_retained': True,
                      'unknown_import_cost_retained': True, 'stopped': True}))
