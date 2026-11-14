"""Signed trailer-cast changes (cast_change receipts layered over the proposal).

The proposal checkpoint's bytes are fingerprinted by pipeline_migration
receipts, so it is never edited: a human-signed ``cast_change`` receipt
carries the whole resulting cast, and every cast reader uses the effective
cast. Invented names only.
"""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import gates, receipts  # noqa: E402
from lib.canonical_json import record_sha256  # noqa: E402
from lib.cast_change import (  # noqa: E402
    CastChangeError,
    effective_cast,
    prepare_cast_change_request,
    proposal_sha256,
    with_effective_cast,
)
from scripts import gate_approve  # noqa: E402
from tests.lib.look_lock_helpers import activate_look, approve_request, decline_request, location_look  # noqa: E402

BASE = {"character_ids": ["hero-a"], "location_ids": ["arena", "home"]}


class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def human_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Tty())


def write_proposal(root: Path, cast: dict = BASE, **extra) -> None:
    packet = {"version": "1.1", "cast": cast, **extra}
    (root / "checkpoint_proposal.json").write_text(json.dumps({"artifacts": {"proposal_packet": packet}}, indent=2))


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "projects" / "p"
    (root / ".gate-requests").mkdir(parents=True)
    (root / "project.json").write_text(json.dumps({"project_id": "p", "pipeline_type": "authored-film"}))
    (root / "project.yaml").write_text("title: test\n")
    write_proposal(root)
    canon = {"characters": [{"id": "hero-a", "name": "Hero A"}, {"id": "side-b", "name": "Side B"}],
             "locations": [{"id": "arena", "name": "The arena"}, {"id": "home", "name": "Home"}]}
    (root / "checkpoint_canon_ingest.json").write_text(json.dumps({"artifacts": {"canon_packet": canon}}))
    return root


def packet(root: Path) -> dict:
    return json.loads((root / "checkpoint_proposal.json").read_text())["artifacts"]["proposal_packet"]


def sign(root: Path, record: dict, supersedes=None, project_id: str = "p") -> dict:
    """Stand in for the gate handler (as look_lock_helpers does) to lay a receipt directly."""
    with gates.handler_context():
        token = gates.mint_gate_token(project_id, "proposal", "cast:trailer", record_sha256(record), user_response="approve")
    return receipts.record_human_approval(
        root, project_id, "proposal", "cast:trailer", record, token, "cast_change",
        entity_id="trailer_cast", envelope={"supersedes_receipt_id": supersedes},
    )


def change(root: Path, **kw) -> dict:
    return json.loads(prepare_cast_change_request(root, "p", **kw).read_text())


class TestEffectiveCast:
    def test_no_receipts_is_the_proposal_cast(self, root):
        assert effective_cast(root, packet(root)) == BASE
        assert effective_cast(root) == BASE

    def test_signed_change_is_applied(self, root, human_tty):
        activate_look(root, location_look("parlor"))
        req = change(root, remove=["arena"], add=["parlor"])
        approve_request(req, root)
        assert effective_cast(root, packet(root)) == {"character_ids": ["hero-a"], "location_ids": ["home", "parlor"]}
        out = with_effective_cast(root, packet(root))
        assert out["cast"]["location_ids"] == ["home", "parlor"] and out["version"] == "1.1"
        assert packet(root)["cast"] == BASE  # the proposal file itself is never edited

    def test_a_changed_proposal_drops_old_amendments(self, root, human_tty):
        approve_request(change(root, remove=["arena"]), root)
        assert effective_cast(root)["location_ids"] == ["home"]
        write_proposal(root, {"character_ids": ["hero-a"], "location_ids": ["arena", "home", "pier"]})
        assert effective_cast(root, packet(root))["location_ids"] == ["arena", "home", "pier"]

    def test_a_packet_that_is_not_the_one_on_disk_gets_no_amendment(self, root, human_tty):
        approve_request(change(root, remove=["arena"]), root)
        other = dict(packet(root), cast={"character_ids": ["x"], "location_ids": ["arena"]})
        assert effective_cast(root, other) == {"character_ids": ["x"], "location_ids": ["arena"]}

    def test_chain_of_two_uses_the_tip(self, root, human_tty):
        first = approve_request(change(root, remove=["arena"]), root)
        req2 = change(root, add=["side-b"])
        assert req2["envelope"] == {"supersedes_receipt_id": first["receipt_id"]}
        assert req2["request_id"] == "cast-change-2"
        approve_request(req2, root)
        assert effective_cast(root) == {"character_ids": ["hero-a", "side-b"], "location_ids": ["home"]}

    def test_unsigned_or_forged_rows_are_ignored(self, root):
        record = {"base_proposal_sha256": proposal_sha256(root), "character_ids": [], "location_ids": [],
                  "removed": [], "added": []}
        with (root / "approvals.jsonl").open("a") as fh:
            fh.write(json.dumps({"kind": "cast_change", "entity_id": "trailer_cast", "record": record}) + "\n")
        with pytest.raises(Exception):  # an unchained local row breaks the receipt chain: fail closed
            effective_cast(root)

    def test_receipt_laid_directly_applies_by_base_sha(self, root):
        record = {"base_proposal_sha256": proposal_sha256(root), "character_ids": ["hero-a"], "location_ids": ["home"],
                  "removed": [{"kind": "location", "id": "arena"}], "added": []}
        sign(root, record)
        assert effective_cast(root)["location_ids"] == ["home"]


class TestPrepare:
    def test_request_file_shape_and_summary(self, root):
        activate_look(root, location_look("parlor"))
        req = change(root, remove=["arena"], add=["parlor"])
        assert req["request_id"] == "cast-change-1"
        assert (req["kind"], req["stage"], req["scope"], req["entity_id"]) == ("cast_change", "proposal", "cast:trailer", "trailer_cast")
        assert req["summary"] == "Take The arena out of the approved trailer cast and put Parlor in."
        assert req["cast_change"] == {"remove": ["arena"], "add": ["parlor"]}
        rec = req["approval_record"]
        assert rec == {
            "base_proposal_sha256": proposal_sha256(root),
            "character_ids": ["hero-a"], "location_ids": ["home", "parlor"],
            "removed": [{"kind": "location", "id": "arena"}], "added": [{"kind": "location", "id": "parlor"}],
        }
        assert req["envelope"] == {"supersedes_receipt_id": None}
        assert req["source_checkpoint_digest"] is None and req["preview_paths"] == []

    def test_added_character_kind_comes_from_canon(self, root):
        req = change(root, add=["side-b"])
        assert req["approval_record"]["character_ids"] == ["hero-a", "side-b"]
        assert req["summary"] == "Put Side B in the approved trailer cast."

    def test_remove_only_summary(self, root):
        assert change(root, remove=["arena", "home"])["summary"] == \
            "Take The arena and Home out of the approved trailer cast."

    @pytest.mark.parametrize("kw, match", [
        ({}, "at least one"),
        ({"remove": ["nowhere"]}, "not in the approved trailer cast"),
        ({"add": ["arena"]}, "already in the approved trailer cast"),
        ({"add": ["stranger"]}, "Front Lot doesn't know"),
        ({"remove": ["arena"], "add": ["arena"]}, "both"),
        ({"remove": ["arena", "arena"]}, "twice"),
        ({"remove": ["../x"]}, "id"),
    ])
    def test_refusals(self, root, kw, match):
        with pytest.raises(CastChangeError, match=match):
            prepare_cast_change_request(root, "p", **kw)
        assert not list((root / ".gate-requests").glob("*.json"))

    def test_one_pending_change_at_a_time(self, root):
        change(root, remove=["arena"])
        with pytest.raises(CastChangeError, match="already waiting"):
            change(root, remove=["home"])

    def test_declined_request_frees_the_slot_and_numbers_move_on(self, root, human_tty):
        req = change(root, remove=["arena"])
        decline_request(req, root, note="not yet")
        assert change(root, remove=["home"])["request_id"] == "cast-change-2"

    def test_validation_is_against_the_effective_cast(self, root, human_tty):
        approve_request(change(root, remove=["arena"]), root)
        with pytest.raises(CastChangeError, match="not in the approved trailer cast"):
            prepare_cast_change_request(root, "p", remove=["arena"])
        assert change(root, add=["arena"])["approval_record"]["location_ids"] == ["home", "arena"]


class TestGate:
    def test_constructor_matches_request_and_signs(self, root, human_tty):
        activate_look(root, location_look("parlor"))
        req = change(root, remove=["arena"], add=["parlor"])
        built = gate_approve.construct(root, req)
        assert built.record == req["approval_record"] and built.envelope == req["envelope"]
        assert built.entity_id == "trailer_cast"
        text = "\n".join(built.evidence)
        assert "The arena" in text and "Parlor" in text
        receipt = approve_request(req, root)
        assert receipt["kind"] == "cast_change" and receipt["supersedes_receipt_id"] is None
        assert (root / ".gate-requests" / "done" / "cast-change-1.json").is_file()

    def test_proposal_changed_after_request_is_refused(self, root, human_tty):
        req = change(root, remove=["arena"])
        write_proposal(root, BASE, note="edited")
        with pytest.raises(gate_approve.GateHandlerError):
            approve_request(req, root)
        assert effective_cast(root, packet(root)) == BASE

    def test_proposal_changed_between_display_and_signing_is_refused(self, root, human_tty):
        req = change(root, remove=["arena"])
        shown = gate_approve.construct(root, req)
        write_proposal(root, BASE, note="edited")
        with gates.handler_context(), pytest.raises(gate_approve.GateHandlerError):
            gate_approve._decide(req, root, shown=shown, note=None)

    def test_pre_commit_refuses_a_moved_tip(self, root, human_tty):
        req = change(root, remove=["arena"])
        built = gate_approve.construct(root, req)
        sign(root, req["approval_record"])  # someone else's receipt lands first
        with pytest.raises(gate_approve.GateHandlerError, match="changed"):
            built.pre_commit_check()

    def test_entity_must_be_the_trailer_cast(self, root, human_tty):
        req = change(root, remove=["arena"])
        req["entity_id"] = "other"
        with pytest.raises(gate_approve.GateHandlerError):
            gate_approve.construct(root, req)


class TestScript:
    def test_cli_files_the_request_and_says_where_to_sign(self, root, monkeypatch, capsys):
        from scripts import cast_change as cli

        activate_look(root, location_look("parlor"))
        rc = cli.main(["--project", "p", "--remove", "arena", "--add", "parlor",
                       "--projects-dir", str(root.parent)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Take The arena out of the approved trailer cast and put Parlor in." in out
        assert "Needs you" in out
        assert (root / ".gate-requests" / "cast-change-1.json").is_file()

    def test_cli_refusal_is_a_plain_error(self, root, capsys):
        from scripts import cast_change as cli

        assert cli.main(["--project", "p", "--remove", "nowhere", "--projects-dir", str(root.parent)]) == 1
        assert "not in the approved trailer cast" in capsys.readouterr().err


class TestReaders:
    """Every cast reader honours a signed cast change."""

    def test_look_ingest_packet_completeness(self, root, human_tty):
        from lib.look_ingest import build_look_packet

        assert build_look_packet(root, {}, project_id="p", proposal=packet(root))["complete"] is False
        approve_request(change(root, remove=["hero-a", "arena", "home"]), root)
        assert build_look_packet(root, {}, project_id="p", proposal=packet(root))["complete"] is True

    def test_hero_migration_blockers_cover_only_the_effective_cast(self, root, human_tty, monkeypatch):
        import lib.headshots as hs
        from lib.headshot_verify import hero_migration_blockers

        monkeypatch.setattr(hs, "active_headshots", lambda project_dir: {"hero-a": object()})
        assert hero_migration_blockers(root)  # hero-a is cast: its hero is checked (no config here: a blocker)
        approve_request(change(root, remove=["hero-a"]), root)
        assert hero_migration_blockers(root) == []

    def test_canon_enforcement_reads_the_effective_cast(self, root, human_tty):
        from lib.canon_enforcement import _cast_keys, _load_proposal

        approve_request(change(root, remove=["arena"], add=["side-b"]), root)
        proposal = _load_proposal(root.parent, "p", {})
        assert _cast_keys(proposal) == [("character", "hero-a"), ("character", "side-b"), ("location", "home")]
        assert proposal["version"] == "1.1"

    def test_reference_import_may_bind_an_entity_put_in_by_a_cast_change(self, root, human_tty, tmp_path):
        from PIL import Image

        from lib.reference_import import ORIGIN_IMPORTED_SYNTHETIC, prepare_reference_import

        src = tmp_path / "ref.png"
        Image.new("RGB", (12, 9), (40, 80, 120)).save(src)
        prepared = prepare_reference_import(root, "p", src, origin_class=ORIGIN_IMPORTED_SYNTHETIC,
                                            origin_tool="elsewhere", entity_id="side-b")
        req = json.loads(prepared.request_path.read_text())
        with pytest.raises(gate_approve.GateHandlerError, match="not in the approved trailer cast"):
            gate_approve.construct(root, req)
        approve_request(change(root, add=["side-b"]), root)
        assert gate_approve.construct(root, req).record["entity_id"] == "side-b"


class TestBoard:
    def test_gate_detail_shows_the_change_in_names(self, root):
        from backlot.state import load_gate_detail

        activate_look(root, location_look("parlor"))
        req = change(root, remove=["arena"], add=["parlor"])
        packet = load_gate_detail(root, req["request_id"])["packet"]
        assert packet == {
            "summary": "Take The arena out of the approved trailer cast and put Parlor in.",
            "cast_now": "Characters: Hero A. Places: The arena, Home.",
            "cast_after_signing": "Characters: Hero A. Places: Home, Parlor.",
            "taking_out": "The arena",
            "putting_in": "Parlor",
        }

    def test_gate_detail_builds_on_the_signed_tip_and_flags_a_moved_proposal(self, root, human_tty):
        from backlot.state import load_gate_detail

        approve_request(change(root, remove=["arena"]), root)
        req = change(root, add=["side-b"])
        packet = load_gate_detail(root, req["request_id"])["packet"]
        assert packet["cast_now"] == "Characters: Hero A. Places: Home."
        assert packet["cast_after_signing"] == "Characters: Hero A, Side B. Places: Home."
        assert "packet_error" not in packet
        write_proposal(root, BASE, note="edited")
        packet = load_gate_detail(root, req["request_id"])["packet"]
        assert packet["packet_error"] is True and "changed" in packet["error"]

    def test_gate_words(self):
        board = (Path(__file__).resolve().parents[2] / "backlot" / "ui" / "board.js").read_text()
        assert 'cast_change: "Change the trailer cast"' in board
