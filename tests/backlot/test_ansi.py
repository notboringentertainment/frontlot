"""Incremental ANSI sanitiser contracts for the signing terminal."""

from __future__ import annotations

import pytest

from backlot.ansi import AnsiSanitizer, sanitize


def _chunked(payload: bytes, cuts: tuple[int, ...]) -> bytes:
    parser = AnsiSanitizer()
    positions = (0, *cuts, len(payload))
    return b"".join(parser.feed(payload[a:b]) for a, b in zip(positions, positions[1:])) + parser.flush()


def test_printable_utf8_layout_controls_and_sgr_survive() -> None:
    payload = "CHAR → café".encode() + b"\r\n\tX\bY\x1b[1;38;5;42mgreen\x1b[0m"
    assert sanitize(payload) == payload


def test_hostile_terminal_controls_are_removed_without_hiding_plain_text() -> None:
    forbidden_c0 = bytes(value for value in range(0x20) if value not in (0x08, 0x09, 0x0A, 0x0D))
    payload = (
        b"before"
        + forbidden_c0
        + b"\x7f"
        + b"\x1b[2Jcursor"
        + b"\x1b]0;title\x07osc-bel"
        + b"\x1b]52;c;Y2xpcGJvYXJk\x1b\\osc-st"
        + b"\x1bP1;2|dcs-data\x1b\\dcs"
        + b"\x1bPsecret\x07LEAK\x1b\\"
        + b"\x1bXstart-of-string\x1b\\sos"
        + b"\x1b^privacy-message\x1b\\pm"
        + b"\x1b_application-command\x1b\\apc"
        + b"after"
    )
    assert sanitize(payload) == b"beforecursorosc-belosc-stdcssospmapcafter"


HOSTILE = (
    b"A"
    + "雪".encode()
    + b"\x1b[31mRED\x1b[0m"
    + b"\x1b[2J"
    + b"B\x1b]52;c;bmV2ZXItdG8tdGhlLWJyb3dzZXI=\x07C"
    + b"\x1bPpayload\x1b\\D"
    + b"\x1bPsecret\x07LEAK\x1b\\"
    + b"\x9b2J\x9d0;raw-osc\x9c\x90raw-dcs\xc2\x9c"
    + "Ω".encode()
    + b"\r\n"
)
HOSTILE_EXPECTED = b"A" + "雪".encode() + b"\x1b[31mRED\x1b[0mBCD" + "Ω".encode() + b"\r\n"


@pytest.mark.parametrize("cut", range(1, len(HOSTILE)))
def test_hostile_payload_matches_one_shot_at_every_single_byte_boundary(cut: int) -> None:
    assert _chunked(HOSTILE, (cut,)) == HOSTILE_EXPECTED == sanitize(HOSTILE)


@pytest.mark.parametrize(
    "cuts",
    [
        (1, 2, 3, 4, 5),
        (2, 7, 13, 29, 41),
        tuple(range(1, len(HOSTILE))),
        (len(HOSTILE) // 3, 2 * len(HOSTILE) // 3),
    ],
)
def test_hostile_payload_matches_one_shot_across_representative_partitions(cuts: tuple[int, ...]) -> None:
    assert _chunked(HOSTILE, cuts) == HOSTILE_EXPECTED


def test_partial_utf8_and_escape_sequences_carry_but_flush_never_leaks_them() -> None:
    parser = AnsiSanitizer()
    snow = "雪".encode()
    assert parser.feed(snow[:1]) == b""
    assert parser.feed(snow[1:]) == snow
    assert parser.feed(b"\x1b[38;5;") == b""
    assert parser.feed(b"42mOK") == b"\x1b[38;5;42mOK"

    for incomplete in (b"\xe9", b"\xf0\x9f", b"\x1b", b"\x1b[31", b"\x1b]52;c;secret", b"\x1bPsecret\x1b"):
        unfinished = AnsiSanitizer()
        assert unfinished.feed(incomplete) == b""
        assert unfinished.flush() == b""
