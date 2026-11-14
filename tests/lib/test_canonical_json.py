import math
import unicodedata

import pytest

from lib.canonical_json import canonical_bytes, record_sha256


def test_key_order_is_irrelevant():
    a = {"z": 1, "a": {"y": 2, "b": 3}}
    b = {"a": {"b": 3, "y": 2}, "z": 1}
    assert canonical_bytes(a) == canonical_bytes(b) == b'{"a":{"b":3,"y":2},"z":1}'
    assert record_sha256(a) == record_sha256(b)


def test_nfc_and_nfd_strings_hash_equal():
    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    assert nfc != nfd
    assert record_sha256({"name": nfc}) == record_sha256({"name": nfd})
    assert record_sha256({nfc: 1}) == record_sha256({nfd: 1})


def test_integral_float_serializes_as_integer():
    assert canonical_bytes(1.0) == b"1"
    assert canonical_bytes({"v": 1.0}) == canonical_bytes({"v": 1})
    assert canonical_bytes(-0.0) == b"0"
    assert canonical_bytes(1e21) == b"1e+21"
    assert canonical_bytes(123456789012345680000.0) == b"123456789012345680000"


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.5, b"0.5"),
        (3.14, b"3.14"),
        (1e-7, b"1e-7"),
        (0.000001, b"0.000001"),
        (1.5e-10, b"1.5e-10"),
        (1e100, b"1e+100"),
        (-2.5, b"-2.5"),
        (333333333.33333331, b"333333333.3333333"),
    ],
)
def test_es6_number_formatting(value, expected):
    assert canonical_bytes(value) == expected


def test_nan_and_infinity_rejected():
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            canonical_bytes({"x": bad})


def test_key_sort_uses_utf16_code_units():
    # U+1D11E (surrogate pair, first unit 0xD834) sorts before U+FF5E in UTF-16
    # but after it in code-point order.
    obj = {"～": 1, "\U0001d11e": 2}
    assert canonical_bytes(obj) == '{"\U0001d11e":2,"～":1}'.encode("utf-8")


def test_string_escaping_and_no_whitespace():
    out = canonical_bytes({"s": 'a"b\\c\n\t '})
    assert out == '{"s":"a\\"b\\\\c\\n\\t\\u0001 "}'.encode("utf-8")


def test_nested_structures_and_scalars():
    obj = {"list": [True, False, None, 1, "x", {"k": [1.5, [2]]}], "t": (1, 2)}
    assert canonical_bytes(obj) == b'{"list":[true,false,null,1,"x",{"k":[1.5,[2]]}],"t":[1,2]}'


def test_rfc8785_sample_vector():
    # From RFC 8785 §3.2.3 (subset that is representable in Python).
    obj = {
        "numbers": [333333333.33333329, 1e30, 4.5, 2e-3, 0.000000000000000000000000001],
        "string": "\u20ac$\u000f\nA'B\"\\\\\"/",
        "literals": [None, True, False],
    }
    expected = (
        '{"literals":[null,true,false],'
        '"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    ).encode("utf-8")
    assert canonical_bytes(obj) == expected


def test_unsupported_types_raise():
    with pytest.raises(TypeError):
        canonical_bytes({"x": object()})
    with pytest.raises(TypeError):
        canonical_bytes({1: "int key"})
