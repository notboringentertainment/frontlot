"""Only actual rendering checks depend on installed local Remotion tools."""
import pytest

@pytest.fixture(autouse=True)
def require_title_renderer():
    from lib.trailer_titles import renderer_configuration
    try:renderer_configuration()
    except ValueError as error:pytest.skip(str(error))
