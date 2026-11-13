"""Real signed footage and audio approvals feed the cut; every service is fake."""
import hashlib,json,time
import pytest,yaml
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign
from tests.tools.test_audio_half_transport import wav_bytes

@pytest.fixture
def signed_cut(film,provider,monkeypatch):
 from lib import trailer_edit,audio_workspace as audio,footage_requests as footage
 from lib.cutting_view import save_cut,render_preview,validate_cut
 from tools.audio.elevenlabs_tts import ElevenLabsTTS
 root,shot=film
 # Add the line before making any footage request, so its source stays current.
 saved=trailer_edit.read_revision(root);paper=saved['paper_cut'];paper['lines']=[{'text':'The lamp is still lit.','start_frame':0,'duration_frames':24}]
 trailer_edit.save_revision(root,paper,saved['edit_revision_sha256'])
 settings=yaml.safe_load((root/'project.yaml').read_text());settings['provider_egress'].append({'provider':'elevenlabs','content_classes':['speech_text']})
 settings['audio_price_basis']={k:{'value':v,'set_at':1,'expires_at':4102444800} for k,v in [('dollars_per_credit',.0002),('speech_credits_per_character',1),('music_credits_per_minute',1200),('voice_multiplier_ceiling',2)]}
 raw=yaml.safe_dump(settings).encode();(root/'project.yaml').write_bytes(raw);sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
 # Rebuild the shot brief's source snapshot through the same board preparation.
 from backlot.footage_controls import _prepare_brief
 _prepare_brief(root,shot,{})
 pictures=[]
 for i in range(3):
  request=footage.prepare_request(root,shot,model=workspace().MODEL,duration_seconds=5)
  gate=footage.request_approval(root,request['request_id']);looks.approve_request(gate,root)
  take=footage.execute(root,request['request_id'])
  gate=footage.request_decision(root,shot,take['take_id'],'accept');looks.approve_request(gate,root)
  footage.apply_decision(root,shot,take['take_id'],'accept')
  pictures.append(dict(shot_id=shot,take_id=take['take_id'],start_frame=i*24,source_in_frame=12,source_out_frame=36))
 save_cut(root,{'fps':24,'picture':pictures,'audio':[]},trailer_edit.read_revision(root)['edit_revision_sha256'])
 import subprocess
 for name,hz in (('voice',440),('score',220),('latch',880)):
  subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i',f'sine=frequency={hz}:duration=1',str(root/f'assets/{name}.wav')],check=True)
 posts=[]
 class Response:
  status_code=200;headers={'character-cost':'1','request-id':'invented-speech'};content=(root/'assets/voice.wav').read_bytes()
  def raise_for_status(self):pass
 def post(*a,**kw):posts.append(kw);return Response()
 monkeypatch.setattr('requests.post',post);monkeypatch.setenv('ELEVENLABS_API_KEY','invented-only')
 request=audio.prepare_request(root,'speech','eleven_multilingual_v2',line_index=0,voice='invented')
 sign(root,'audio_spend',request,request['request_id'])
 made=ElevenLabsTTS().execute({'project_dir':str(root),'audio_request_id':request['request_id']});assert made.success,made.error
 take=made.data['take'];sign(root,'audio_take',audio.decision_record(root,take['id'],'accept'),take['id'])
 effect=audio.save_effect_brief(root,'A quiet latch.',1,False,24,0)
 imported=[audio.import_music(root,root/'assets/score.wav','My score','I own this invented recording.'),audio.import_music(root,root/'assets/latch.wav','My latch','I own this invented recording.',kind='effect',brief_id=effect)]
 for item in imported:sign(root,'audio_take',audio.decision_record(root,item['id'],'accept'),item['id'])
 timeline={'fps':24,'picture':pictures,'audio':[dict(take_id=item['id'],start_frame=24 if item['kind']=='effect' else 0,source_in_frame=0,source_out_frame=24,level_db=-6,fade_in_frames=0,fade_out_frames=0) for item in [take,*imported]]}
 old=trailer_edit.read_revision(root);saved=save_cut(root,timeline,old['edit_revision_sha256'])
 return root,provider,posts,timeline,take

def test_only_real_signed_kept_sources_render(signed_cut):
 from lib import audio_workspace as audio,trailer_edit
 from lib.cutting_view import render_preview,validate_cut
 root,provider,posts,timeline,take=signed_cut
 assert render_preview(root,trailer_edit.read_revision(root)['edit_revision_sha256']).stat().st_size>0
 sign(root,'audio_take',audio.decision_record(root,take['id'],'decline'),take['id'])
 with pytest.raises(ValueError,match='Sound 1.*declined'):validate_cut(root,timeline,verify_files=True)
 assert len(provider['posts'])==3 and len(posts)==1
