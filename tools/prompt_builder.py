"""Structured prompt builder for look-governed image generation (plan D10, D11).

Prompt text for hero / sheet / location generation is NEVER copied verbatim
from a ticket or a director's notes. It is assembled here from the fields of a
validated ``look_spec`` payload (version 1.0), a palette and a role, so that:

- the rendering is deterministic (same payload + palette + role -> same text),
- every free-text field is normalized (whitespace collapsed, control
  characters stripped, quotes/braces neutralized) and bounded,
- injection-shaped lines are refused (instruction verbs aimed at the model,
  role markers, delimiter blocks), and
- known-person name patterns are refused. This is a SMALL documented pattern
  list — honorific + capitalized name, "looks like / resembles / played by /
  portrayed by <Name>", "a young <Name>", possessive-of-celebrity forms — not
  a celebrity database. Resemblance to a real person cannot be proven absent
  here; the writer's ``fictional_subject_attestation`` and the wayfinder
  rubric remain the primary controls (D16).

The result carries a ``prompt_recipe`` ``{look_hash, builder_version,
fields_used[], rendered_sha256}``. ``rendered_sha256`` is the sha256 of the
exact prompt string; a governed ``stage: visual_bible`` call must present a
prompt hashing to it (``tools.video._shared._verify_prompt_recipe``), which
replaces the former ``approved_prompt_block`` verbatim-copy contract.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

BUILDER_VERSION = "1.0"
LOOK_SPEC_VERSION = "1.0"

CHARACTER_ROLES = ("hero", "front", "three_quarter", "profile", "full_body", "expressions", "wardrobe")
LOCATION_ROLES = ("establishing", "detail", "time_variant")
ROLES = CHARACTER_ROLES + LOCATION_ROLES

MAX_FIELD_CHARS = 400
MAX_LIST_ITEMS = 12
MAX_PALETTE = 6

_ROLE_FRAMING = {
    "hero": "single character, head and shoulders portrait, neutral expression, facing camera",
    "front": "single character, full front view, neutral pose, plain studio background",
    "three_quarter": "single character, three-quarter view, neutral pose, plain studio background",
    "profile": "single character, strict side profile, neutral pose, plain studio background",
    "full_body": "single character, full body head to toe, neutral standing pose, plain studio background",
    "expressions": "single character, grid of six facial expressions, same framing each cell, plain background",
    "wardrobe": "single character, full body, wardrobe study, neutral pose, plain studio background",
    "establishing": "wide establishing view, no people",
    "detail": "medium detail view of the place, no people",
    "time_variant": "the same place at a different time of day, no people",
}

# --- refusal patterns (documented, small) ----------------------------------

# Instruction-shaped lines: a leading imperative aimed at the model, a role or
# system marker, or delimiter blocks used to smuggle instructions.
_INJECTION_PATTERNS = (
    re.compile(r"^\s*(ignore|disregard|forget|override|bypass)\b.*\b(instruction|rule|prompt|policy|above|previous)", re.I),
    re.compile(r"^\s*(system|assistant|user|developer)\s*[:>]", re.I),
    re.compile(r"^\s*(you are|act as|pretend to be|from now on|new instructions?)\b", re.I),
    re.compile(r"^\s*(do not|don't|never)\s+(follow|obey|apply)\b", re.I),
    re.compile(r"(```|<\|[a-z_]+\|>|\[INST\]|<\/?(system|prompt|instructions?)>)", re.I),
    re.compile(r"\{\{.*\}\}|\$\{.*\}"),
)

# Known-person name patterns. A capitalized two-part name is only refused
# when framed as a real person: honorific, resemblance/portrayal phrasing,
# "a young <Name>", or "<Name>'s face/look".
_CAP = r"[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?"
_NAME = rf"{_CAP}(?:\s+{_CAP}){{1,2}}"
_NAME_PATTERNS = (
    re.compile(rf"\b(Mr|Mrs|Ms|Miss|Dr|Sir|Dame|Lord|Lady|President|Senator)\.?\s+{_NAME}"),
    re.compile(rf"\b(looks?\s+like|resembl(?:es|ing)|played\s+by|portrayed\s+by|in\s+the\s+style\s+of\s+actor|like\s+actor|like\s+actress|as\s+played\s+by|lookalike\s+of|doppelg[aä]nger\s+of)\s+(?:a\s+|an\s+|the\s+)?(?:young\s+|old(?:er)?\s+)?{_NAME}", re.I),
    re.compile(rf"\b(a|an)\s+(young|older|middle-aged)\s+{_NAME}\b"),
    re.compile(rf"\b{_NAME}'s\s+(face|features|look|likeness|eyes|smile|jawline)\b"),
    re.compile(rf"\b(celebrity|famous\s+(?:actor|actress|singer|politician|athlete))\s+{_NAME}\b", re.I),
)


class PromptBuildError(ValueError):
    """The look_spec payload cannot be rendered safely."""


def rendered_prompt_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_DELIMITER_MAP = str.maketrans({'"': "'", "`": "'", "{": "(", "}": ")", "[": "(", "]": ")", "<": "(", ">": ")", "|": "/", "\\": "/"})


def canonical_text(value: Any, *, field: str) -> str:
    """NFKC, control/format characters stripped, whitespace collapsed — the
    form the safety patterns are checked against (delimiters still intact)."""
    if not isinstance(value, str):
        raise PromptBuildError(f"{field}: expected text, got {type(value).__name__}")
    text = unicodedata.normalize("NFKC", value)
    # Tabs/newlines become spaces; every other control or format character
    # (zero-width, bidi overrides, ...) is dropped.
    text = "".join(" " if ch.isspace() else ch for ch in text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_FIELD_CHARS:
        raise PromptBuildError(f"{field}: exceeds {MAX_FIELD_CHARS} characters after normalization")
    return text


def normalize_text(value: Any, *, field: str) -> str:
    """``canonical_text`` with quoting and structural delimiters neutralized;
    the provider gets prose only."""
    return canonical_text(value, field=field).translate(_DELIMITER_MAP)


def check_safe_line(text: str, *, field: str) -> None:
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            raise PromptBuildError(f"{field}: injection-shaped text refused ({pat.pattern[:40]}...)")
    for pat in _NAME_PATTERNS:
        if pat.search(text):
            raise PromptBuildError(f"{field}: real-person name pattern refused; describe the fictional subject in type terms")


def _clean(value: Any, field: str) -> str:
    raw = canonical_text(value, field=field)
    if not raw:
        raise PromptBuildError(f"{field}: empty after normalization")
    # Patterns are checked on the canonical text (delimiters intact, so a
    # fenced/templated block is still recognisable) and again after
    # neutralization (so nothing rendered can slip through).
    check_safe_line(raw, field=field)
    text = raw.translate(_DELIMITER_MAP)
    check_safe_line(text, field=field)
    return text


def _clean_list(values: Any, field: str, *, limit: int = MAX_LIST_ITEMS) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise PromptBuildError(f"{field}: expected a list")
    if len(values) > limit:
        raise PromptBuildError(f"{field}: more than {limit} items")
    out = []
    for i, item in enumerate(values):
        if isinstance(item, dict):
            # Structured items (wardrobe pieces, variants): render name/when-style pairs.
            parts = [_clean(v, f"{field}[{i}].{k}") for k, v in sorted(item.items()) if isinstance(v, str) and v.strip()]
            if parts:
                out.append(", ".join(parts))
        else:
            out.append(_clean(item, f"{field}[{i}]"))
    return out


def _palette(palette: Any) -> list[str]:
    if palette is None:
        return []
    if not isinstance(palette, list):
        raise PromptBuildError("palette: expected a list of named colors")
    if len(palette) > MAX_PALETTE:
        raise PromptBuildError(f"palette: more than {MAX_PALETTE} colors")
    out = []
    for i, item in enumerate(palette):
        name = item.get("name") if isinstance(item, dict) else item
        out.append(_clean(name, f"palette[{i}]"))
    return out


def _character_sections(spec: dict[str, Any]) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    if spec.get("age_band"):
        sections.append(("age_band", f"age band {_clean(spec['age_band'], 'age_band')}"))
    build = spec.get("build")
    if isinstance(build, dict):
        parts = [_clean(build[k], f"build.{k}") for k in ("type", "note") if isinstance(build.get(k), str) and build[k].strip()]
        if parts:
            sections.append(("build", "build " + ", ".join(parts)))
    elif isinstance(build, str) and build.strip():
        sections.append(("build", f"build {_clean(build, 'build')}"))
    if spec.get("hair"):
        sections.append(("hair", f"hair {_clean(spec['hair'], 'hair')}"))
    marks = _clean_list(spec.get("distinguishing_marks"), "distinguishing_marks", limit=8)
    if marks:
        sections.append(("distinguishing_marks", "distinguishing marks: " + "; ".join(marks)))
    wardrobe = spec.get("default_wardrobe")
    if isinstance(wardrobe, dict):
        pieces = _clean_list(wardrobe.get("pieces"), "default_wardrobe.pieces", limit=8)
        if pieces:
            sections.append(("default_wardrobe", "wearing " + ", ".join(pieces)))
    props = _clean_list(spec.get("props"), "props", limit=6)
    if props:
        sections.append(("props", "props: " + ", ".join(props)))
    if spec.get("era_and_class_signals"):
        sections.append(("era_and_class_signals", _clean(spec["era_and_class_signals"], "era_and_class_signals")))
    if spec.get("heritage_note"):
        # Writer-stated only (D16); rendered as given, never inferred.
        sections.append(("heritage_note", _clean(spec["heritage_note"], "heritage_note")))
    return sections


def _location_sections(spec: dict[str, Any]) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    if spec.get("establishing_view"):
        sections.append(("establishing_view", _clean(spec["establishing_view"], "establishing_view")))
    if spec.get("architecture_or_terrain"):
        sections.append(("architecture_or_terrain", _clean(spec["architecture_or_terrain"], "architecture_or_terrain")))
    dressing = _clean_list(spec.get("dressing"), "dressing", limit=10)
    if dressing:
        sections.append(("dressing", "dressing: " + ", ".join(dressing)))
    if spec.get("time_of_day_default"):
        sections.append(("time_of_day_default", f"time of day {_clean(spec['time_of_day_default'], 'time_of_day_default')}"))
    if spec.get("weather_or_light_rules"):
        sections.append(("weather_or_light_rules", _clean(spec["weather_or_light_rules"], "weather_or_light_rules")))
    anchors = _clean_list(spec.get("palette_anchors"), "palette_anchors", limit=4)
    if anchors:
        sections.append(("palette_anchors", "palette anchors: " + ", ".join(anchors)))
    return sections


def build_prompt(
    look_spec: dict[str, Any],
    *,
    look_hash: str,
    role: str,
    palette: list[Any] | None = None,
) -> dict[str, Any]:
    """Render ``{prompt, negative, prompt_recipe}`` from a validated look_spec.

    ``look_spec`` must already have passed schema validation (lib side); this
    builder re-checks only what it needs to render safely. ``look_hash`` is
    the caller-supplied digest of that validated payload and is bound into the
    recipe unchanged.
    """
    if not isinstance(look_spec, dict):
        raise PromptBuildError("look_spec must be an object")
    if str(look_spec.get("version")) != LOOK_SPEC_VERSION:
        raise PromptBuildError(f"look_spec.version must be {LOOK_SPEC_VERSION!r}")
    kind = look_spec.get("entity_kind")
    if kind not in ("character", "location"):
        raise PromptBuildError("look_spec.entity_kind must be character or location")
    if look_spec.get("shape_only") is True:
        raise PromptBuildError("shape_only looks are never generation-sufficient")
    if kind == "character" and look_spec.get("minor") is True:
        raise PromptBuildError("looks for minors are refused for generation")
    if look_spec.get("fictional_subject_attestation") is not True:
        raise PromptBuildError("fictional_subject_attestation must be true")
    if not isinstance(look_hash, str) or len(look_hash) != 64:
        raise PromptBuildError("look_hash must be a 64-hex sha256")
    if role not in ROLES:
        raise PromptBuildError(f"role must be one of {ROLES}")
    if (kind == "character") != (role in CHARACTER_ROLES):
        raise PromptBuildError(f"role {role!r} does not apply to a {kind} look")

    sections: list[tuple[str, str]] = [
        ("prompt_safe_description", _clean(look_spec.get("prompt_safe_description"), "prompt_safe_description")),
    ]
    sections += _character_sections(look_spec) if kind == "character" else _location_sections(look_spec)
    palette_names = _palette(palette)
    if palette_names:
        sections.append(("palette", "palette: " + ", ".join(palette_names)))
    sections.append(("role", _ROLE_FRAMING[role]))

    negative_lines = _clean_list(look_spec.get("negative_lines"), "negative_lines", limit=12)
    negative = ", ".join(negative_lines + ["text", "watermark", "logo", "extra limbs", "deformed"])

    prompt = ". ".join(text for _, text in sections) + "."
    fields_used = [name for name, _ in sections]
    return {
        "prompt": prompt,
        "negative": negative,
        "prompt_recipe": {
            "look_hash": look_hash,
            "builder_version": BUILDER_VERSION,
            "fields_used": fields_used,
            "rendered_sha256": rendered_prompt_sha256(prompt),
        },
    }


# Backwards-friendly alias name used in the plan text.
builder_version = BUILDER_VERSION
