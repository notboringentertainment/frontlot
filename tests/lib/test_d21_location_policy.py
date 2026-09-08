"""D21 step 1 — the LOCATION QC policy bundle.

Its own checklists, its own system prompt, its own local size rules and its
own bundle hash, alongside the frozen sheet and hero bundles. Invented ids
only (``loc-alpha``); every image is synthetic.
"""
from __future__ import annotations

import hashlib
import io

import jsonschema
import pytest
from PIL import Image

from lib import look_spec
from lib.sheet_qc import local_checks, policy
from schemas.artifacts import validate_artifact
from tests.lib.look_lock_helpers import location_look
from tools import prompt_builder

SHA = "a" * 64
SHB = "b" * 64


def _png(w: int, h: int, mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h), (10, 20, 30) if mode == "RGB" else (10, 20, 30, 255)).save(buf, format="PNG")
    return buf.getvalue()


# ---- checklists ----

class TestLocationChecklists:
    def test_roles_and_membership(self):
        assert policy.LOCATION_ROLES == ("establishing", "angle")
        assert policy.is_location_role("establishing") and policy.is_location_role("angle")
        assert not policy.is_location_role("hero")
        assert not policy.is_hero_role("establishing")

    def test_establishing_items(self):
        ids = [i.id for i in policy.checklist("establishing")]
        assert ids == ["architecture_matches", "view_matches", "time_of_day_matches", "palette_anchors_present",
                       "no_people", "no_text", "no_watermark_or_border", "single_place", "render_quality"]
        ev = {i.id: i.evidence for i in policy.checklist("establishing")}
        assert ev["architecture_matches"] == "look_architecture"
        assert ev["view_matches"] == "look_establishing"
        assert ev["time_of_day_matches"] == "look_time_of_day"
        assert ev["palette_anchors_present"] == "look_palette"
        assert "establishing_image" not in ev.values()

    def test_angle_swaps_the_view_item_for_place_identity(self):
        ids = [i.id for i in policy.checklist("angle")]
        assert ids == ["same_place", "architecture_matches", "time_of_day_matches", "palette_anchors_present",
                       "no_people", "no_text", "no_watermark_or_border", "single_place", "render_quality"]
        same = next(i for i in policy.checklist("angle") if i.id == "same_place")
        assert same.evidence == "establishing_image" and same.severity == "fail"
        assert "view_matches" not in ids

    def test_severities_and_known_evidence_kinds(self):
        for role in policy.LOCATION_ROLES:
            items = policy.checklist(role)
            assert all(i.evidence in policy.EVIDENCE_KINDS for i in items)
            assert all(i.severity in ("fail", "warn") for i in items)
            sev = {i.id: i.severity for i in items}
            assert sev["palette_anchors_present"] == "warn"
            assert sev["no_people"] == sev["no_text"] == sev["single_place"] == "fail"

    def test_unknown_role_still_refused(self):
        with pytest.raises(KeyError):
            policy.checklist("detail")


# ---- system prompt routing ----

class TestSystemPromptRouting:
    def test_sheet_and_hero_keep_the_identical_prompt(self):
        for role in policy.SHEET_ROLES + (policy.HERO_ROLE,):
            assert policy.system_prompt_for(role) is policy.SYSTEM_PROMPT

    def test_both_location_roles_get_the_location_prompt(self):
        for role in policy.LOCATION_ROLES:
            assert policy.system_prompt_for(role) is policy.LOCATION_SYSTEM_PROMPT
        assert policy.LOCATION_SYSTEM_PROMPT != policy.SYSTEM_PROMPT
        assert "location" in policy.LOCATION_SYSTEM_PROMPT
        assert "character reference sheets" not in policy.LOCATION_SYSTEM_PROMPT


# ---- bundle ----

class TestLocationBundle:
    def test_bundle_is_location_only(self):
        b = policy.location_bundle()
        assert list(b["checklists"]) == ["establishing", "angle"]
        assert list(b["response_schemas"]) == ["establishing", "angle"]
        assert b["system_prompt"] == policy.LOCATION_SYSTEM_PROMPT
        assert b["local_rules"] is policy.LOCATION_LOCAL_RULES
        assert b["policy_version"] == policy.QC_POLICY_VERSION

    def test_role_routing_picks_the_location_hash(self):
        loc = policy.location_bundle_sha256()
        assert policy.bundle_sha256_for_role("establishing") == loc
        assert policy.bundle_sha256_for_role("angle") == loc
        assert loc != policy.bundle_sha256() and loc != policy.hero_bundle_sha256()

    def test_response_schema_enumerates_the_role_ids(self):
        for role in policy.LOCATION_ROLES:
            enum = policy.response_schema(role)["properties"]["items"]["items"]["properties"]["id"]["enum"]
            assert enum == [i.id for i in policy.checklist(role)]

    def test_existing_bundles_carry_no_location_role(self):
        assert not set(policy.LOCATION_ROLES) & set(policy.bundle()["checklists"])
        assert not set(policy.LOCATION_ROLES) & set(policy.hero_bundle()["checklists"])
        assert not set(policy.LOCATION_ROLES) & set(policy.LOCAL_RULES["size"])
        assert not set(policy.LOCATION_ROLES) & set(policy.HERO_LOCAL_RULES["size"])


# ---- local rules ----

class TestLocationLocalRules:
    def test_landscape_and_square_pass_portrait_fails(self):
        for role in policy.LOCATION_ROLES:
            assert local_checks.check_image(role, _png(1536, 1024)) == {"width": 1536, "height": 1024}
            assert local_checks.check_image(role, _png(1024, 1024))["height"] == 1024
            with pytest.raises(local_checks.LocalCheckError) as exc:
                local_checks.check_image(role, _png(1024, 1536))
            assert exc.value.item == "orientation"
            assert "portrait" in str(exc.value)

    def test_small_and_alpha_still_refused(self):
        with pytest.raises(local_checks.LocalCheckError) as exc:
            local_checks.check_image("establishing", _png(800, 600))
        assert exc.value.item == "size"
        with pytest.raises(local_checks.LocalCheckError) as exc:
            local_checks.check_image("angle", _png(1536, 1024, "RGBA"))
        assert exc.value.item == "alpha"

    def test_unknown_orientation_token_is_rejected_not_ignored(self, monkeypatch):
        monkeypatch.setitem(policy.LOCATION_LOCAL_RULES["size"], "angle",
                            {"orientation": "diagonal", "min_long_edge": 1024})
        with pytest.raises(local_checks.LocalCheckError) as exc:
            local_checks.check_image("angle", _png(1536, 1024))
        assert exc.value.item == "orientation"
        assert "diagonal" in str(exc.value)

    def test_sheet_and_hero_rules_unchanged(self):
        assert local_checks.check_image("hero", _png(1024, 1024))["width"] == 1024
        with pytest.raises(local_checks.LocalCheckError) as exc:
            local_checks.check_image("hero", _png(1536, 1024))
        assert exc.value.item == "orientation"
        assert local_checks.check_image("turnaround", _png(2560, 1600))["width"] == 2560


# ---- evidence + judge prompt ----

class TestLocationEvidenceAndPrompt:
    def test_evidence_reads_signed_look_fields_only(self):
        look = location_look("loc-alpha")
        ev = policy.look_evidence("establishing", dict(look, ignored="caller text"))
        assert ev == {
            "look_architecture": "ribbed steel corridors",
            "look_establishing": "wide down the main corridor toward the airlock",
            "look_time_of_day": "night",
            "look_palette": "sodium amber, frost white, gunmetal",
        }
        angle_ev = policy.look_evidence("angle", look)
        assert "look_establishing" not in angle_ev
        assert angle_ev["look_architecture"] == "ribbed steel corridors"

    def test_missing_fields_become_empty_strings(self):
        ev = policy.look_evidence("angle", {})
        assert ev == {"look_architecture": "", "look_time_of_day": "", "look_palette": ""}

    def test_establishing_prompt_judges_the_candidate_alone(self):
        ev = policy.look_evidence("establishing", location_look("loc-alpha"))
        prompt = policy.judge_prompt("establishing", look_fields=ev)
        lines = prompt.splitlines()
        assert lines[0] == "Location role: establishing."
        assert "Image 2" not in prompt
        assert "Described architecture or terrain: ribbed steel corridors." in prompt
        assert "Described establishing view: wide down the main corridor toward the airlock." in prompt
        assert "Described time of day: night." in prompt
        assert "Described palette anchors: sodium amber, frost white, gunmetal." in prompt
        for item in policy.checklist("establishing"):
            assert f"- {item.id}: {item.question}" in prompt

    def test_angle_prompt_names_the_establishing_plate(self):
        ev = policy.look_evidence("angle", location_look("loc-alpha"))
        prompt = policy.judge_prompt("angle", look_fields=ev)
        assert prompt.splitlines()[0] == "Location role: angle."
        assert "Image 1 is the candidate, Image 2 is the approved establishing plate of the same place." in prompt
        assert "same_place" in prompt

    def test_sheet_and_hero_prompts_unchanged(self):
        assert policy.judge_prompt("turnaround", wardrobe_pieces=["coat"], has_hero=True).startswith("Sheet role: turnaround.")
        hero = policy.judge_prompt("hero", wardrobe_pieces=None, has_hero=False,
                                   look_fields=policy.look_evidence("hero", {"hair": "cropped grey"}))
        assert "Described hair: cropped grey." in hero and "reference face" in hero


# ---- 600-char caps on the long free-text location fields ----

def _resolve(schema: dict, spec: dict) -> dict:
    ref = spec.get("$ref")
    return schema["$defs"][ref.rsplit("/", 1)[-1]] if ref else spec


def _schema_max_chars(schema: dict, spec: dict) -> int | None:
    """The longest judge-ready text the schema permits for one location field,
    or None if the schema leaves it unbounded. Lists are joined with ", ", the
    way ``policy.look_evidence`` joins them."""
    spec = _resolve(schema, spec)
    if spec.get("type") == "array":
        items, n = _resolve(schema, spec["items"]), spec.get("maxItems")
        per = items.get("maxLength")
        return None if n is None or per is None else n * per + 2 * (n - 1)
    if "enum" in spec:
        return max(len(v) for v in spec["enum"])
    return spec.get("maxLength")

class TestLocationTextCaps:
    def test_the_caps_agree_at_both_ends(self):
        assert policy.LOCATION_TEXT_MAX_CHARS == look_spec.LOCATION_TEXT_MAX_CHARS == 600
        # both ends must bound EXACTLY the same fields, or a look that signs
        # cleanly can still crash the judge
        assert policy.LOCATION_CAPPED_FIELDS == look_spec.LOCATION_CAPPED_FIELDS
        assert look_spec.LOCATION_CAPPED_FIELDS == ("architecture_or_terrain", "weather_or_light_rules")
        # the prompt builder renders the same two fields; its cap must not be looser
        assert prompt_builder.MAX_FIELD_CHARS <= look_spec.LOCATION_TEXT_MAX_CHARS

    def test_signing_refuses_an_over_cap_field(self):
        for field in look_spec.LOCATION_CAPPED_FIELDS:
            over = location_look("loc-alpha", **{field: "granite " * 100})
            with pytest.raises(look_spec.LookSpecError):
                look_spec.validate_look_spec(over)
            # the cap itself, independent of the schema's own (stricter) line cap
            with pytest.raises(look_spec.LookSpecError, match=field):
                look_spec.check_location_text_caps(
                    dict(over, **{field: "z" * (look_spec.LOCATION_TEXT_MAX_CHARS + 1)}))
            look_spec.check_location_text_caps(
                dict(over, **{field: "z" * look_spec.LOCATION_TEXT_MAX_CHARS}))

    def test_every_capped_evidence_field_is_bounded_at_signing(self):
        """Every field the evidence end caps must also be capped at signing, and
        the schema's own bound must not sit above the cap."""
        schema = look_spec.load_look_spec_schema()
        props = schema["$defs"]["location"]["properties"]
        capped = set(policy.LOCATION_CAPPED_FIELDS)
        assert capped <= set(look_spec.LOCATION_CAPPED_FIELDS)
        # every field the evidence builder reads is bounded by the schema, so
        # nothing reaches the judge unbounded whether or not it is capped
        for field in policy.LOCATION_LOOK_EVIDENCE_FIELDS.values():
            assert _schema_max_chars(schema, props[field]) is not None, field
        for field in capped:
            ref = props[field]["$ref"].rsplit("/", 1)[-1]
            assert schema["$defs"][ref]["maxLength"] <= look_spec.LOCATION_TEXT_MAX_CHARS

    def test_signing_accepts_a_look_within_the_caps(self):
        assert look_spec.validate_look_spec(location_look("loc-alpha"))

    def test_a_maximal_schema_valid_look_signs_and_judges(self):
        """A look the schema calls valid at every bound must sign AND build
        evidence — joined palette anchors run past the cap and must not be
        capped, because signing never bounds them."""
        schema = look_spec.load_look_spec_schema()
        defs = schema["$defs"]
        maximal = location_look(
            "loc-alpha",
            establishing_view="e" * defs["line"]["maxLength"],
            architecture_or_terrain="a" * defs["line"]["maxLength"],
            weather_or_light_rules="w" * defs["line"]["maxLength"],
            dressing=["d" * defs["line"]["maxLength"]] * defs["location"]["properties"]["dressing"]["maxItems"],
            palette_anchors=[c * defs["short_text"]["maxLength"] for c in "pqrs"],
        )
        assert look_spec.validate_look_spec(maximal)
        for role in policy.LOCATION_ROLES:
            ev = policy.look_evidence(role, maximal)
            assert len(ev["look_palette"]) > policy.LOCATION_TEXT_MAX_CHARS
            assert policy.judge_prompt(role, look_fields=ev)

    def test_evidence_refuses_an_over_cap_field(self):
        over = "x" * (policy.LOCATION_TEXT_MAX_CHARS + 1)
        look = dict(location_look("loc-alpha"), architecture_or_terrain=over)
        with pytest.raises(ValueError, match="architecture_or_terrain"):
            policy.look_evidence("establishing", look)
        at_cap = dict(location_look("loc-alpha"), architecture_or_terrain="y" * policy.LOCATION_TEXT_MAX_CHARS)
        assert len(policy.look_evidence("establishing", at_cap)["look_architecture"]) == policy.LOCATION_TEXT_MAX_CHARS


# ---- qc_verdict schema round trips ----

def _verdict(role: str, **over) -> dict:
    v = {
        "version": "1.0", "project_id": "p", "entity_kind": "location", "entity_id": "loc-alpha",
        "role": role, "asset_id": SHA, "look_hash": SHB, "look_receipt_id": "look-1",
        "headshot_asset_id": None, "headshot_receipt_id": None,
        "policy_version": policy.QC_POLICY_VERSION, "policy_bundle_sha256": policy.location_bundle_sha256(),
        "provider": "openai", "model": "judge-x", "prompt_sha256": "c" * 64, "tuple_sha256": "d" * 64,
        "attempt_id": "att-1", "attempt_n": 1, "items": [], "verdict": "pass", "failing_items": [],
        "warnings": [], "cost_usd": 0.01, "judged_at": "2026-09-01T00:00:00+00:00",
        "provider_request_id": "req-1", "provider_model_version": "judge-x-2026", "raw_response_asset_id": "e" * 64,
    }
    if role == "angle":
        # step 2: an angle verdict names the establishing plate it was judged beside
        v["establishing_asset_id"] = "f" * 64
    v.update(over)
    return v


def _answers(role: str, no: str | None = None) -> list[dict]:
    return [{"id": i.id, "answer": "no" if i.id == no else "yes", "note": "", "severity": i.severity}
            for i in policy.checklist(role)]


class TestVerdictSchemaAcceptsLocationRoles:
    def test_role_enum_carries_both_location_roles(self):
        validate_artifact("qc_verdict", _verdict("establishing", items=_answers("establishing")))
        validate_artifact("qc_verdict", _verdict("angle", items=_answers("angle")))
        with pytest.raises(jsonschema.ValidationError, match="detail"):
            validate_artifact("qc_verdict", _verdict("detail"))

    def test_local_fail_round_trips(self):
        v = _verdict("establishing", provider="local", model="local_checks",
                     prompt_sha256=hashlib.sha256(b"rules").hexdigest(),
                     provider_request_id=None, provider_model_version=None, raw_response_asset_id=None,
                     items=[{"id": "orientation", "answer": "no", "note": "portrait", "severity": "fail"}],
                     verdict="fail", failing_items=["orientation"], cost_usd=0.0)
        validate_artifact("qc_verdict", v)

    def test_remote_pass_and_fail_round_trip(self):
        validate_artifact("qc_verdict", _verdict("establishing", items=_answers("establishing")))
        validate_artifact("qc_verdict", _verdict("angle", items=_answers("angle", no="same_place"),
                                                 verdict="fail", failing_items=["same_place"]))

    def test_reused_verdict_round_trips(self):
        # a reused verdict is the same sealed record read back and re-attached
        first = _verdict("angle", items=_answers("angle"))
        validate_artifact("qc_verdict", first)
        validate_artifact("qc_verdict", dict(first))

    def test_remote_verdict_still_requires_provider_receipts(self):
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("qc_verdict", _verdict("angle", raw_response_asset_id=None))
