"""D19.1 — the QC policy bundle(s).

Everything that decides a verdict is data here and is hashed together into a
policy bundle hash (Codex R2 new#1): per-role checklists, the judge system
prompt, the response JSON schema, the scoring-rules version and the
local-check rules. The signed project config pins that hash; a verdict is
accepted only if it carries the pinned hash, so editing any of these inputs
invalidates every existing verdict instead of silently surviving.

D20 (R2#1, R4#4): TWO independent bundles, pinned separately.

* the SHEET bundle (``bundle()`` / ``bundle_sha256()``, alias
  ``sheet_bundle_sha256()``) — exactly what D19 shipped; its hash is pinned
  by a golden test to the value Bloodless signed on 2026-08-27, so adopting
  1.4 invalidates no sheet verdict;
* the HERO bundle (``hero_bundle()`` / ``hero_bundle_sha256()``) — the
  ``hero`` checklist, its evidence kinds and local rules, the shared system
  prompt and scoring version. Pinned as ``qc.hero_policy_sha256`` in a 1.2
  config.

D21: a THIRD bundle, ``location_bundle()`` / ``location_bundle_sha256()`` —
the ``establishing`` and ``angle`` checklists, their local size rules and a
system prompt of their own (a location plate is not a character sheet, so the
character-sheet wording would mislead the judge). The sheet and hero bundles
are byte-frozen: ``system_prompt_for`` hands both of them the identical
``SYSTEM_PROMPT`` object, so adding the location prompt moves neither hash.

``bundle_sha256_for_role`` says which hash a verdict for a role must carry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lib.canonical_json import record_sha256

QC_POLICY_VERSION = "1.0"
SCORING_RULES_VERSION = "1.0"
SHEET_ROLES = ("turnaround", "expressions", "wardrobe")
HERO_ROLE = "hero"
ALL_ROLES = SHEET_ROLES + (HERO_ROLE,)
# D21: the two location judge roles. ``angle`` is never judged alone — the
# approved establishing plate rides along as evidence (global constraint 5).
LOCATION_ROLES = ("establishing", "angle")
KNOWN_ROLES = ALL_ROLES + LOCATION_ROLES
ANSWERS = ("yes", "no", "unsure")
# D20: look_* evidence is text from fields of the SIGNED active look.
# D21 adds the location look fields plus ``establishing_image``, the one
# image-shaped location evidence kind (it carries no text).
EVIDENCE_KINDS = ("none", "wardrobe_pieces", "hero_image", "look_hair", "look_age_band", "look_build",
                  "look_architecture", "look_establishing", "look_time_of_day", "look_palette",
                  "establishing_image")
LOOK_EVIDENCE_FIELDS = {"look_hair": "hair", "look_age_band": "age_band", "look_build": "build"}
LOCATION_LOOK_EVIDENCE_FIELDS = {
    "look_architecture": "architecture_or_terrain",
    "look_establishing": "establishing_view",
    "look_time_of_day": "time_of_day_default",
    "look_palette": "palette_anchors",
}
# How each location evidence kind is introduced in the judge prompt.
LOCATION_EVIDENCE_LABELS = {
    "look_architecture": "architecture or terrain",
    "look_establishing": "establishing view",
    "look_time_of_day": "time of day",
    "look_palette": "palette anchors",
}
# The long free-text location fields are capped where they are signed
# (lib.look_spec.LOCATION_TEXT_MAX_CHARS) and again here, where they become
# judge evidence. Keep the two in agreement — the prompt_safe_description
# precedent (lib/look_spec.py:37, tools/prompt_builder.py:52).
LOCATION_TEXT_MAX_CHARS = 600


@dataclass(frozen=True)
class Item:
    id: str
    question: str          # YES means the sheet passes this item
    severity: str = "fail" # fail | warn
    evidence: str = "none" # none | wardrobe_pieces | hero_image


CHECKLISTS: dict[str, tuple[Item, ...]] = {
    "turnaround": (
        Item("panels_7", "Does the sheet show exactly seven panels: four full-body figures in the top row over three close-up portraits in the bottom row?"),
        Item("profiles_opposite", "Do the two full-body profile panels face OPPOSITE directions (in one the nose points to the left edge of the image, in the other to the right edge)?"),
        Item("portraits_opposite", "Do the two profile portraits face OPPOSITE directions (one nose to the left edge, one to the right edge)?"),
        Item("shoes_visible", "Are both shoes fully inside the frame on every full-body panel, with nothing cut off at the bottom?"),
        Item("hands_empty", "Are the hands empty in every panel, with nothing held and nothing in or at the mouth (no cigarette, no phone, no cup)?"),
        Item("wardrobe_complete", "Is every listed wardrobe piece visible where the angle allows?", evidence="wardrobe_pieces"),
        Item("head_height", "Is the head at the same height across the full-body row, and is the face the same size across the three portraits?", severity="warn"),
        Item("one_light", "Is the lighting direction the same across all panels?", severity="warn"),
        Item("single_subject", "Is there exactly one person, and is it clearly the same person, in every panel?"),
        Item("no_text", "Is the image free of text, watermarks and logos?"),
    ),
    "expressions": (
        Item("grid_2x3", "Is the image one grid of six cells, two rows of three, with identical framing in every cell?"),
        Item("head_shoulders_only", "Does every cell show head and shoulders only, with nothing below the chest?"),
        Item("realism_matches_hero", "Do the cells match the photographic realism of the reference face (no illustration or cartoon look)?", severity="warn", evidence="hero_image"),
        Item("mouth_clear", "Is there nothing in or at the mouth in any cell (no cigarette, no object, no hand)?"),
        Item("no_hands_props", "Are hands and props absent from every cell?"),
        Item("six_distinct", "Are the six expressions clearly different from one another?", severity="warn"),
        Item("single_subject", "Is it clearly the same single person in every cell?"),
        Item("no_text", "Is the image free of text, watermarks and logos?"),
    ),
    "wardrobe": (
        Item("variants_side_by_side", "Does the image show the character at least twice side by side in different outfits?"),
        Item("full_body", "Is every figure shown head to toe with shoes inside the frame?"),
        Item("pieces_visible", "Is every listed wardrobe piece visible on the default-outfit figure?", evidence="wardrobe_pieces"),
        Item("single_subject", "Is it clearly the same single person in every figure?"),
        Item("no_text", "Is the image free of text, watermarks and logos?"),
    ),
}

# D20.3 — the HERO checklist, in its own bundle. YES = the candidate passes.
HERO_CHECKLIST: tuple[Item, ...] = (
    Item("single_subject", "Is there exactly one person, with no second face and no reflection of a face?"),
    Item("bust_front", "Is it a head-and-shoulders portrait facing the camera with both eyes visible?"),
    Item("neutral_expression", "Is the expression neutral or near-neutral, with the mouth closed or only naturally parted?"),
    Item("plain_background", "Is the background plain and uncluttered, with no scenery, text or props?"),
    Item("no_occlusion", "Is the face unobstructed — no hands, glasses, hat, mask or hair across the eyes?"),
    Item("hair_matches", "Does the hair match the described hair?", evidence="look_hair"),
    Item("age_matches", "Does the apparent age fall within the described age band?", severity="warn", evidence="look_age_band"),
    Item("build_matches", "Does the visible build match the described build?", severity="warn", evidence="look_build"),
    Item("no_text", "Is the image free of text, watermarks and logos?"),
    Item("photoreal_or_treatment", "Is the rendering style consistent within the image (no half-illustrated face, no mixed media)?", severity="warn"),
)

# D21 — the LOCATION checklists, in their own bundle. YES = the plate passes.
# Both roles share everything except how the place is identified: an
# establishing plate is checked against the described establishing view; an
# angle is checked against the approved establishing plate itself (R1#8).
_LOCATION_ARCHITECTURE = Item(
    "architecture_matches",
    "Does the built form or terrain match the described architecture or terrain?",
    evidence="look_architecture")
_LOCATION_COMMON: tuple[Item, ...] = (
    Item("time_of_day_matches", "Does the light and sky read as the described time of day?",
         evidence="look_time_of_day"),
    Item("palette_anchors_present", "Are the described palette anchor colours present in the image?",
         severity="warn", evidence="look_palette"),
    Item("no_people", "Is the image free of people and human figures?"),
    Item("no_text", "Is the image free of legible text, lettering, signage words and logos?"),
    Item("no_watermark_or_border", "Is the image free of watermarks, frames, borders and letterboxing?"),
    Item("single_place", "Does the image show one single coherent place, not a collage, grid or split panel?"),
    Item("render_quality", "Is the image free of obvious render defects such as melted geometry, "
                           "impossible structure or smeared detail?"),
)
LOCATION_CHECKLISTS: dict[str, tuple[Item, ...]] = {
    "establishing": (
        _LOCATION_ARCHITECTURE,
        Item("view_matches", "Is the camera looking at the place from the described establishing view?",
             evidence="look_establishing"),
    ) + _LOCATION_COMMON,
    "angle": (
        Item("same_place", "Is image 1 the same place as the approved establishing plate in image 2, "
                           "sharing its architecture, materials and dressing?", evidence="establishing_image"),
        _LOCATION_ARCHITECTURE,
    ) + _LOCATION_COMMON,
}

SYSTEM_PROMPT = (
    "You are a strict quality-control inspector for character reference sheets used in film pre-production. "
    "You will receive one sheet image (and sometimes a reference face image). Answer every question by its id "
    "exactly once with yes, no or unsure. YES means the sheet PASSES that check. Say unsure whenever you cannot "
    "tell; never guess. Keep each note under twenty words. Output JSON only, matching the provided schema."
)

# D21 — location plates are judged under their own wording. Kept separate so
# the sheet and hero bundles keep SYSTEM_PROMPT byte for byte.
LOCATION_SYSTEM_PROMPT = (
    "You are a strict quality-control inspector for location reference plates used in film pre-production. "
    "You will receive one location image (and sometimes the approved establishing plate of the same place). "
    "Answer every question by its id exactly once with yes, no or unsure. YES means the plate PASSES that "
    "check. Say unsure whenever you cannot tell; never guess. Keep each note under twenty words. Output JSON "
    "only, matching the provided schema."
)


def system_prompt_for(role: str) -> str:
    """The judge's system turn for ``role``. Sheet and hero roles share the
    character-sheet prompt object byte for byte (their bundle hashes must not
    move); location roles get LOCATION_SYSTEM_PROMPT."""
    return LOCATION_SYSTEM_PROMPT if is_location_role(role) else SYSTEM_PROMPT


# The response schema the judge must satisfy. ``id`` enum is filled per role.
def response_schema(role: str) -> dict[str, Any]:
    ids = [i.id for i in checklist(role)]
    return {
        "type": "object", "additionalProperties": False, "required": ["items"],
        "properties": {"items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "answer", "note"],
            "properties": {"id": {"type": "string", "enum": ids},
                           "answer": {"type": "string", "enum": list(ANSWERS)},
                           "note": {"type": "string"}}}}},
    }


# Deterministic pre-check rules (lib.sheet_qc.local_checks reads these).
LOCAL_RULES: dict[str, Any] = {
    "version": "1.0",
    "format": "png",
    "alpha_allowed": False,
    "max_bytes": 40_000_000,
    "size": {
        "turnaround": {"width": 2560, "height": 1600, "tolerance": 0.02},
        "expressions": {"aspect": 1.5, "tolerance": 0.05, "min_long_edge": 1024},
        "wardrobe": {"min_long_edge": 1024},
    },
}


# Hero local rules live OUTSIDE LOCAL_RULES so the sheet bundle's bytes (and
# hash) are untouched by D20. Same shape; read through ``local_rules_for``.
HERO_LOCAL_RULES: dict[str, Any] = {
    "version": "1.0",
    "format": "png",
    "alpha_allowed": False,
    "max_bytes": 40_000_000,
    "size": {
        "hero": {"orientation": "square_or_portrait", "min_long_edge": 1024},
    },
}


# D21 — location plates are wide by nature; a portrait plate is refused
# locally, before any paid judge call.
LOCATION_LOCAL_RULES: dict[str, Any] = {
    "version": "1.0",
    "format": "png",
    "alpha_allowed": False,
    "max_bytes": 40_000_000,
    "size": {
        "establishing": {"orientation": "landscape_or_square", "min_long_edge": 1024},
        "angle": {"orientation": "landscape_or_square", "min_long_edge": 1024},
    },
}


def is_hero_role(role: str) -> bool:
    return role == HERO_ROLE


def is_location_role(role: str) -> bool:
    return role in LOCATION_ROLES


def checklist(role: str) -> tuple[Item, ...]:
    if role == HERO_ROLE:
        return HERO_CHECKLIST
    if is_location_role(role):
        return LOCATION_CHECKLISTS[role]
    if role not in CHECKLISTS:
        raise KeyError(f"no QC checklist for role {role!r}; roles are {KNOWN_ROLES}")
    return CHECKLISTS[role]


def local_rules_for(role: str) -> dict[str, Any]:
    """The local-check rule set a role is judged under (part of its bundle)."""
    if is_location_role(role):
        return LOCATION_LOCAL_RULES
    return HERO_LOCAL_RULES if role == HERO_ROLE else LOCAL_RULES


def checklist_payload(role: str) -> list[dict[str, str]]:
    return [{"id": i.id, "question": i.question, "severity": i.severity, "evidence": i.evidence}
            for i in checklist(role)]


def bundle() -> dict[str, Any]:
    """The complete executable policy as data."""
    return {
        "policy_version": QC_POLICY_VERSION,
        "scoring_rules_version": SCORING_RULES_VERSION,
        "checklists": {role: checklist_payload(role) for role in SHEET_ROLES},
        "system_prompt": SYSTEM_PROMPT,
        "response_schemas": {role: response_schema(role) for role in SHEET_ROLES},
        "local_rules": LOCAL_RULES,
    }


def bundle_sha256() -> str:
    """The SHEET bundle hash (D19 meaning, unchanged by D20)."""
    return record_sha256(bundle())


sheet_bundle_sha256 = bundle_sha256


def hero_bundle() -> dict[str, Any]:
    """The complete executable HERO policy as data (D20.3)."""
    return {
        "policy_version": QC_POLICY_VERSION,
        "scoring_rules_version": SCORING_RULES_VERSION,
        "checklists": {HERO_ROLE: checklist_payload(HERO_ROLE)},
        "system_prompt": SYSTEM_PROMPT,
        "response_schemas": {HERO_ROLE: response_schema(HERO_ROLE)},
        "local_rules": HERO_LOCAL_RULES,
    }


def hero_bundle_sha256() -> str:
    return record_sha256(hero_bundle())


def location_bundle() -> dict[str, Any]:
    """The complete executable LOCATION policy as data (D21)."""
    return {
        "policy_version": QC_POLICY_VERSION,
        "scoring_rules_version": SCORING_RULES_VERSION,
        "checklists": {role: checklist_payload(role) for role in LOCATION_ROLES},
        "system_prompt": LOCATION_SYSTEM_PROMPT,
        "response_schemas": {role: response_schema(role) for role in LOCATION_ROLES},
        "local_rules": LOCATION_LOCAL_RULES,
    }


def location_bundle_sha256() -> str:
    return record_sha256(location_bundle())


def bundle_sha256_for_role(role: str) -> str:
    """Which bundle hash a verdict for ``role`` must carry."""
    if is_location_role(role):
        return location_bundle_sha256()
    return hero_bundle_sha256() if role == HERO_ROLE else bundle_sha256()


def look_evidence(role: str, look_payload: dict[str, Any] | None) -> dict[str, str]:
    """``{evidence_kind: text}`` for the look_* evidence the role's checklist
    needs, read from fields of the SIGNED active look payload (never caller
    text). A missing field yields an empty string; the judge then answers
    ``unsure`` (scored as ``no``). ``establishing_image`` evidence is an image,
    not text, so it never appears here.

    For location roles the caller may replace ``look_time_of_day`` with the
    per-slot recipe's time of day before calling ``judge_prompt`` — the plate
    is then judged against what it was ordered to be, not against the look's
    default. Every other entry comes from the signed look and only the signed
    look."""
    out: dict[str, str] = {}
    payload = look_payload or {}
    if is_location_role(role):
        for item in checklist(role):
            field = LOCATION_LOOK_EVIDENCE_FIELDS.get(item.evidence)
            if field is None:
                continue
            out[item.evidence] = _location_field_text(field, payload.get(field))
        return out
    for item in checklist(role):
        field = LOOK_EVIDENCE_FIELDS.get(item.evidence)
        if field is not None:
            out[item.evidence] = str(payload.get(field) or "").strip()
    return out


def _location_field_text(field: str, value: Any) -> str:
    """One location look field as judge-ready text. Lists are joined; the long
    free-text fields are re-checked against the cap they were signed under, so
    an over-cap look fails closed instead of blowing the evidence budget."""
    if isinstance(value, (list, tuple)):
        text = ", ".join(str(v).strip() for v in value if str(v).strip())
    else:
        text = str(value or "").strip()
    if len(text) > LOCATION_TEXT_MAX_CHARS:
        raise ValueError(
            f"{field} exceeds the {LOCATION_TEXT_MAX_CHARS}-character evidence cap ({len(text)}); "
            f"the look must be superseded within the cap before it can be judged"
        )
    return text


def judge_prompt(role: str, *, wardrobe_pieces: list[str] | None = None, has_hero: bool = False,
                 look_fields: dict[str, str] | None = None) -> str:
    """The user-turn text sent with the image(s). Built only from policy data
    and fields of the SIGNED active look (never caller text)."""
    if is_location_role(role):
        lines = [f"Location role: {role}."]
        if role == "angle":
            lines.append("Image 1 is the candidate, Image 2 is the approved establishing plate of the same place.")
        else:
            lines.append("Image 1 is the candidate under inspection. There is no second image; judge it on its "
                         "own and against the described place.")
    elif role == HERO_ROLE:
        lines = ["Candidate role: hero (a reference headshot of a fictional character)."]
        lines.append("Image 1 is the candidate under inspection. There is no reference face; judge the image on its own and against the described look.")
    else:
        lines = [f"Sheet role: {role}."]
        if has_hero:
            lines.append("Image 1 is the sheet under inspection. Image 2 is the approved reference face of the same character.")
        else:
            lines.append("Image 1 is the sheet under inspection.")
    if wardrobe_pieces:
        lines.append("Listed wardrobe pieces: " + "; ".join(wardrobe_pieces) + ".")
    for kind, text in sorted((look_fields or {}).items()):
        label = (LOCATION_EVIDENCE_LABELS.get(kind) or LOOK_EVIDENCE_FIELDS.get(kind, kind)).replace("_", " ")
        lines.append(f"Described {label}: {text or '(not described)'}.")
    lines.append("Questions:")
    for item in checklist(role):
        lines.append(f"- {item.id}: {item.question}")
    return "\n".join(lines)
