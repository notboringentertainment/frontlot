"""Final lettering draws the same literal signed characters at full resolution."""
import io
import pytest
from PIL import Image
from tests.lib.test_trailer_titles import card

@pytest.mark.parametrize('text',['  “Cafe\u0301!”\nTomorrow…  ','Man\u0303ana —\u00a0tonight.'])
def test_full_resolution_literal_lettering_keeps_text_and_layout(text):
    from lib import trailer_titles as t
    value=card(text);before=value.copy()
    small=Image.open(io.BytesIO(t.card_png(value)))
    large=Image.open(io.BytesIO(t.card_png(value,resolution=(1920,1080))))
    assert value==before and large.size==(1920,1080) and small.size==(640,360)
    assert large.getbbox()[0]>=192 and large.getbbox()[2]<=1728
    normal=card(__import__('unicodedata').normalize('NFC',text))
    assert large.tobytes()==Image.open(io.BytesIO(t.card_png(normal,resolution=(1920,1080)))).tobytes()

def test_final_lettering_refuses_missing_font_and_never_shrinks():
    from lib import trailer_titles as t
    value=card('An impossible long title '*50)
    with pytest.raises(ValueError,match='safe margins'):t.card_png(value,resolution=(1920,1080))
    value=card();value['font_sha256']='0'*64
    with pytest.raises(ValueError,match='font'):t.card_png(value,resolution=(1920,1080))
