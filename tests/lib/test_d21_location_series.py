"""D21 step 2 — QC series, verdict tuple and attempt budget for LOCATIONS
(import path only).

The series schema is versioned and entity-kind aware: a location series names
no headshot, binds the signed look receipt, and — for an ``angle`` — binds the
approved establishing plate it is judged beside, so an angle judged next to
plate A can never be reused next to plate B. Character series and character
verdict tuples are byte-frozen and proven so by golden hashes.

Generated location plates (and their ``references_applied`` anchoring proof)
land with generate mode; everything here is the import path.

Invented ids only (``loc-alpha``, ``char-one``); every image is synthetic.
"""
from __future__ import annotations

import dataclasses
import hashlib
import io

import pytest
from PIL import Image

from lib import qc_receipts as qr
from lib import receipts
from lib.canonical_json import record_sha256
from lib.look_spec import look_hash as _look_hash
from lib.project_config import QCConfig
from lib.sheet_qc import policy
from lib.sheet_qc.verify import SeriesMismatch, verify_series_against_receipt
from tests.lib import look_lock_helpers as H
from tests.lib.d19_helpers import JUDGE_MODEL, config_1_1, make_qc_project
from tests.tools.test_sheet_judge import FakeAdapter, _all
from tools.qa.sheet_judge import SheetJudge

LOC = "loc-alpha"
PLATE_A = "a" * 64
PLATE_B = "b" * 64


# ---------------------------------------------------------------- golden bytes

# The exact hashes D19/D20 signed. If either constant has to change, every
# character sheet and hero verdict ever signed has been invalidated.
CHARACTER_SERIES_KEY = {
    "project_id": "proj-one", "entity_kind": "character", "entity_id": "char-one", "role": "turnaround",
    "look_hash": "l" * 64, "headshot_receipt_id": "hs-1", "policy_bundle_sha256": "a" * 64,
    "builder_policy_sha256": "b" * 64, "generation_endpoint": "vendor/edit", "generation_model": "vendor/edit",
    "judge_provider": "openai", "judge_model": "judge-x",
}
CHARACTER_SERIES_SHA = "ebdd20788d8481679be004e64440610cc7d7267ac5ec239235cbbc2bd14a2eb4"
HERO_SERIES_SHA = "f361ee9c564ec51c98577c90a96a7d4b3fcbe539e062abcc57d6200ddb11a5db"
HERO_GRANDFATHER_SERIES_SHA = "8523ead0e2fd6d1775d4d5225ee56eaa0d1626e76999cbf94e8e572ef437bc6c"

CHARACTER_TUPLE = {
    "project_id": "proj-one", "entity_kind": "character", "entity_id": "char-one", "role": "turnaround",
    "asset_id": "c" * 64, "look_hash": "l" * 64, "look_receipt_id": "lk-1", "headshot_asset_id": "d" * 64,
    "headshot_receipt_id": "hs-1", "policy_bundle_sha256": "a" * 64, "provider": "openai", "model": "judge-x",
}
CHARACTER_TUPLE_SHA = "04ec382562f8c716eaea1ad8131297fc7ad7596f47e9dc46d8a200626ecaca3a"
HERO_TUPLE_SHA = "e1186d7e94131e4f0a5ae313c88c7cc6b682332430f38cb3cd8a7fc6b7f178b1"


class TestCharacterBytesAreFrozen:
    def test_sheet_series_hash(self):
        assert qr.series_sha256(CHARACTER_SERIES_KEY) == CHARACTER_SERIES_SHA
        assert qr.series_fields(CHARACTER_SERIES_KEY) == CHARACTER_SERIES_KEY

    def test_hero_series_hash_with_and_without_the_grandfather_flag(self):
        hero = dict(CHARACTER_SERIES_KEY, role="hero", headshot_receipt_id=None)
        assert qr.series_sha256(hero) == HERO_SERIES_SHA
        assert qr.series_sha256(dict(hero, grandfather=True)) == HERO_GRANDFATHER_SERIES_SHA

    def test_no_version_key_is_hashed_into_a_character_series(self):
        assert qr.SERIES_VERSION_FIELD not in qr.series_fields(CHARACTER_SERIES_KEY)

    def test_sheet_and_hero_tuple_hashes(self):
        assert qr.tuple_sha256(CHARACTER_TUPLE) == CHARACTER_TUPLE_SHA
        hero = dict(CHARACTER_TUPLE, role="hero", headshot_asset_id=None, headshot_receipt_id=None)
        assert qr.tuple_sha256(hero) == HERO_TUPLE_SHA

    def test_a_character_tuple_ignores_a_stray_location_field(self):
        """A caller cannot smuggle the anchor into a character tuple to move its bytes."""
        assert qr.tuple_sha256(dict(CHARACTER_TUPLE, establishing_asset_id=PLATE_A)) == CHARACTER_TUPLE_SHA


# ---------------------------------------------------------------- series schema

def _series(*, role="establishing", anchor=PLATE_A, **over):
    key = {
        "project_id": "proj-one", "entity_kind": "location", "entity_id": LOC, "role": role,
        "look_hash": "l" * 64, "look_receipt_id": "lk-loc-1", "headshot_receipt_id": None,
        "policy_bundle_sha256": policy.location_bundle_sha256(),
        "builder_policy_sha256": qr.IMPORTED_SENTINEL, "generation_endpoint": qr.IMPORTED_SENTINEL,
        "generation_model": qr.IMPORTED_SENTINEL, "judge_provider": "openai", "judge_model": JUDGE_MODEL,
    }
    if role == "angle":
        key["establishing_asset_id"] = anchor
    key.update(over)
    return key


class TestLocationSeriesSchema:
    def test_establishing_series_carries_the_location_fields(self):
        fields = qr.series_fields(_series())
        assert fields["series_version"] == qr.SERIES_VERSION_LOCATION == "1.1"
        assert fields["entity_kind"] == "location" and fields["role"] == "establishing"
        assert fields["look_receipt_id"] == "lk-loc-1"
        assert fields["headshot_receipt_id"] is None
        assert "establishing_asset_id" not in fields
        assert set(fields) == set(qr.SERIES_FIELDS) | {"series_version", "look_receipt_id"}

    def test_the_version_is_part_of_the_hash(self):
        fields = qr.series_fields(_series())
        without = {k: v for k, v in fields.items() if k != "series_version"}
        assert record_sha256(without) != qr.series_sha256(_series())

    def test_the_look_receipt_is_part_of_the_hash(self):
        assert qr.series_sha256(_series()) != qr.series_sha256(_series(look_receipt_id="lk-loc-2"))

    def test_angle_series_binds_the_establishing_plate(self):
        fields = qr.series_fields(_series(role="angle"))
        assert fields["establishing_asset_id"] == PLATE_A
        assert qr.series_sha256(_series(role="angle", anchor=PLATE_A)) != \
            qr.series_sha256(_series(role="angle", anchor=PLATE_B))

    def test_angle_needs_a_real_anchor(self):
        with pytest.raises(qr.QCReceiptError, match="establishing_asset_id"):
            qr.series_fields(_series(role="angle", anchor=None))
        with pytest.raises(qr.QCReceiptError, match="establishing_asset_id"):
            qr.series_fields(_series(role="angle", anchor="not-a-sha"))

    def test_establishing_refuses_a_stray_anchor(self):
        with pytest.raises(qr.QCReceiptError, match="only an angle series"):
            qr.series_fields(dict(_series(), establishing_asset_id=PLATE_A))

    def test_location_series_names_no_headshot(self):
        with pytest.raises(qr.QCReceiptError, match="headshot"):
            qr.series_fields(_series(headshot_receipt_id="hs-1"))
        key = _series()
        key.pop("headshot_receipt_id")
        with pytest.raises(qr.QCReceiptError, match="missing"):
            qr.series_fields(key)

    def test_location_series_must_name_its_look_receipt(self):
        with pytest.raises(qr.QCReceiptError, match="look receipt"):
            qr.series_fields(_series(look_receipt_id=""))
        key = _series()
        key.pop("look_receipt_id")
        with pytest.raises(qr.QCReceiptError, match="missing"):
            qr.series_fields(key)

    def test_a_declared_version_must_be_the_real_one(self):
        assert qr.series_fields(_series(series_version="1.1"))["series_version"] == "1.1"
        with pytest.raises(qr.QCReceiptError, match="series_version"):
            qr.series_fields(_series(series_version="9.9"))

    def test_a_row_series_key_round_trips(self):
        """The hashed subset a row stores re-hashes to the same series."""
        key = _series(role="angle")
        assert qr.series_sha256(qr.series_fields(key)) == qr.series_sha256(key)


class TestRoleMatrix:
    @pytest.mark.parametrize("kind,role", [
        ("character", "establishing"), ("character", "angle"),
        ("location", "turnaround"), ("location", "expressions"),
        ("location", "wardrobe"), ("location", "hero"),
    ])
    def test_roles_are_bound_to_their_entity_kind(self, kind, role):
        key = dict(CHARACTER_SERIES_KEY, entity_kind=kind, role=role)
        with pytest.raises(qr.QCReceiptError, match="role"):
            qr.series_fields(key)

    def test_unknown_entity_kind_is_refused(self):
        with pytest.raises(qr.QCReceiptError, match="entity_kind"):
            qr.series_fields(dict(CHARACTER_SERIES_KEY, entity_kind="prop"))

    def test_unknown_role_is_refused(self):
        with pytest.raises(qr.QCReceiptError, match="role"):
            qr.series_fields(dict(CHARACTER_SERIES_KEY, role="poster"))

    def test_the_matrix_agrees_with_the_qc_policy(self):
        assert set(qr.ROLES_BY_ENTITY_KIND["character"]) == set(policy.ALL_ROLES)
        assert qr.LOCATION_ROLES == policy.LOCATION_ROLES
        assert set(qr.ROLES_BY_ENTITY_KIND) == {"character", "location"}


# ---------------------------------------------------------------- verdict tuple

def _tuple(*, role="establishing", anchor=PLATE_A, **over):
    ev = {
        "project_id": "proj-one", "entity_kind": "location", "entity_id": LOC, "role": role,
        "asset_id": "c" * 64, "look_hash": "l" * 64, "look_receipt_id": "lk-loc-1",
        "headshot_asset_id": None, "headshot_receipt_id": None,
        "policy_bundle_sha256": policy.location_bundle_sha256(), "provider": "openai", "model": JUDGE_MODEL,
    }
    if role == "angle":
        ev["establishing_asset_id"] = anchor
    ev.update(over)
    return ev


class TestLocationVerdictTuple:
    def test_the_tuple_version_is_hashed(self):
        ev = _tuple()
        plain = record_sha256({f: ev[f] for f in qr.TUPLE_FIELDS})
        assert qr.tuple_sha256(ev) != plain
        assert qr.tuple_version_for("location", "establishing") == qr.TUPLE_VERSION_LOCATION == "1.1"
        assert qr.tuple_version_for("character", "turnaround") == qr.TUPLE_VERSION_CHARACTER == "1.0"

    def test_an_angle_judged_beside_another_plate_is_a_different_evaluation(self):
        """R2#4: reuse can never cross the anchor — plate A and plate B are two evaluations."""
        assert qr.tuple_sha256(_tuple(role="angle", anchor=PLATE_A)) != \
            qr.tuple_sha256(_tuple(role="angle", anchor=PLATE_B))

    def test_an_angle_tuple_requires_the_anchor(self):
        ev = _tuple(role="angle")
        ev.pop("establishing_asset_id")
        with pytest.raises(qr.QCReceiptError, match="establishing_asset_id"):
            qr.tuple_sha256(ev)
        with pytest.raises(qr.QCReceiptError, match="establishing_asset_id"):
            qr.tuple_sha256(_tuple(role="angle", anchor=None))

    def test_an_establishing_tuple_carries_no_anchor(self):
        with pytest.raises(qr.QCReceiptError, match="only an angle"):
            qr.tuple_sha256(dict(_tuple(), establishing_asset_id=PLATE_A))

    def test_a_location_verdict_names_no_headshot(self):
        with pytest.raises(qr.QCReceiptError, match="headshot"):
            qr.tuple_sha256(_tuple(headshot_asset_id="d" * 64))
        with pytest.raises(qr.QCReceiptError, match="headshot"):
            qr.tuple_sha256(_tuple(headshot_receipt_id="hs-1"))

    def test_the_role_matrix_applies_to_tuples_too(self):
        with pytest.raises(qr.QCReceiptError, match="role"):
            qr.tuple_sha256(_tuple(entity_kind="character"))


class TestVerdictArtifactAnchor:
    """The signed artifact carries the anchor exactly where the tuple hashes it."""

    def _artifact(self, role, **over):
        v = {"version": "1.0", "project_id": "proj-one", "entity_kind": "location", "entity_id": LOC,
             "role": role, "asset_id": "c" * 64, "look_hash": "l" * 64, "look_receipt_id": "lk-loc-1",
             "headshot_asset_id": None, "headshot_receipt_id": None, "policy_version": policy.QC_POLICY_VERSION,
             "policy_bundle_sha256": policy.location_bundle_sha256(), "provider": "openai", "model": JUDGE_MODEL,
             "prompt_sha256": "e" * 64, "tuple_sha256": "d" * 64, "attempt_id": "att-1", "attempt_n": 1,
             "items": [], "verdict": "pass", "failing_items": [], "warnings": [], "cost_usd": 0.01,
             "judged_at": "2026-09-01T00:00:00+00:00", "provider_request_id": "req-1",
             "provider_model_version": "judge-x-2026", "raw_response_asset_id": "f" * 64}
        if role == "angle":
            v["establishing_asset_id"] = PLATE_A
        v.update(over)
        return v

    def test_an_angle_verdict_must_name_its_anchor(self):
        import jsonschema
        from schemas.artifacts import validate_artifact

        validate_artifact("qc_verdict", self._artifact("angle"))
        bare = self._artifact("angle")
        bare.pop("establishing_asset_id")
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("qc_verdict", bare)
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("qc_verdict", self._artifact("angle", establishing_asset_id=None))

    def test_every_other_role_carries_no_anchor(self):
        import jsonschema
        from schemas.artifacts import validate_artifact

        validate_artifact("qc_verdict", self._artifact("establishing"))
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("qc_verdict", self._artifact("establishing", establishing_asset_id=PLATE_A))
        sheet = self._artifact("turnaround", entity_kind="character", entity_id="char-one",
                               headshot_asset_id="d" * 64, headshot_receipt_id="hs-1",
                               establishing_asset_id=PLATE_A)
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("qc_verdict", sheet)


# ---------------------------------------------------------------- budget

@pytest.fixture(autouse=True)
def gates_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(tmp_path / "gates"))


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj-one"
    p.mkdir()
    return p


class TestLocationBudget:
    def test_budget_key_shape(self):
        assert qr.location_budget_key("proj-one", LOC, "l" * 64) == {
            "project_id": "proj-one", "entity_id": LOC, "look_hash": "l" * 64}

    def test_one_cap_per_entity_per_look_across_every_slot(self, project):
        """D2: the establishing slot and every angle slot draw on ONE allowance."""
        cap = 3
        budget = qr.location_budget_key("proj-one", LOC, "l" * 64)
        a1 = qr.start_attempt(project, _series(), max_attempts=cap, budget_key=budget, budget_cap=cap)
        assert a1["budget_n"] == 1 and a1["attempt_n"] == 1 and a1["budget_cap"] == cap
        qr.attach_verdict(project, a1["attempt_id"], qc_receipt_id="v-1")
        # a different SLOT is a different series but the same allowance
        a2 = qr.start_attempt(project, _series(role="angle"), max_attempts=cap, budget_key=budget, budget_cap=cap)
        assert a2["budget_n"] == 2 and a2["attempt_n"] == 1
        qr.attach_verdict(project, a2["attempt_id"], qc_receipt_id="v-2")
        # a different anchor is a different series but STILL the same allowance
        a3 = qr.start_attempt(project, _series(role="angle", anchor=PLATE_B), max_attempts=cap,
                              budget_key=budget, budget_cap=cap)
        assert a3["budget_n"] == 3 and a3["attempt_n"] == 1
        qr.attach_verdict(project, a3["attempt_id"], qc_receipt_id="v-3")
        with pytest.raises(qr.AttemptCapExceeded, match="location budget"):
            qr.start_attempt(project, _series(), max_attempts=cap, budget_key=budget, budget_cap=cap)
        assert len(qr.location_attempts_started(project, LOC, "l" * 64)) == 3

    def test_another_look_has_its_own_allowance(self, project):
        cap = 1
        qr.start_attempt(project, _series(), max_attempts=cap,
                         budget_key=qr.location_budget_key("proj-one", LOC, "l" * 64), budget_cap=cap)
        other = _series(look_hash="m" * 64)
        a = qr.start_attempt(project, other, max_attempts=cap,
                             budget_key=qr.location_budget_key("proj-one", LOC, "m" * 64), budget_cap=cap)
        assert a["budget_n"] == 1

    def test_a_location_attempt_must_name_its_own_budget(self, project):
        with pytest.raises(qr.QCReceiptError, match="budget_key and budget_cap"):
            qr.start_attempt(project, _series(), max_attempts=3)
        with pytest.raises(qr.QCReceiptError, match="is not the series' own"):
            qr.start_attempt(project, _series(), max_attempts=3,
                             budget_key=qr.location_budget_key("proj-one", "loc-beta", "l" * 64), budget_cap=3)
        with pytest.raises(qr.QCReceiptError, match="is not the series' own"):
            qr.start_attempt(project, _series(), max_attempts=3,
                             budget_key={"project_id": "proj-one", "entity_id": LOC}, budget_cap=3)

    def test_hero_attempts_are_not_counted_as_location_attempts(self, project):
        hero = dict(CHARACTER_SERIES_KEY, project_id="proj-one", role="hero", headshot_receipt_id=None,
                    entity_id=LOC, look_hash="l" * 64)
        cap = 2
        qr.start_attempt(project, hero, max_attempts=cap,
                         budget_key=qr.hero_budget_key("proj-one", LOC, "l" * 64), budget_cap=cap)
        assert qr.location_attempts_started(project, LOC, "l" * 64) == []

    def test_a_sheet_attempt_still_refuses_a_budget_key(self, project):
        with pytest.raises(qr.QCReceiptError, match="only hero and location"):
            qr.start_attempt(project, dict(CHARACTER_SERIES_KEY, project_id="proj-one"), max_attempts=3,
                             budget_key=qr.location_budget_key("proj-one", "char-one", "l" * 64), budget_cap=3)

    def test_the_per_series_cap_still_applies(self, project):
        budget = qr.location_budget_key("proj-one", LOC, "l" * 64)
        a = qr.start_attempt(project, _series(), max_attempts=1, budget_key=budget, budget_cap=9)
        qr.attach_verdict(project, a["attempt_id"], qc_receipt_id="v-1")
        with pytest.raises(qr.AttemptCapExceeded, match="cap is 1"):
            qr.start_attempt(project, _series(), max_attempts=1, budget_key=budget, budget_cap=9)


# ---------------------------------------------------------------- verifier

def _imported_receipt(**over):
    r = {"generator_kind": "imported", "origin_tool": "midjourney", "attestation_receipt_id": "att-1",
         "prompt_recipe": None, "headshot_ref": None, "model_endpoint": None,
         "look_refs": [{"entity_kind": "location", "entity_id": LOC, "look_hash": "l" * 64}],
         "output_sha256": "c" * 64}
    r.update(over)
    return r


ENTRY = {"id": LOC, "establishing": {"asset_id": PLATE_A}, "angles": [{"asset_id": "c" * 64}]}


class TestLocationSeriesVerifier:
    def test_imported_establishing_series_verifies(self):
        verify_series_against_receipt(_series(), _imported_receipt(), asset_id="c" * 64)

    def test_imported_angle_series_verifies_against_the_entrys_establishing_slot(self):
        verify_series_against_receipt(_series(role="angle", anchor=PLATE_A), _imported_receipt(), entry=ENTRY)

    def test_an_angle_anchored_to_another_plate_is_refused(self):
        with pytest.raises(SeriesMismatch, match="establishing slot"):
            verify_series_against_receipt(_series(role="angle", anchor=PLATE_B), _imported_receipt(), entry=ENTRY)

    def test_an_angle_series_must_carry_an_anchor(self):
        key = _series(role="angle")
        key.pop("establishing_asset_id")
        with pytest.raises(SeriesMismatch, match="establishing plate"):
            verify_series_against_receipt(key, _imported_receipt(), entry=ENTRY)

    def test_a_generated_location_plate_names_the_deferral(self):
        gen = _imported_receipt(generator_kind="model", model_endpoint="vendor/edit", origin_tool=None,
                                attestation_receipt_id=None)
        with pytest.raises(SeriesMismatch, match="generate mode"):
            verify_series_against_receipt(_series(), gen)

    def test_the_imported_sentinels_are_required(self):
        for field in ("builder_policy_sha256", "generation_endpoint", "generation_model"):
            with pytest.raises(SeriesMismatch, match="sentinel"):
                verify_series_against_receipt(_series(**{field: "z" * 64}), _imported_receipt())

    def test_a_location_series_carries_no_headshot(self):
        with pytest.raises(SeriesMismatch, match="headshot"):
            verify_series_against_receipt(dict(_series(), headshot_receipt_id="hs-1"), _imported_receipt())
        with pytest.raises(SeriesMismatch, match="headshot_ref"):
            verify_series_against_receipt(_series(), _imported_receipt(
                headshot_ref={"entity_id": LOC, "asset_id": "d" * 64, "approval_receipt_id": "hs-1"}))

    def test_an_imported_receipt_carries_no_prompt_recipe(self):
        with pytest.raises(SeriesMismatch, match="prompt_recipe"):
            verify_series_against_receipt(_series(), _imported_receipt(prompt_recipe={"look_hash": "l" * 64}))

    def test_contradicting_look_refs_are_refused(self):
        gen = _imported_receipt(look_refs=[{"entity_kind": "location", "entity_id": "loc-beta", "look_hash": "l" * 64}])
        with pytest.raises(SeriesMismatch, match="look_refs"):
            verify_series_against_receipt(_series(), gen)

    def test_the_series_must_name_its_look_receipt(self):
        with pytest.raises(SeriesMismatch, match="look receipt"):
            verify_series_against_receipt(_series(look_receipt_id=""), _imported_receipt())

    def test_the_receipt_must_be_for_the_judged_asset(self):
        with pytest.raises(SeriesMismatch, match="another asset"):
            verify_series_against_receipt(_series(), _imported_receipt(), asset_id="d" * 64)

    def test_character_series_verification_is_unchanged(self):
        """The sheet path still refuses an imported receipt with its own message."""
        sheet = dict(CHARACTER_SERIES_KEY)
        with pytest.raises(SeriesMismatch, match="imported"):
            verify_series_against_receipt(sheet, _imported_receipt())


# ---------------------------------------------------------------- judge branch

@dataclasses.dataclass(frozen=True)
class _QC13(QCConfig):
    """Stand-in for the 1.3 config Step 3 signs: the two location fields the
    judge reads through its accessor seam."""
    location_policy_sha256: str | None = None
    max_location_attempts: int | None = None


def _png(w, h, seed):
    d = hashlib.sha256(seed.encode()).digest()
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (d[0], d[1], d[2])).save(buf, "PNG")
    return buf.getvalue()


def _location_config():
    """A 1.2 config whose SHEET bundle field is set to the location bundle hash.

    ``qc_call_context`` compares an attempt series against
    ``qc.policy_bundle_sha256`` for every non-hero role; Step 3 gives location
    roles their own comparison against ``qc.location_policy_sha256``. Until
    then this is how a location series gets through the shared boundary — the
    judge itself still reads the location hash through the Step 3 seam.
    """
    cfg = config_1_1(max_attempts=3)
    cfg["qc"]["policy_bundle_sha256"] = policy.location_bundle_sha256()
    return cfg


def _patch_location_config(monkeypatch, *, location_hash=None, cap=3):
    from tools.video import _shared

    real = _shared.qc_call_context

    def wrapper(inputs, **kw):
        root, tracker, config, qc, started = real(inputs, **kw)
        shim = _QC13(**dataclasses.asdict(qc),
                     location_policy_sha256=location_hash or policy.location_bundle_sha256(),
                     max_location_attempts=cap)
        return root, tracker, config, shim, started

    monkeypatch.setattr(_shared, "qc_call_context", wrapper)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A registered project with a ratified location look and two imported
    plates (an approved establishing plate and an angle candidate)."""
    monkeypatch.setenv("OPENMONTAGE_TEST_MODE", "1")
    project = make_qc_project(tmp_path, monkeypatch, slug=H.PROJECT, config=_location_config())
    look = H.location_look(LOC)
    look_receipt = H.activate_look(project, look)
    plates = {}
    for name, seed in (("establishing", "plate-est"), ("angle", "plate-ang")):
        data = _png(1600, 1024, seed)
        sha = hashlib.sha256(data).hexdigest()
        (project / "canon/visual/objects" / f"{sha}.png").write_bytes(data)
        row = receipts.record_generation(
            project, execution_id=f"exec-{seed}", tool="reference_import", normalized_inputs_hash="a" * 64,
            output_sha256=sha, cost_usd=0.0, started_at="t", finished_at="t", generator_kind="imported",
            origin_tool="midjourney", attestation_receipt_id=f"att-{seed}",
            look_refs=H.look_refs_for(look), import_receipt_id=f"imp-{seed}", normalized_pixel_hash=sha)
        plates[name] = {"asset_id": sha, "gen": row}
    return {"project": project, "look": look, "look_receipt": look_receipt, "plates": plates}


def _key(w, *, role="establishing", anchor=None):
    key = {"entity_kind": "location", "entity_id": LOC, "role": role, "look_hash": _look_hash(w["look"]),
           "look_receipt_id": w["look_receipt"]["receipt_id"], "headshot_receipt_id": None,
           "policy_bundle_sha256": policy.location_bundle_sha256(),
           "builder_policy_sha256": qr.IMPORTED_SENTINEL, "generation_endpoint": qr.IMPORTED_SENTINEL,
           "generation_model": qr.IMPORTED_SENTINEL, "judge_provider": "openai", "judge_model": JUDGE_MODEL}
    if role == "angle":
        key["establishing_asset_id"] = anchor or w["plates"]["establishing"]["asset_id"]
    return key


def _attempt(w, key, slot):
    budget = qr.location_budget_key(H.PROJECT, LOC, _look_hash(w["look"]))
    a = qr.start_attempt(w["project"], key, max_attempts=3, budget_key=budget, budget_cap=3)
    qr.attach_generation(w["project"], a["attempt_id"],
                         generation_receipt_id=w["plates"][slot]["gen"]["receipt_id"],
                         asset_id=w["plates"][slot]["asset_id"])
    return a


class TestJudgeLocationBranch:
    def test_establishing_plate_is_judged_alone_under_the_location_policy(self, world, monkeypatch):
        w = world
        _patch_location_config(monkeypatch)
        a = _attempt(w, _key(w), "establishing")
        fake = FakeAdapter(_all("establishing"))
        r = SheetJudge(adapter=fake).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"],
                                              "asset_id": w["plates"]["establishing"]["asset_id"]})
        assert r.success, r.error
        assert r.data["verdict"] == "pass"
        call = fake.calls[0]
        assert call["n_images"] == 1
        assert call["user"].startswith("Location role: establishing.")
        assert "Described time of day: night." in call["user"]
        assert "Described architecture or terrain: ribbed steel corridors." in call["user"]
        row = qr.find_verdict_by_id(w["project"], r.data["qc_receipt_id"])
        assert row["entity_kind"] == "location" and row["role"] == "establishing"
        assert row["policy_bundle_sha256"] == policy.location_bundle_sha256()
        assert row["headshot_asset_id"] is None and row["headshot_receipt_id"] is None
        assert row["tuple_sha256"] == qr.tuple_sha256(row)

    def test_the_location_system_prompt_is_used(self, world, monkeypatch):
        w = world
        _patch_location_config(monkeypatch)
        a = _attempt(w, _key(w), "establishing")
        seen = {}

        class _Capturing(FakeAdapter):
            def submit(self, *, model, system, user, images, schema):
                seen["system"] = system
                return super().submit(model=model, system=system, user=user, images=images, schema=schema)

        r = SheetJudge(adapter=_Capturing(_all("establishing"))).execute(
            {"project_dir": str(w["project"]), "attempt_id": a["attempt_id"],
             "asset_id": w["plates"]["establishing"]["asset_id"]})
        assert r.success, r.error
        assert seen["system"] == policy.LOCATION_SYSTEM_PROMPT
        assert seen["system"] != policy.SYSTEM_PROMPT

    def test_an_angle_is_judged_beside_the_establishing_plate(self, world, monkeypatch):
        w = world
        _patch_location_config(monkeypatch)
        anchor = w["plates"]["establishing"]["asset_id"]
        a = _attempt(w, _key(w, role="angle", anchor=anchor), "angle")
        fake = FakeAdapter(_all("angle"))
        r = SheetJudge(adapter=fake).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"],
                                              "asset_id": w["plates"]["angle"]["asset_id"]})
        assert r.success, r.error
        call = fake.calls[0]
        assert call["n_images"] == 2
        assert "Image 2 is the approved establishing plate" in call["user"]
        row = qr.find_verdict_by_id(w["project"], r.data["qc_receipt_id"])
        assert row["establishing_asset_id"] == anchor
        assert row["tuple_sha256"] == qr.tuple_sha256(row)

    def test_the_second_image_is_the_anchor_plate_bytes(self, world, monkeypatch):
        w = world
        _patch_location_config(monkeypatch)
        anchor = w["plates"]["establishing"]["asset_id"]
        a = _attempt(w, _key(w, role="angle", anchor=anchor), "angle")

        class _Recording(FakeAdapter):
            def submit(self, *, model, system, user, images, schema):
                self.images = list(images)
                return super().submit(model=model, system=system, user=user, images=images, schema=schema)

        fake = _Recording(_all("angle"))
        r = SheetJudge(adapter=fake).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"],
                                              "asset_id": w["plates"]["angle"]["asset_id"]})
        assert r.success, r.error
        assert [hashlib.sha256(b).hexdigest() for b in fake.images] == [w["plates"]["angle"]["asset_id"], anchor]

    def test_an_angle_beside_another_plate_is_a_new_evaluation(self, world, monkeypatch):
        """R2#4 end to end: the same pixels judged under a different anchor are
        judged again, never reused."""
        w = world
        _patch_location_config(monkeypatch)
        est = w["plates"]["establishing"]["asset_id"]
        a1 = _attempt(w, _key(w, role="angle", anchor=est), "angle")
        first = SheetJudge(adapter=FakeAdapter(_all("angle"))).execute(
            {"project_dir": str(w["project"]), "attempt_id": a1["attempt_id"],
             "asset_id": w["plates"]["angle"]["asset_id"]})
        assert first.success, first.error
        # same pixels, same look, same policy — but anchored to the OTHER plate
        other = w["plates"]["angle"]["asset_id"]
        a2 = _attempt(w, _key(w, role="angle", anchor=other), "angle")
        fake = FakeAdapter(_all("angle"))
        second = SheetJudge(adapter=fake).execute(
            {"project_dir": str(w["project"]), "attempt_id": a2["attempt_id"],
             "asset_id": w["plates"]["angle"]["asset_id"]})
        assert second.success, second.error
        assert second.data.get("reused") is not True and fake.calls != []
        assert second.data["qc_receipt_id"] != first.data["qc_receipt_id"]

    def test_identical_anchor_and_pixels_reuse_the_verdict(self, world, monkeypatch):
        w = world
        _patch_location_config(monkeypatch)
        est = w["plates"]["establishing"]["asset_id"]
        a1 = _attempt(w, _key(w, role="angle", anchor=est), "angle")
        first = SheetJudge(adapter=FakeAdapter(_all("angle"))).execute(
            {"project_dir": str(w["project"]), "attempt_id": a1["attempt_id"],
             "asset_id": w["plates"]["angle"]["asset_id"]})
        assert first.success, first.error
        a2 = _attempt(w, _key(w, role="angle", anchor=est), "angle")
        fake = FakeAdapter(_all("angle", "no"))
        second = SheetJudge(adapter=fake).execute(
            {"project_dir": str(w["project"]), "attempt_id": a2["attempt_id"],
             "asset_id": w["plates"]["angle"]["asset_id"]})
        assert second.success and second.data["reused"] and fake.calls == []

    def test_a_portrait_plate_fails_locally_without_a_provider_call(self, world, monkeypatch):
        w = world
        _patch_location_config(monkeypatch)
        data = _png(1024, 1600, "tall")
        sha = hashlib.sha256(data).hexdigest()
        (w["project"] / "canon/visual/objects" / f"{sha}.png").write_bytes(data)
        row = receipts.record_generation(
            w["project"], execution_id="exec-tall", tool="reference_import", normalized_inputs_hash="a" * 64,
            output_sha256=sha, cost_usd=0.0, started_at="t", finished_at="t", generator_kind="imported",
            origin_tool="midjourney", attestation_receipt_id="att-tall", look_refs=H.look_refs_for(w["look"]),
            import_receipt_id="imp-tall", normalized_pixel_hash=sha)
        w["plates"]["tall"] = {"asset_id": sha, "gen": row}
        a = _attempt(w, _key(w), "tall")
        fake = FakeAdapter(_all("establishing"))
        r = SheetJudge(adapter=fake).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"],
                                              "asset_id": sha})
        assert r.success and r.data["local"] and r.data["failing_items"] == ["orientation"]
        assert fake.calls == []

    def test_a_config_with_no_location_policy_refuses_the_call(self, world):
        """Fail closed until a 1.3 config pins the location bundle (Step 3)."""
        w = world
        a = _attempt(w, _key(w), "establishing")
        r = SheetJudge(adapter=FakeAdapter(_all("establishing"))).execute(
            {"project_dir": str(w["project"]), "attempt_id": a["attempt_id"],
             "asset_id": w["plates"]["establishing"]["asset_id"]})
        assert not r.success and "location_policy_sha256" in r.error

    def test_a_stale_pinned_location_bundle_refuses_the_call(self, world, monkeypatch):
        w = world
        _patch_location_config(monkeypatch, location_hash="9" * 64)
        a = _attempt(w, _key(w), "establishing")
        r = SheetJudge(adapter=FakeAdapter(_all("establishing"))).execute(
            {"project_dir": str(w["project"]), "attempt_id": a["attempt_id"],
             "asset_id": w["plates"]["establishing"]["asset_id"]})
        assert not r.success and "re-sign the config" in r.error

    def test_a_generated_location_plate_is_refused_by_the_judge(self, world, monkeypatch):
        """Generate mode is deferred: the judge refuses a model receipt with the
        verifier's message rather than silently judging an unanchored plate."""
        w = world
        _patch_location_config(monkeypatch)
        data = _png(1600, 1024, "generated")
        sha = hashlib.sha256(data).hexdigest()
        (w["project"] / "canon/visual/objects" / f"{sha}.png").write_bytes(data)
        row = receipts.record_generation(
            w["project"], execution_id="exec-gen", tool="seedream_image", normalized_inputs_hash="a" * 64,
            output_sha256=sha, cost_usd=0.1, started_at="t", finished_at="t", model_endpoint=H.IMAGE_MODEL,
            prompt="a place", look_refs=H.look_refs_for(w["look"]))
        w["plates"]["generated"] = {"asset_id": sha, "gen": row}
        a = _attempt(w, _key(w), "generated")
        r = SheetJudge(adapter=FakeAdapter(_all("establishing"))).execute(
            {"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": sha})
        assert not r.success and "generate mode" in r.error
