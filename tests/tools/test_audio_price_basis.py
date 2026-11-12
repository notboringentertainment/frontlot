"""Signed rate bounds, rather than public averages, price individual requests."""
import pytest
from tests.tools.test_audio_governance import project, no_real_network_contact

def test_absent_price_basis_cannot_quote(project):
    from lib.audio_prices import quote
    with pytest.raises(ValueError,match='signed price basis'):
        quote(project,'speech','A bell.',3)
