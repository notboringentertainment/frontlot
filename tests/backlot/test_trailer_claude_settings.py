"""Settings inspection only. Never launch an assistant or use a real key."""
import json
from pathlib import Path
from backlot import claude_settings as cs
from lib.checkpoint import init_project


def test_trailer_shell_cannot_write_canonical_files_or_read_signer(tmp_path, monkeypatch):
    repo=tmp_path/'repo';repo.mkdir()
    film=init_project('p',title='Night Window',pipeline_type='authored-film@1.6',
                      pipeline_dir=tmp_path/'projects',target_duration_seconds=28)
    work=cs.work_dir(film);meta=tmp_path/'private-signer'
    settings=cs.build_settings(repo_root=repo,film_root=film,meta_root=meta,
                               environ={'OPENMONTAGE_GATES_DIR':str(meta)})
    sandbox=settings['sandbox'];fs=sandbox['filesystem']
    assert not sandbox['allowUnsandboxedCommands']
    assert 'Bash(blender:*)' not in settings['permissions']['allow']
    for rel in ['project.json','project.yaml','checkpoint_edit.json','checkpoint_compose.json',
                'approvals.jsonl','artifacts','canon','production','renders','.gate-requests']:
        assert str(film/rel) in fs['denyWrite']
    assert str(meta) in fs['denyRead'] and str(meta) in fs['denyWrite']
    assert str(work) not in fs['denyWrite']
    assert f'Edit(/{work}/**)' in settings['permissions']['allow']
    assert cs.TOOL_NAME in settings['permissions']['allow']
    assert sandbox['network']['allowUnixSockets']==[]
    def broken_pin(root): raise ValueError('damaged pin')
    monkeypatch.setattr('lib.trailer_contract.signed_trailer_pin', broken_pin)
    assert cs.trailer_workspace(repo) is True


def test_trailer_brief_has_one_composition_route():
    brief=cs.build_brief(film_title='Night Window',film_slug='p',trailer=True)
    assert 'dangerouslyDisableSandbox' not in brief
    assert '30 credits' not in brief
    assert 'Remotion' in brief and 'FFmpeg' in brief
    assert 'frontlot_run' in brief
