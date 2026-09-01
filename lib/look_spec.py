"""look_spec validation, hashing and the prompt-injection scan (plan D11).

``schemas/look_spec.schema.json`` is the source of truth for the shape.
This module adds what JSON Schema cannot express: the 20–80 word bound on
``prompt_safe_description``, the injection scan over every string leaf, and
the canonical digest ``look_hash`` = sha256 of the RFC 8785 canonical JSON of
the validated payload (lib.canonical_json). The hash lives only in the
external look_lock receipt envelope — never inside the block.

The injection patterns are vendored from WriterOS
``server/projectMemory/importer.ts`` (``IMPERATIVE_PATTERNS``); the test
``tests/lib/test_look_spec.py`` pins their sha256 so a drift on either side
is a failing test, not a silent divergence.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import jsonschema

from lib.canonical_json import record_sha256

LOOK_SPEC_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "look_spec.schema.json"
LOOK_SPEC_VERSION = "1.0"
ENTITY_KINDS = ("character", "location")
DESCRIPTION_MIN_WORDS = 20
DESCRIPTION_MAX_WORDS = 80
# Must match tools/prompt_builder.py MAX_DESCRIPTION_CHARS: a look that
# validates here but exceeds the builder's cap would sign cleanly and then
# fail at generation time (Ivy, 2026-08-31 — 80 words / 462 chars).
DESCRIPTION_MAX_CHARS = 600

# Vendored verbatim (as JS regex source, case-insensitive) from WriterOS
# importer.ts IMPERATIVE_PATTERNS. Keep the ORDER and the SOURCE strings —
# the vendoring hash test covers exactly this tuple.
PROMPT_INJECTION_PATTERNS: tuple[str, ...] = (
    r"@\w+",
    r"\bignore (all |any )?(previous|prior|above)\b",
    r"\byou (must|should|will) now\b",
    r"\bsystem prompt\b",
    r"\bnew instructions?\b",
)
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in PROMPT_INJECTION_PATTERNS]


def injection_patterns_sha256() -> str:
    """sha256 of the vendored pattern list (newline-joined sources)."""
    return hashlib.sha256("\n".join(PROMPT_INJECTION_PATTERNS).encode("utf-8")).hexdigest()


class LookSpecError(ValueError):
    """The payload is not a valid look_spec."""


@lru_cache(maxsize=1)
def load_look_spec_schema() -> dict[str, Any]:
    with open(LOOK_SPEC_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _string_leaves(obj: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _string_leaves(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _string_leaves(v, f"{path}[{i}]")


def find_prompt_injection(obj: Any) -> list[tuple[str, str]]:
    """``[(json_path, offending_text)]`` for every string leaf matching a
    vendored injection pattern (scanned line by line like the WriterOS adapter)."""
    hits: list[tuple[str, str]] = []
    for path, text in _string_leaves(obj):
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if any(rx.search(line) for rx in _INJECTION_RE):
                hits.append((path, line))
                break
    return hits


def validate_look_spec(payload: Any) -> dict[str, Any]:
    """Validate ``payload`` against the look_spec schema plus the Python-level
    rules; returns the payload unchanged. Raises LookSpecError."""
    if not isinstance(payload, dict):
        raise LookSpecError("look_spec must be a mapping")
    kind = payload.get("entity_kind")
    if kind not in ENTITY_KINDS:
        raise LookSpecError(f"entity_kind must be one of {list(ENTITY_KINDS)}, got {kind!r}")
    schema = load_look_spec_schema()
    branch = {k: v for k, v in schema.items() if k not in ("oneOf", "type")}
    branch["$ref"] = f"#/$defs/{kind}"
    try:
        jsonschema.validate(instance=payload, schema=branch)
    except jsonschema.ValidationError as exc:
        raise LookSpecError(f"[look_spec {kind}] {exc.message} at {list(exc.absolute_path)}") from exc
    desc = str(payload.get("prompt_safe_description", ""))
    words = len(desc.split())
    if not DESCRIPTION_MIN_WORDS <= words <= DESCRIPTION_MAX_WORDS:
        raise LookSpecError(
            f"prompt_safe_description must be {DESCRIPTION_MIN_WORDS}-{DESCRIPTION_MAX_WORDS} words, got {words}"
        )
    if len(desc) > DESCRIPTION_MAX_CHARS:
        raise LookSpecError(
            f"prompt_safe_description must be at most {DESCRIPTION_MAX_CHARS} characters "
            f"(the prompt builder's cap), got {len(desc)}"
        )
    hits = find_prompt_injection(payload)
    if hits:
        raise LookSpecError(
            "prompt-injection pattern detected in look_spec: "
            + "; ".join(f"{path}: {line[:80]!r}" for path, line in hits)
        )
    return payload


def look_hash(payload: dict[str, Any]) -> str:
    """sha256 of the canonical JSON of a VALIDATED payload."""
    return record_sha256(payload)


def look_key(payload: dict[str, Any]) -> tuple[str, str]:
    return str(payload.get("entity_kind")), str(payload.get("entity_id"))


def generation_sufficient(payload: dict[str, Any]) -> tuple[bool, str]:
    """(ok, reason): a look can drive generation only when it is not
    shape_only, not a minor, and the writer attested a fictional subject."""
    if payload.get("shape_only") is True:
        return False, "shape_only looks are never sufficient for generation"
    if payload.get("minor") is True:
        return False, "looks for minors are refused for generation"
    if payload.get("fictional_subject_attestation") is not True:
        return False, "fictional_subject_attestation must be true"
    return True, ""
