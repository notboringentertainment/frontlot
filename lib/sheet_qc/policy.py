"""D19.1 — the QC policy bundle.

Everything that decides a verdict is data here and is hashed together into
``policy_bundle_sha256`` (Codex R2 new#1): per-role checklists, the judge
system prompt, the response JSON schema, the scoring-rules version and the
local-check rules. The signed project config pins that hash; a verdict is
accepted only if it carries the pinned hash, so editing any of these inputs
invalidates every existing verdict instead of silently surviving.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lib.canonical_json import record_sha256

QC_POLICY_VERSION = "1.0"
SCORING_RULES_VERSION = "1.0"
SHEET_ROLES = ("turnaround", "expressions", "wardrobe")
ANSWERS = ("yes", "no", "unsure")
EVIDENCE_KINDS = ("none", "wardrobe_pieces", "hero_image")


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


def checklist(role: str) -> tuple[Item, ...]:
    if role not in CHECKLISTS:
        raise KeyError(f"no QC checklist for role {role!r}; roles are {SHEET_ROLES}")
    return CHECKLISTS[role]


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
    return record_sha256(bundle())


def judge_prompt(role: str, *, wardrobe_pieces: list[str] | None, has_hero: bool) -> str:
    """The user-turn text sent with the image(s). Built only from policy data
    and fields of the SIGNED active look (never caller text)."""
    lines = [f"Sheet role: {role}."]
    if has_hero:
        lines.append("Image 1 is the sheet under inspection. Image 2 is the approved reference face of the same character.")
    else:
        lines.append("Image 1 is the sheet under inspection.")
    if wardrobe_pieces:
        lines.append("Listed wardrobe pieces: " + "; ".join(wardrobe_pieces) + ".")
    lines.append("Questions:")
    for item in checklist(role):
        lines.append(f"- {item.id}: {item.question}")
    return "\n".join(lines)
