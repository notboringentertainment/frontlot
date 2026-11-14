"""The story-wayfinder look-spec template mirrors schemas/look_spec.schema.json.

Its worked examples must validate against the schema (flat shape, exact
enums), and every enum the template lists must be the schema's enum verbatim.
The template lives in the writer's skill folder; when it is absent (CI, another
machine) the test is skipped rather than failed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from lib.look_spec import load_look_spec_schema, validate_look_spec

TEMPLATE = Path.home() / ".claude" / "skills" / "story-wayfinder" / "templates" / "look-spec.md"
_FENCE = re.compile(r"```(?:yaml|yml)[ \t]*\r?\n(.*?)\r?\n```", re.DOTALL)
_SECTION = re.compile(r"^## (.+?)\s*$", re.MULTILINE)


def _sections(text: str) -> dict[str, str]:
    heads = list(_SECTION.finditer(text))
    return {
        h.group(1): text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        for i, h in enumerate(heads)
    }


@pytest.fixture(scope="module")
def template_text() -> str:
    if not TEMPLATE.is_file():
        pytest.skip(f"look-spec template not present at {TEMPLATE}")
    return TEMPLATE.read_text(encoding="utf-8")


def _worked_examples(text: str) -> dict[str, dict]:
    out = {}
    for title, body in _sections(text).items():
        if not title.startswith("Worked example"):
            continue
        fences = _FENCE.findall(body)
        assert len(fences) == 1, f"{title!r} must hold exactly one yaml block"
        out[title] = yaml.safe_load(fences[0])
    return out


def test_worked_examples_validate_against_schema(template_text):
    examples = _worked_examples(template_text)
    kinds = {ex["entity_kind"] for ex in examples.values()}
    assert kinds == {"character", "location"}, "one worked example per branch"
    for title, payload in examples.items():
        validate_look_spec(payload)  # raises LookSpecError on any drift from the schema


def _enum_from_comment(text: str, field: str) -> list[str]:
    """The `a | b | c` list on the template line that declares ``field``."""
    line = next(l for l in text.splitlines() if re.match(rf"^\s*{re.escape(field)}:", l))
    comment = line.split("#", 1)[1]
    # Continuation comment lines (indented, starting with #) extend the list only if they carry '|'.
    return [v.strip() for v in comment.split("|") if v.strip() and "(" not in v.strip()[:1]]


def test_template_enums_match_schema_exactly(template_text):
    schema = load_look_spec_schema()["$defs"]
    expected = {
        "age_band": schema["character"]["properties"]["age_band"]["enum"],
        "kind": schema["character"]["properties"]["build"]["properties"]["kind"]["enum"],
        "time_of_day_default": schema["location"]["properties"]["time_of_day_default"]["enum"],
    }
    for field, enum in expected.items():
        listed = _enum_from_comment(template_text, field)
        listed = [v.split()[0] for v in listed]  # drop trailing parenthetical notes
        assert listed == enum, f"template enum for {field} drifted from the schema: {listed} != {enum}"


def test_schema_does_not_enumerate_hair(template_text):
    # The template calls hair "category only"; the schema keeps it free text
    # (short_text). If either side ever enumerates it, the other must follow.
    hair = load_look_spec_schema()["$defs"]["character"]["properties"]["hair"]
    assert "enum" not in hair
    assert "category only" in template_text
