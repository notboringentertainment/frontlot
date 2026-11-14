"""Private measurement writes must not flood the signing return's refresh."""
import pytest

@pytest.mark.parametrize('path',[
    'work', 'work/mix.wav', 'work/mix-parts/speech.wav',
    'check-'+'b'*32, 'check-'+'b'*32+'/mix-parts/voice-1-source.wav',
])
def test_delivery_private_work_is_not_a_board_change(tmp_path,monkeypatch,path):
    import backlot.server as server
    monkeypatch.setattr(server,'_PROJECTS_ROOT_STR',str(tmp_path.resolve()))
    assert server._project_of_change(str(tmp_path/'invented-trailer/artifacts/deliveries'/('a'*32)/path)) is None

@pytest.mark.parametrize('path',[
    'approvals.jsonl','generation-receipts.jsonl','artifacts/edit_decisions.json',
    'artifacts/deliveries/'+'a'*32+'/complete.json',
    'artifacts/deliveries/'+'a'*32+'/frames/frame-24.png',
    'artifacts/deliveries/'+'a'*32+'/trailer.mp4',
    'assets/kept.wav', 'assets/kept.mp4',
])
def test_signed_material_and_results_still_notify_the_board(tmp_path,monkeypatch,path):
    import backlot.server as server
    monkeypatch.setattr(server,'_PROJECTS_ROOT_STR',str(tmp_path.resolve()))
    assert server._project_of_change(str(tmp_path/'invented-trailer'/path))=='invented-trailer'
