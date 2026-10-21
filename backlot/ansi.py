"""Incremental terminal-output sanitising for Backlot's signing terminal.

The browser may render SGR colour/style sequences, but it must never receive
terminal control strings (OSC titles/clipboard payloads, DCS, cursor movement,
and friends).  This parser operates on bytes so an escape sequence or UTF-8
code point may be split at any read boundary without changing the result.
"""

from __future__ import annotations

import codecs


_GROUND = 0
_ESCAPE = 1
_CSI = 2
_CONTROL_STRING = 3
_CONTROL_STRING_ESCAPE = 4
_ESCAPE_INTERMEDIATE = 5

_ALLOWED_C0 = {0x08, 0x09, 0x0A, 0x0D}  # backspace, tab, LF, CR
_CONTROL_STRING_STARTS = {ord("]"), ord("P"), ord("X"), ord("^"), ord("_")}
_C1_CONTROL_STRING_STARTS = {0x90, 0x98, 0x9D, 0x9E, 0x9F}
_MAX_ESCAPE_BYTES = 4096


class AnsiSanitizer:
    """Keep printable UTF-8, basic layout controls, and valid SGR only."""

    def __init__(self) -> None:
        self._mode = _GROUND
        self._csi = bytearray()
        self._csi_is_sgr = True
        self._control_kind: str | None = None
        self._control_utf8_remaining = 0
        self._control_utf8_lead: int | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")("ignore")

    def _begin_control_string(self, *, osc: bool) -> None:
        self._control_kind = "osc" if osc else "string"
        self._control_utf8_remaining = 0
        self._control_utf8_lead = None
        self._mode = _CONTROL_STRING

    def _control_payload_byte(self, value: int) -> None:
        """Track UTF-8 continuation state inside discarded control payload."""
        if self._control_utf8_remaining:
            if 0x80 <= value <= 0xBF:
                self._control_utf8_remaining -= 1
                if self._control_utf8_remaining == 0:
                    self._control_utf8_lead = None
                return
            self._control_utf8_remaining = 0
            self._control_utf8_lead = None
        if 0xC2 <= value <= 0xDF:
            self._control_utf8_remaining = 1
            self._control_utf8_lead = value
        elif 0xE0 <= value <= 0xEF:
            self._control_utf8_remaining = 2
            self._control_utf8_lead = value
        elif 0xF0 <= value <= 0xF4:
            self._control_utf8_remaining = 3
            self._control_utf8_lead = value

    def _reset_decoder(self) -> None:
        # Finalising drops a partial/invalid code point.  It must not be joined
        # across an intervening terminal control sequence.
        self._decoder.decode(b"", final=True)
        self._decoder = codecs.getincrementaldecoder("utf-8")("ignore")

    def _text_byte(self, value: int, out: bytearray) -> None:
        if value < 0x20 or value == 0x7F:
            if value in _ALLOWED_C0:
                self._reset_decoder()
                out.append(value)
            # Every other C0 byte is discarded.
            return
        decoded = self._decoder.decode(bytes((value,)), final=False)
        for char in decoded:
            codepoint = ord(char)
            if codepoint == 0x9B:
                self._csi.clear()
                self._csi_is_sgr = True
                self._mode = _CSI
            elif codepoint in _C1_CONTROL_STRING_STARTS:
                self._begin_control_string(osc=codepoint == 0x9D)
            elif char.isprintable():
                out.extend(char.encode("utf-8"))

    def feed(self, data: bytes | bytearray | memoryview) -> bytes:
        """Sanitise one chunk, retaining incomplete parser state for the next."""
        out = bytearray()
        for value in bytes(data):
            if self._mode == _GROUND:
                if value == 0x1B:
                    self._reset_decoder()
                    self._mode = _ESCAPE
                elif not self._decoder.getstate()[0] and value == 0x9B:
                    # Raw 8-bit C1 CSI.  A value in this range following a
                    # UTF-8 lead byte remains part of that Unicode character.
                    self._reset_decoder()
                    self._csi.clear()
                    self._csi_is_sgr = True
                    self._mode = _CSI
                elif not self._decoder.getstate()[0] and value in _C1_CONTROL_STRING_STARTS:
                    self._reset_decoder()
                    self._begin_control_string(osc=value == 0x9D)
                elif not self._decoder.getstate()[0] and 0x80 <= value <= 0x9F:
                    self._reset_decoder()
                else:
                    self._text_byte(value, out)
                continue

            if self._mode == _ESCAPE:
                if value == 0x1B:
                    # The newest ESC can begin a valid sequence.
                    continue
                if value == ord("["):
                    self._csi.clear()
                    self._csi_is_sgr = True
                    self._mode = _CSI
                elif value in _CONTROL_STRING_STARTS:
                    self._begin_control_string(osc=value == ord("]"))
                elif 0x20 <= value <= 0x2F:
                    self._mode = _ESCAPE_INTERMEDIATE
                else:
                    # A non-string ESC sequence is discarded in full.
                    self._mode = _GROUND
                continue

            if self._mode == _ESCAPE_INTERMEDIATE:
                if value == 0x1B:
                    self._mode = _ESCAPE
                elif 0x30 <= value <= 0x7E:
                    self._mode = _GROUND
                elif not 0x20 <= value <= 0x2F:
                    self._mode = _GROUND
                continue

            if self._mode == _CSI:
                if value == 0x1B:
                    self._csi.clear()
                    self._mode = _ESCAPE
                    continue
                if 0x40 <= value <= 0x7E:
                    # SGR has no intermediate bytes: retain only parameter
                    # bytes followed by the final 'm'.
                    if self._csi_is_sgr and value == ord("m"):
                        out.extend(b"\x1b[")
                        out.extend(self._csi)
                        out.append(value)
                    self._csi.clear()
                    self._mode = _GROUND
                    continue
                if 0x30 <= value <= 0x3F and self._csi_is_sgr and len(self._csi) < _MAX_ESCAPE_BYTES:
                    self._csi.append(value)
                    continue
                # Keep consuming an invalid/overlong CSI until its final byte
                # so no suffix can escape into printable output.
                self._csi_is_sgr = False
                continue

            if self._mode == _CONTROL_STRING:
                if value == 0x07 and self._control_kind == "osc":
                    self._mode = _GROUND
                    self._control_kind = None
                    self._control_utf8_remaining = 0
                    self._control_utf8_lead = None
                elif (
                    value == 0x9C
                    and self._control_utf8_remaining == 1
                    and self._control_utf8_lead == 0xC2
                ):
                    # Exact UTF-8 encoding of C1 ST.  Other 0x9c continuation
                    # bytes (for example the tail of E2 82 9C) remain payload.
                    self._mode = _GROUND
                    self._control_kind = None
                    self._control_utf8_remaining = 0
                    self._control_utf8_lead = None
                elif value == 0x9C and self._control_utf8_remaining == 0:
                    # Raw C1 ST only. A 0x9c continuation byte inside a UTF-8
                    # payload is data and must not expose the hostile suffix.
                    self._mode = _GROUND
                    self._control_kind = None
                    self._control_utf8_remaining = 0
                    self._control_utf8_lead = None
                elif value == 0x1B:
                    self._control_utf8_remaining = 0
                    self._control_utf8_lead = None
                    self._mode = _CONTROL_STRING_ESCAPE
                else:
                    self._control_payload_byte(value)
                continue

            # Inside a control string, only ST (ESC backslash) terminates it.
            if value == ord("\\"):
                self._mode = _GROUND
                self._control_kind = None
                self._control_utf8_remaining = 0
                self._control_utf8_lead = None
            elif value != 0x1B:
                self._mode = _CONTROL_STRING
                self._control_payload_byte(value)

        return bytes(out)

    # ``sanitize`` is a readable alias for callers that process read chunks.
    sanitize = feed

    def flush(self) -> bytes:
        """Finish a stream without ever exposing an incomplete sequence."""
        decoded = self._decoder.decode(b"", final=True)
        out = "".join(char for char in decoded if char.isprintable()).encode("utf-8")
        self._decoder = codecs.getincrementaldecoder("utf-8")("ignore")
        self._mode = _GROUND
        self._csi.clear()
        self._csi_is_sgr = True
        self._control_kind = None
        self._control_utf8_remaining = 0
        self._control_utf8_lead = None
        return out


IncrementalAnsiSanitizer = AnsiSanitizer


def sanitize(data: bytes) -> bytes:
    """Convenience one-shot sanitiser."""
    parser = AnsiSanitizer()
    return parser.feed(data) + parser.flush()
