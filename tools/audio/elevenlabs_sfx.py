"""Sound effects have one route: an individually signed protected request."""
import os
from tools.base_tool import (BaseTool,ToolTier,ToolRuntime,ToolStability,ExecutionMode,
    Determinism,ResourceProfile,RetryPolicy,ToolStatus)

class ElevenLabsSFX(BaseTool):
    name='elevenlabs_sfx'
    version='1.0.0'
    tier=ToolTier.GENERATE
    capability='sound_effects'
    provider='elevenlabs'
    stability=ToolStability.EXPERIMENTAL
    execution_mode=ExecutionMode.SYNC
    determinism=Determinism.STOCHASTIC
    runtime=ToolRuntime.API
    dependencies=[]
    agent_skills=['sound-effects']
    capabilities=['generate_sound_effect']
    input_schema={'type':'object','additionalProperties':False,'required':['project_dir','audio_request_id'],
        'properties':{'project_dir':{'type':'string'},'audio_request_id':{'type':'string'},
                      'output_path':{'type':'string','description':'Must match the approved take location.'}}}
    resource_profile=ResourceProfile(cpu_cores=1,ram_mb=256,vram_mb=0,disk_mb=50,network_required=True)
    retry_policy=RetryPolicy(max_retries=0,retryable_errors=[])
    idempotency_key_fields=['project_dir','audio_request_id']
    side_effects=['Calls ElevenLabs once after approval and saves a receipted audio take.']
    install_instructions='Connect the ElevenLabs account securely on the server, then sign audio price settings and each individual request.'
    user_visible_verification=['Listen to the whole effect before signing a take decision.']

    def get_status(self):return ToolStatus.AVAILABLE if os.environ.get('ELEVENLABS_API_KEY','').strip() else ToolStatus.UNAVAILABLE
    def execute(self,inputs):
        from tools.audio._approved_transport import execute
        return execute(inputs,'effect')
    def estimate_cost(self,inputs):
        from tools.audio._approved_transport import estimate
        return estimate(inputs,'effect')
