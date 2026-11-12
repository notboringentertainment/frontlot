"""Signed rate bounds, rather than public averages, price individual requests."""
import pytest
from tests.tools.test_audio_governance import project, no_real_network_contact

def test_absent_price_basis_cannot_quote(project):
    from lib.audio_prices import quote
    with pytest.raises(ValueError,match='signed price basis'):
        quote(project,'speech','A bell.',3)

@pytest.mark.parametrize('kind,prompt,seconds,want',[('speech','A bell.',3,.0028),('music','A quiet bell.',30,.12)])
def test_signed_rates_compute_upward_bound(project,kind,prompt,seconds,want):
    from tests.tools.test_audio_half_transport import priced
    from lib.audio_prices import quote
    priced.__wrapped__(project)
    price=quote(project,kind,prompt,seconds)
    assert price['max_credits_usd']==want and price['max_cash_usd']==0

@pytest.mark.parametrize('key',['dollars_per_credit','speech_credits_per_character','voice_multiplier_ceiling'])
def test_each_rate_expiry_refuses(project,key):
    import yaml,hashlib,time
    from tests.tools.test_audio_half_transport import priced
    from tests.tools.test_audio_governance import sign
    from lib.audio_prices import quote
    priced.__wrapped__(project)
    data=yaml.safe_load((project/'project.yaml').read_text());data['audio_price_basis'][key]['expires_at']=time.time()-1
    raw=yaml.safe_dump(data).encode();(project/'project.yaml').write_bytes(raw)
    sign(project,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    with pytest.raises(ValueError,match='expired'):quote(project,'speech','A bell.',3)


def test_voices_without_signed_multiplier_ceiling_are_refused(project):
    import yaml,hashlib
    from tests.tools.test_audio_half_transport import priced
    from tests.tools.test_audio_governance import sign
    from lib.audio_prices import quote
    priced.__wrapped__(project)
    data=yaml.safe_load((project/'project.yaml').read_text());data['audio_price_basis'].pop('voice_multiplier_ceiling')
    raw=yaml.safe_dump(data).encode();(project/'project.yaml').write_bytes(raw)
    sign(project,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    with pytest.raises(ValueError,match='multiplier ceiling'):quote(project,'speech','A bell.',3)
