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

``bundle_for_role`` says which hash a verdict for a role must carry.
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
ANSWERS = ("yes", "no", "unsure")
# D20: look_* evidence is text from fields of the SIGNED active look.
EVIDENCE_KINDS = ("none", "wardrobe_pieces", "hero_image", "look_hair", "look_age_band", "look_build")
LOOK_EVIDENCE_FIELDS = {"look_hair": "hair", "look_age_band": "age_band", "look_build": "build"}


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

SYSTEM_PROMPT = (
    "You are a strict quality-control inspector for character reference sheets used in film pre-production. "
    "You will receive one sheet image (and sometimes a reference face image). Answer every question by its id "
    "exactly once with yes, no or unsure. YES means the sheet PASSES that check. Say unsure whenever you cannot "
    "tell; never guess. Keep each note under twenty words. Output JSON only, matching the provided schema."
)

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


def is_hero_role(role: str) -> bool:
    return role == HERO_ROLE


def checklist(role: str) -> tuple[Item, ...]:
    if role == HERO_ROLE:
        return HERO_CHECKLIST
    if role not in CHECKLISTS:
        raise KeyError(f"no QC checklist for role {role!r}; roles are {ALL_ROLES}")
    return CHECKLISTS[role]


def local_rules_for(role: str) -> dict[str, Any]:
    """The local-check rule set a role is judged under (part of its bundle)."""
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


def bundle_sha256_for_role(role: str) -> str:
    """Which bundle hash a verdict for ``role`` must carry."""
    return hero_bundle_sha256() if role == HERO_ROLE else bundle_sha256()


def look_evidence(role: str, look_payload: dict[str, Any] | None) -> dict[str, str]:
    """``{evidence_kind: text}`` for the look_* evidence the role's checklist
    needs, read from fields of the SIGNED active look payload (never caller
    text). A missing field yields an empty string; the judge then answers
    ``unsure`` (scored as ``no``)."""
    out: dict[str, str] = {}
    payload = look_payload or {}
    for item in checklist(role):
        field = LOOK_EVIDENCE_FIELDS.get(item.evidence)
        if field is not None:
            out[item.evidence] = str(payload.get(field) or "").strip()
    return out


def judge_prompt(role: str, *, wardrobe_pieces: list[str] | None, has_hero: bool,
                 look_fields: dict[str, str] | None = None) -> str:
    """The user-turn text sent with the image(s). Built only from policy data
    and fields of the SIGNED active look (never caller text)."""
    if role == HERO_ROLE:
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
        label = LOOK_EVIDENCE_FIELDS.get(kind, kind).replace("_", " ")
        lines.append(f"Described {label}: {text or '(not described)'}.")
    lines.append("Questions:")
    for item in checklist(role):
        lines.append(f"- {item.id}: {item.question}")
    return "\n".join(lines)
