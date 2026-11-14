"""Shared canonical-JSON vectors for WriterOS (look sessions plan, Task 0).

WriterOS computes ``look_hash`` in TypeScript and must produce the same bytes
as ``lib.canonical_json`` for every input it can legally produce. This module
owns the vector file both repos pin:

- ``match``: inputs inside the WriterOS subset (finite numbers within the JS
  safe-integer range). WriterOS must reproduce ``canonical`` and ``sha256``.
- ``refuse``: inputs OpenMontage serialises but WriterOS must refuse (numbers
  outside the JS safe-integer range). No expected output is recorded.

Regenerate after an intentional change with::

    python tests/lib/test_canonical_vectors.py --regenerate

then update ``CANONICAL_VECTORS_SHA256`` below and copy the file and the
constant into WriterOS (``tests/fixtures/lookSpec/vectors.json``).
Invented names only.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unicodedata
from pathlib import Path

if __name__ == "__main__":  # allow running as a script from the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.canonical_json import canonical_bytes, record_sha256

VECTORS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "canonical_vectors.json"
CANONICAL_VECTORS_SHA256 = "4c1016d9b95628fd8a883347cde2b71fd3b4b06009f7d1d9a428e80e71865b98"

_CAFE_NFC = unicodedata.normalize("NFC", "café")
_CAFE_NFD = unicodedata.normalize("NFD", "café")

_CHARACTER_1_1 = {
    "version": "1.1",
    "entity_kind": "character",
    "entity_id": "vector-engineer",
    "fictional_subject_attestation": True,
    "minor": False,
    "prompt_safe_description": (
        "A wiry station engineer in her thirties with cropped ash-blond hair, grey eyes behind "
        "wire-rimmed glasses, a weathered grey work coat over a rust jumpsuit, and a habit of "
        "standing with one hand on any nearby rail."
    ),
    "continuity_risks": ["hair length drifts between shots", "glasses vanish in close-ups"],
    "negative_lines": ["no hat"],
    "spoiler": False,
    "depends_on": [
        {"writeros_record_id": "mem_" + "0123456789abcdef" * 2, "content_hash": "ab" * 32},
    ],
    "shape_only": False,
    "age_band": "thirties",
    "build": {"kind": "lean", "note": "long limbs"},
    "hair": "cropped ash-blond",
    "distinguishing_marks": ["burn scar on left wrist"],
    "default_wardrobe": {"pieces": ["grey work coat", "rust jumpsuit"]},
    "wardrobe_variants": [{"name": "dress blues", "when": "the ceremony"}],
    "props": ["torque wrench"],
    "era_and_class_signals": "near-future working crew, café-bar regular",
}

_LOCATION_1_1 = {
    "version": "1.1",
    "entity_kind": "location",
    "entity_id": "vector-station",
    "fictional_subject_attestation": True,
    "minor": False,
    "prompt_safe_description": (
        "A salt-crusted orbital refuelling station of ribbed steel corridors, sodium-lit and "
        "humming, with frost blooming along every window seam and cargo netting slung between "
        "the pillars."
    ),
    "continuity_risks": ["frost pattern must match between angles"],
    "negative_lines": [],
    "spoiler": True,
    "depends_on": [],
    "shape_only": False,
    "establishing_view": "wide down the main corridor toward the airlock",
    "time_of_day_default": "night",
    "palette_anchors": ["sodium amber", "frost white", "gunmetal"],
    "architecture_or_terrain": "ribbed steel corridors",
    "dressing": ["cargo netting", "a \"no smoking\" sign\twith a tab"],
    "weather_or_light_rules": "always sodium-lit, never daylight",
}

MATCH_INPUTS: list[tuple[str, object]] = [
    ("key-order-nested", {"z": 1, "a": {"y": 2, "b": 3}}),
    ("non-ascii-keys", {"é": 1, "e": 2, "ß": 3, "日本": 4, "\U0001F600": 5, "ﬁ": 6}),
    ("nfc-string", {"name": _CAFE_NFC}),
    ("nfd-string", {"name": _CAFE_NFD}),
    ("nfd-key", {_CAFE_NFD: True}),
    ("control-and-escapes", {"s": "quote\" backslash\\ tab\t newline\n bell\u0007 nul\u0000"}),
    ("int-one", 1),
    ("float-one", 1.0),
    ("negative-zero", -0.0),
    ("tenth", 0.1),
    ("small-exponent", 1e-7),
    ("max-safe-integer", 9007199254740991),
    ("min-safe-integer", -9007199254740991),
    ("array-mixed", [1, "two", None, True, False, [], {}]),
    ("booleans-and-null", {"t": True, "f": False, "n": None}),
    ("empty-object", {}),
    ("look-character-1.1", _CHARACTER_1_1),
    ("look-location-1.1", _LOCATION_1_1),
]

# Written into the file as JSON number literals; WriterOS parses them and must throw.
REFUSE_INPUTS: list[tuple[str, object]] = [
    ("float-1e21", 1e21),
    ("int-above-safe", 9007199254740993),
    ("int-below-safe", -9007199254740993),
    ("nested-unsafe", {"look": {"count": 2**60}}),
]


def build_vectors() -> dict:
    return {
        "match": [
            {
                "name": name,
                "input": value,
                "canonical": canonical_bytes(value).decode("utf-8"),
                "sha256": record_sha256(value),
            }
            for name, value in MATCH_INPUTS
        ],
        "refuse": [{"name": name, "input": value} for name, value in REFUSE_INPUTS],
    }


def render(vectors: dict) -> bytes:
    # ensure_ascii keeps NFD vs NFC distinguishable in the file bytes.
    return (json.dumps(vectors, ensure_ascii=True, indent=2) + "\n").encode("ascii")


def test_committed_vectors_match_canonical_json():
    assert VECTORS_PATH.exists(), "run: python tests/lib/test_canonical_vectors.py --regenerate"
    raw = VECTORS_PATH.read_bytes()
    assert raw == render(build_vectors()), "vector file is stale; regenerate it"


def test_vector_file_hash_is_pinned():
    assert hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest() == CANONICAL_VECTORS_SHA256


def test_nfc_and_nfd_vectors_hash_equal():
    by_name = {v["name"]: v for v in build_vectors()["match"]}
    assert by_name["nfc-string"]["sha256"] == by_name["nfd-string"]["sha256"]


def test_file_round_trips_match_inputs():
    # What WriterOS reads (JSON.parse of the file) must canonicalise to the recorded bytes here too.
    loaded = json.loads(VECTORS_PATH.read_text("ascii"))
    for vector in loaded["match"]:
        assert canonical_bytes(vector["input"]).decode("utf-8") == vector["canonical"], vector["name"]
        assert record_sha256(vector["input"]) == vector["sha256"], vector["name"]


def test_refuse_inputs_are_outside_js_safe_range():
    def unsafe(value) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return abs(value) > 2**53 - 1
        if isinstance(value, dict):
            return any(unsafe(v) for v in value.values())
        if isinstance(value, list):
            return any(unsafe(v) for v in value)
        return False

    for name, value in REFUSE_INPUTS:
        assert unsafe(value), name
        canonical_bytes(value)  # OpenMontage itself accepts them


if __name__ == "__main__":
    if "--regenerate" not in sys.argv[1:]:
        sys.exit("usage: python tests/lib/test_canonical_vectors.py --regenerate")
    data = render(build_vectors())
    VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    VECTORS_PATH.write_bytes(data)
    print(f"wrote {VECTORS_PATH}")
    print(f"CANONICAL_VECTORS_SHA256 = {hashlib.sha256(data).hexdigest()}")
