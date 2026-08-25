"""BaseTool instrumentation: execution_id in events + fatal generation receipts."""

from __future__ import annotations

import pytest

from lib.events import read_events
from lib.receipts import find_generation, generation_receipts_path
from lib.state_io import read_jsonl
from tools.base_tool import BaseTool, ToolResult, current_execution_id, normalized_inputs_hash

from tests.tools._authored_film_helpers import make_project


def _write_output(project, name="out.bin", payload=b"hello"):
    out = project / "assets" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    return out


def test_execution_id_is_on_all_events_and_thread_local(tmp_path, monkeypatch):
    project = make_project(tmp_path, monkeypatch)
    seen = {}

    class Plain(BaseTool):
        name = "plain_tool"

        def execute(self, inputs):
            seen["inside"] = current_execution_id()
            return ToolResult(success=True)

    out = _write_output(project)
    Plain().execute({"output_path": str(out)})
    events = read_events(project)
    assert [e["event"] for e in events] == ["start", "finish"]
    assert events[0]["execution_id"] == events[1]["execution_id"] == seen["inside"]
    assert current_execution_id() is None  # restored after the call
    assert not generation_receipts_path(project).exists()  # flag not set → untouched


def test_error_event_carries_execution_id(tmp_path, monkeypatch):
    project = make_project(tmp_path, monkeypatch)

    class Boom(BaseTool):
        name = "boom_tool"

        def execute(self, inputs):
            raise RuntimeError("kaput")

    with pytest.raises(RuntimeError):
        Boom().execute({"output_path": str(project / "x.bin")})
    events = read_events(project)
    assert events[-1]["event"] == "error"
    assert events[-1]["execution_id"] == events[0]["execution_id"]


def test_flagged_tool_writes_receipt_with_metadata(tmp_path, monkeypatch):
    project = make_project(tmp_path, monkeypatch)
    out = _write_output(project, payload=b"video-bytes")

    class Gen(BaseTool):
        name = "gen_tool"
        emits_generation_receipt = True

        def execute(self, inputs):
            return ToolResult(
                success=True,
                data={"output_path": str(out)},
                cost_usd=0.25,
                metadata={"model_endpoint": "vendor/model", "provider_request_id": "req-1"},
            )

    inputs = {"prompt": "a lantern", "output_path": str(out), "project_dir": str(project), "seed": 7}
    Gen().execute(inputs)
    from lib.pathsafe import sha256_file

    receipt = find_generation(project, sha256_file(out))
    assert receipt is not None
    assert receipt["tool"] == "gen_tool"
    assert receipt["model_endpoint"] == "vendor/model"
    assert receipt["provider_request_id"] == "req-1"
    assert receipt["cost_usd"] == 0.25
    assert receipt["generator_kind"] == "model"
    assert receipt["execution_id"] == read_events(project)[0]["execution_id"]
    assert receipt["started_at"] <= receipt["finished_at"]
    # path-like keys are excluded from the hash; content keys are not
    assert receipt["normalized_inputs_hash"] == normalized_inputs_hash({"prompt": "a lantern", "seed": 7})


def test_local_generator_passthrough_and_multi_output(tmp_path, monkeypatch):
    project = make_project(tmp_path, monkeypatch)
    a = _write_output(project, "a.png", b"a")
    b = _write_output(project, "b.png", b"b")

    class Local(BaseTool):
        name = "local_gen"
        emits_generation_receipt = True

        def execute(self, inputs):
            return ToolResult(
                success=True,
                data={"output_path": str(a), "output_paths": [str(a), str(b)]},
                metadata={
                    "generator_kind": "local",
                    "local_tool": "local_gen",
                    "local_tool_version": "9.9",
                    "parameters_hash": "ab" * 32,
                    "input_asset_ids": ["cd" * 32],
                },
            )

    Local().execute({"text": "x", "project_dir": str(project)})
    rows = read_jsonl(generation_receipts_path(project))
    assert len(rows) == 2
    assert {r["output_sha256"] for r in rows} == {
        __import__("hashlib").sha256(b"a").hexdigest(),
        __import__("hashlib").sha256(b"b").hexdigest(),
    }
    assert all(r["local_tool_version"] == "9.9" and r["input_asset_ids"] == ["cd" * 32] for r in rows)
    assert len({r["execution_id"] for r in rows}) == 1


def test_receipt_failure_is_fatal(tmp_path, monkeypatch):
    project = make_project(tmp_path, monkeypatch)
    out = _write_output(project)

    class BadMeta(BaseTool):
        name = "bad_meta"
        emits_generation_receipt = True

        def execute(self, inputs):
            # model generation without model_endpoint → record_generation raises
            return ToolResult(success=True, data={"output_path": str(out)})

    with pytest.raises(ValueError, match="model_endpoint"):
        BadMeta().execute({"output_path": str(out)})


def test_no_receipt_for_failed_or_unattributed_result(tmp_path, monkeypatch):
    project = make_project(tmp_path, monkeypatch)
    outside = tmp_path.parent / "elsewhere.bin"
    outside.write_bytes(b"z")

    class Gen(BaseTool):
        name = "gen2"
        emits_generation_receipt = True

        def execute(self, inputs):
            return ToolResult(success=inputs["ok"], data={"output_path": inputs["output_path"]},
                              metadata={"model_endpoint": "m"})

    Gen().execute({"ok": False, "output_path": str(project / "x.bin")})
    Gen().execute({"ok": True, "output_path": str(outside)})
    assert not generation_receipts_path(project).exists()


def test_normalized_inputs_hash_drops_paths_and_objects():
    base = {"prompt": "p", "num_images": 2}
    noisy = {**base, "reference_image_paths": ["/a"], "output_dir": "/o", "cost_tracker": object(), "scene_id": "s"}
    assert normalized_inputs_hash(base) == normalized_inputs_hash(noisy)
    assert normalized_inputs_hash(base) != normalized_inputs_hash({**base, "prompt": "q"})
