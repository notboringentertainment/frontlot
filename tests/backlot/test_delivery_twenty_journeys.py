"""Twenty real signing/return journeys with one reused local picture render.

The repeat fixture avoids rendering identical lettering twenty times. Every
export still makes its own sound references, examines the actual file, seals
its own record and uses the real board, signer and approval verification.
"""
from tests.backlot.test_delivery_browser import delivery_cut,delivery_journey
from tests.backlot.test_voice_panel_browser import short_signing_folder
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.title_renderer import require_title_renderer

def test_twenty_complete_signing_returns_do_not_need_manual_refresh(delivery_cut,monkeypatch):
    from lib import trailer_delivery as d,cutting_view as cut
    from lib.pathsafe import resolve_input
    from tools.audio.audio_mixer import AudioMixer
    root,*_=delivery_cut;original_preview=cut.render_preview;original_final=d.render_final
    previews={};finished=[]
    def preview(root,revision,comparison='after'):
        key=(revision,comparison)
        if key not in previews:previews[key]=original_preview(root,revision,comparison)
        assert previews[key].is_file()
        return previews[key]
    def final(root,revision,directory):
        if not finished:
            result=original_final(root,revision,directory);finished.append(result[:2]);return result
        saved,timeline=d.require_rough(root,revision)
        tracks=[{**r,'path':str(resolve_input(r['path'],root)),'role':'sfx' if r['kind']=='effect' else r['kind']} for r in timeline['audio']]
        mixed=AudioMixer()._full_mix({'timeline_tracks':tracks,'target_frames':72,
            'speech_margin_db':timeline['mix']['speech_margin_db'],'automatic':True,'output_path':str(directory/'repeat.wav')})
        assert mixed.success
        return *finished[0],mixed.data
    monkeypatch.setattr(cut,'render_preview',preview);monkeypatch.setattr(d,'render_final',final)
    delivery_journey(delivery_cut,monkeypatch,repeat=20)
