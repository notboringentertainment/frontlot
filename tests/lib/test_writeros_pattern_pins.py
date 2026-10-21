"""WriterOS ports tools/prompt_builder.py _NAME_PATTERNS into
shared/realPersonPatterns.ts and refuses them at Promote (look sessions plan,
Task 1). Both sides pin the same digest: sources joined by newlines, each
case-insensitive source suffixed with NUL + "i". Changing a pattern here means
changing the WriterOS port and both pins together."""

from __future__ import annotations

import hashlib
import re

from tools import prompt_builder

WRITEROS_NAME_PATTERNS_SHA256 = "30d4a22a29ac1baebc8cc5b997b0937aa0e51116d43212b7fd7aecc44a166b0e"


def test_name_patterns_match_the_writeros_port():
    joined = "\n".join(
        p.pattern + ("\x00i" if p.flags & re.IGNORECASE else "") for p in prompt_builder._NAME_PATTERNS
    )
    assert hashlib.sha256(joined.encode("utf-8")).hexdigest() == WRITEROS_NAME_PATTERNS_SHA256
