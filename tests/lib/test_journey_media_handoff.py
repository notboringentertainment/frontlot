"""Choosing footage must not invalidate unchanged, already-signed sound."""
import copy
from tests.tools.test_signed_footage import film, provider, looks, workspace, sign
from tests.tools.test_audio_half_transport import wav_bytes


def test_footage_choices_preserve_checked_paper_and_kept_sound(film,provider,monkeypatch):
    from lib import trailer_edit as edit, audio_workspace as audio, footage_requests as footage
    root,shot=film
    calls=[]
    def blocked(*args,**kwargs):
        calls.append(1)
        raise AssertionError('No outside audio service belongs in this local handoff.')
    monkeypatch.setattr('requests.post',blocked)
    monkeypatch.setattr('requests.get',blocked)
    source=root/'assets/invented-effect.wav';source.write_bytes(wav_bytes())
    brief=audio.save_effect_brief(root,'An invented small latch.',1,False,0,0)
    effect=audio.import_music(root,source,'An invented local recording','I made and own this recording.',kind='effect',brief_id=brief)
    sign(root,'audio_take',audio.decision_record(root,effect['id'],'accept'),effect['id'])
    before=edit.read_revision(root)
    assert audio.take_state(root,effect)=='Accepted'
    request=footage.prepare_request(root,shot,model=workspace().MODEL,duration_seconds=5)
    looks.approve_request(footage.request_approval(root,request['request_id']),root)
    take=footage.execute(root,request['request_id'])
    for decision in ('accept','decline'):
        looks.approve_request(footage.request_decision(root,shot,take['take_id'],decision),root)
        footage.apply_decision(root,shot,take['take_id'],decision)
        assert edit.paper_state(root)['usable_downstream'], 'A take choice did not change the checked paper cut.'
        assert edit.read_revision(root)['paper_cut']==before['paper_cut']
        assert audio.take_state(root,effect)=='Accepted'
        if decision=='accept':
            from lib.cutting_view import save_cut
            save_cut(root,{'fps':24,
                'picture':[{'shot_id':shot,'take_id':take['take_id'],'start_frame':0,'source_in_frame':0,'source_out_frame':24}],
                'audio':[{'take_id':effect['id'],'start_frame':0,'source_in_frame':0,'source_out_frame':24,
                          'level_db':-6,'fade_in_frames':0,'fade_out_frames':0}]},
                edit.read_revision(root)['edit_revision_sha256'])
    changed=copy.deepcopy(before['paper_cut'])
    changed['shots'][0]['purpose']='The same keeper opens the other door.'
    edit.save_revision(root,changed,edit.read_revision(root)['edit_revision_sha256'])
    assert not edit.paper_state(root)['usable_downstream']
    assert audio.take_state(root,effect)=='Needs a fresh look'
    assert all(len(provider[k])==1 for k in ('posts','uploads','waits','downloads'))
    assert calls==[]
