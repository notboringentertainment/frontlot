"""look_spec 1.1 (look sessions, 2026-09-30): the WriterOS dependency reference,
both versions accepted everywhere a look is validated or rendered, and the
look packet carrying either a wayfinder ticket or a WriterOS promotion as its
source. Invented names only."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from lib.look_spec import LookSpecError, look_hash, validate_look_spec
from tests.lib.look_lock_helpers import character_look, location_look
from tools import prompt_builder as pb

ROOT = Path(__file__).resolve().parents[2]
WRITEROS_REF = {"writeros_record_id": "mem_" + "0123456789abcdef" * 2, "content_hash": "ab" * 32}


def _vector_blocks() -> dict[str, dict]:
    vectors = json.loads((ROOT / "tests/fixtures/canonical_vectors.json").read_text())
    return {v["name"]: v["input"] for v in vectors["match"] if v["name"].startswith("look-")}


def test_the_shared_vector_blocks_are_valid_1_1_looks():
    blocks = _vector_blocks()
    assert set(blocks) == {"look-character-1.1", "look-location-1.1"}
    for block in blocks.values():
        assert validate_look_spec(block) is block


def test_1_1_accepts_a_writeros_dependency_reference():
    validate_look_spec(character_look(version="1.1", depends_on=[WRITEROS_REF]))
    validate_look_spec(location_look(version="1.1", depends_on=[WRITEROS_REF, {"id": "wf-0badc0de"}]))


@pytest.mark.parametrize("bad", [
    {"writeros_record_id": "mem_short", "content_hash": "ab" * 32},
    {"writeros_record_id": "mem_" + "0" * 32, "content_hash": "AB" * 32},
    dict(WRITEROS_REF, extra=1),
    {"writeros_record_id": "mem_" + "0" * 32},
])
def test_malformed_writeros_references_are_refused(bad):
    with pytest.raises(LookSpecError):
        validate_look_spec(character_look(version="1.1", depends_on=[bad]))


def test_unknown_versions_are_refused():
    with pytest.raises(LookSpecError):
        validate_look_spec(character_look(version="2.0"))


def test_prompt_builder_renders_1_1_exactly_as_1_0():
    for make, role in ((character_look, "hero"), (location_look, "establishing")):
        old = pb.build_prompt(make(), role=role)
        new = pb.build_prompt(make(version="1.1", depends_on=[WRITEROS_REF]), role=role)
        assert new["prompt"] == old["prompt"]
        assert new["prompt_recipe"]["rendered_sha256"] == old["prompt_recipe"]["rendered_sha256"]
        assert new["prompt_recipe"]["builder_version"] == old["prompt_recipe"]["builder_version"]
        assert new["prompt_recipe"]["look_hash"] == look_hash(make(version="1.1", depends_on=[WRITEROS_REF]))
    with pytest.raises(pb.PromptBuildError):
        pb.build_prompt(character_look(version="2.0"), role="hero")


def _entry(**source):
    look = character_look(version="1.1", depends_on=[WRITEROS_REF])
    return {"entity_kind": "character", "entity_id": look["entity_id"], "look_spec": look,
            "look_hash": look_hash(look), "receipt_id": "r-1", **source}


WRITEROS_SOURCE = {"source_ref": {"system": "writeros", "record_id": "mem_" + "1" * 32,
                                  "memory_revision": 7, "reference": "generated-elsewhere"}}


def test_look_packet_accepts_either_source_but_exactly_one():
    from schemas.artifacts import validate_artifact

    validate_artifact("look_packet", {"version": "1.0", "looks": [_entry(**WRITEROS_SOURCE)]})
    validate_artifact("look_packet", {"version": "1.0", "looks": [_entry(source_ticket_ref={"id": "wf-0badc0de"})]})
    for bad in (
        _entry(),
        _entry(source_ticket_ref={"id": "wf-0badc0de"}, **WRITEROS_SOURCE),
        _entry(source_ref=dict(WRITEROS_SOURCE["source_ref"], system="wayfinder")),
        _entry(source_ref=dict(WRITEROS_SOURCE["source_ref"], reference="photo")),
        _entry(source_ref={k: v for k, v in WRITEROS_SOURCE["source_ref"].items() if k != "reference"}),
    ):
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("look_packet", {"version": "1.0", "looks": [bad]})
