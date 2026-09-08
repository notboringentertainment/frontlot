"""The shot -> scene mapping the paid boundary reads (``_scene_plan_shot_index``).

scene_plan scenes are keyed ``id`` at both 1.0 and 1.1 (the schema requires it,
and ``lib.canon_enforcement`` / ``lib.checkpoint.entity_free_scene_ids`` read it).
The boundary index read ``scene_id`` instead, so it was ALWAYS empty — which
silently turned two exemptions off:

* a storyboard frame for a PLANNED shot, whose prompt is the shot itself (a
  composite of the shot description and the identity paragraph, not a builder
  rendering of one look), was refused as "not a shot of the scene_plan
  checkpoint";
* ``shot_is_entity_free`` could never be True, whatever the approved plan said.

These tests hold both open at the real boundary: the upload and submission
functions are watched, and a refusal must reach neither.

All network is mocked. Invented ids only.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lib.look_spec import look_hash as _look_hash
from tools.graphics.seedream_image import SeedreamImage
from tools.video import _shared

from tests.tools._authored_film_helpers import write_scene_plan_checkpoint
from tests.tools.test_kling_reference_video import (  # noqa: F401  (env fixture)
    _happy_path,
    _inputs,
    _shot_setup,
    env,
)
from tests.tools.test_shot_allowance import (  # noqa: F401  (shot fixture + its transitive deps)
    SHOT,
    seedream_inputs,
    shot,
    shot_record,
    write_scene_plan,
)
from tests.tools.test_shot_ready_ownership import ENTITY

COMPOSITE_PROMPT = (
    "Storyboard frame for the shot: an empty hall, slow push in on the figure at the far door. "
    "The figure is the character in the attached reference: same face, same hair, same coat."
)


def _composite_inputs(shot: dict, **extra) -> dict:
    """A storyboard frame whose prompt is the SHOT, not a look rendering: the
    shot description plus an identity paragraph, and no ``prompt_recipe``."""
    inputs = seedream_inputs(shot, **extra)
    inputs.pop("prompt_recipe")
    inputs.pop("asset_role", None)
    inputs["prompt"] = COMPOSITE_PROMPT
    return inputs


def _run(shot: dict, inputs: dict):
    from tests.tools.test_seedream_image import _fake_download

    with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png") as up, \
         patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-b1"}) as submit, \
         patch.object(_shared, "fal_queue_wait", return_value={"images": [{"url": "https://v3.fal.media/o.png"}]}), \
         patch.object(_shared, "fal_download", side_effect=_fake_download):
        result = SeedreamImage().execute(inputs)
    return result, up, submit


class TestAPlannedShotMayBeRenderedFromItsDescription:
    def test_a_composite_storyboard_prompt_for_a_planned_shot_reaches_the_provider(self, shot):
        """The exemption the empty index was closing: a planned shot's frame
        needs no ``prompt_recipe``, and the call gets as far as upload+submit."""
        result, up, submit = _run(shot, _composite_inputs(shot))
        assert result.success, result.error
        up.assert_called()
        submit.assert_called_once()

    def test_the_same_call_for_an_unplanned_shot_is_refused_before_any_upload(self, shot):
        inputs = _composite_inputs(shot, shot_id="sh-not-in-the-plan")
        result, up, submit = _run(shot, inputs)
        assert not result.success
        up.assert_not_called()
        submit.assert_not_called()

    def test_the_boundary_itself_names_the_unplanned_shot(self, shot):
        """Straight at ``verify_look_governance``: a rendering of look_refs
        naming a shot the plan does not contain still needs a prompt_recipe."""
        inputs = _composite_inputs(shot, shot_id="sh-not-in-the-plan")
        with pytest.raises(_shared.LookGovernanceError, match="not a shot of the scene_plan checkpoint"):
            _shared.verify_look_governance(inputs, shot["project"])

    def test_the_index_maps_every_planned_shot_to_its_scene_id(self, shot):
        write_scene_plan(
            shot["project"],
            [shot_record(spend_allowance_usd=1.0, max_video_takes=1),
             {"shot_id": "sh-2", "description": "Reverse, static."}],
        )
        assert _shared._scene_plan_shot_index(shot["project"]) == {SHOT: "sc-1", "sh-2": "sc-1"}

    def test_a_scene_without_an_id_maps_nothing(self, shot):
        """``scene_id`` is not a scene key at any scene_plan version; a scene
        that carries no ``id`` contributes no shots rather than a wrong one."""
        import json

        path = shot["project"] / "checkpoint_scene_plan.json"
        checkpoint = json.loads(path.read_text())
        scene = checkpoint["artifacts"]["scene_plan"]["scenes"][0]
        scene["scene_id"] = scene.pop("id")
        path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
        assert _shared._scene_plan_shot_index(shot["project"]) == {}
