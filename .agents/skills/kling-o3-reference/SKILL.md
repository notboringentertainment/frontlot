---
name: kling-o3-reference
description: |
  Generate reference-conditioned video with Kling o3 pro reference-to-video on fal.ai — the default video endpoint for the authored-film pipeline (D4 revised 2026-08-25). Use when: (1) a shot must hold identity against approved character/location sheets that contain human faces (Seedance 2.5 on FAL rejects any human face as a reference — photoreal or drawn), (2) references are addressed in the prompt as @Image1…@ImageN or grouped per entity as @Element1…@ElementN, (3) a storyboard frame steers composition as the last @ImageN, (4) a scene's `model_endpoint` is `fal-ai/kling-video/o3/pro/reference-to-video` (the project default). Accessible via fal.ai only (`kling_reference_video`, FAL_KEY). For the general prompting methodology (opener, temporal markers, camera negation, identity anchors) read `seedance-2-0`; this skill covers only what is binding for Kling o3.
allowed-tools: Bash, Read, Write
metadata:
  openclaw:
    requires:
      env_any:
        - FAL_KEY
---

# Kling o3 pro (fal.ai) — reference-to-video contract

Read `.agents/skills/seedance-2-0/SKILL.md` for the prompt methodology
(opener declaration, temporal markers, camera negation, identity-anchor
phrases). Everything there applies. This file records what is **different
and binding** for Kling o3 pro as verified against the fal.ai contract on
2026-08-25, and why it is the default.

## Why this is the default (D4 revised)

On FAL, Seedance 2.5 refuses **any recognisable human face** as a reference —
`content_policy_violation` / `partner_validation_failed` on pipeline-generated
photoreal portraits AND on a clearly illustrated ink/cel character (Probes A
and B, `PLAN-REVIEW-LOG.md`). Kling o3 pro reference-to-video accepted the
same photoreal pair and held identity across a head turn (`scripts/probe_b_kling.py`).
A trailer needs faces, so the default is Kling. Seedance 2.5 remains available
**only** for scenes marked `entity_free: true` (no `character_refs`) via a
scene-level `model_override {endpoint, reason}` — the writer's call at the
scene-plan gate, never the asset director's.

## The contract facts (verified; fixture is authoritative)

| Fact | Consequence |
|---|---|
| Endpoint `fal-ai/kling-video/o3/pro/reference-to-video`; queue app id `fal-ai/kling-video` | The tool builds status/result URLs from the app id (first two path segments). |
| `prompt` XOR `multi_prompt` (`[{prompt, duration "1".."15"}]`) | One or the other; the tool refuses both or neither. |
| `image_urls` → `@Image1…@ImageN`; `elements` → `@Element1…@ElementN` (`{frontal_image_url, reference_image_urls 1–3, video_url?, voice_id?}`) | Name every slot in the prompt and what it governs. Unaddressed references are decoration. |
| Optional `start_image_url` / `end_image_url` | Exist, but a `shot_visual` take refuses them: the approved storyboard frame is packed as the **last `@ImageN`** (D5, reference not first frame). |
| "Maximum 4 total (elements + reference images) when using video" | With a video reference the pack is hard-capped at 4. **Without video the cap is unconfirmed by the doc** — the tool applies a conservative 9 until a live probe proves more. Over cap fails preflight; never truncated. |
| Only one element may carry a video | `reference_video_url` rides on `@Element1` in the `elements` form only. |
| `duration` "3".."15" (default "5"); `aspect_ratio` 16:9 / 9:16 / 1:1; `generate_audio` default **false**; `shot_type` customize / intelligent | 21:9 is not available — plan the trailer frame at 16:9. |
| Pricing $0.112/s audio off, $0.14/s audio on | A 10s take ≈ $1.12–$1.40; ~3 candidates per shot ≈ $4; 12–20 shots ≈ $50–$85 in video. Reserve before every call. |
| No client idempotency key; `X-Fal-No-Retry: 1` | Reservation persisted `submitting` before submit, request id written on return. A crash in between → `indeterminate` → halt for human reconciliation. Never resubmit. |
| Allowlisted hosts only: `fal.run`, `queue.fal.run`, `*.fal.media` | Any other status/result host is refused. |

Related (not implemented here): `fal-ai/kling-video/v3/pro/image-to-video`
(start_image_url REQUIRED, elements supported, $0.112 / $0.168 per second) for
a start-frame-driven shot, if the writer ever rules that a frame must open a shot.

## Calling it inside OpenMontage

```python
from tools.tool_registry import registry
registry.ensure_discovered()
kling = registry.get("kling_reference_video")
kling.execute({
    "prompt": PROMPT,                       # addresses @Image1..@ImageN
    "reference_image_paths": [              # ORDER = slot number
        ".../objects/<hero_a>.png",         # @Image1  character A hero
        ".../objects/<wardrobe_a>.png",     # @Image2  character A wardrobe
        ".../objects/<establishing>.png",   # @Image3  location establishing
    ],
    "reference_manifest": [...],            # one {asset_id, path, role, visual_bible_entity_id} per path
    "asset_class": "shot_visual",           # the tool appends the storyboard frame as @Image4, LAST
    "shot_id": "s03-sh02",
    "storyboard_frame_sha256": "<approved frame asset_id>",
    "duration": "8",
    "aspect_ratio": "16:9",
    "generate_audio": False,
    "project_dir": "projects/<slug>",
    "output_path": "projects/<slug>/assets/video/s03-sh02-t01.mp4",
})
```

`reference_form: "elements"` groups the same manifest by
`visual_bible_entity_id` into `@ElementN` packs (role `hero` → frontal, other
views → 1–3 references). Locations have no frontal, so keep them in the
`image_urls` form (the default) unless the shot is character-only.

Reference packing is not a creative choice; the asset director packs
deterministically (hero + wardrobe per `character_refs` entry, establishing
for `location_ref`, storyboard frame last) and preflight fails above the cap.
Paths are resolved strictly inside the project root and re-hashed before
upload; every image must carry a generation receipt (pipeline output only —
no real-person photos, no hand-copied files).

## Prompt shape for a bible-conditioned shot

```
[opener — single continuous shot, film stock, "no 3D, no cartoon"]

@Image1 is MARA VOSS's face and hair — maintain exact appearance, no drift,
no face morph. @Image2 is her costume — do not alter garment category or
colour. @Image3 is the location — match its architecture and light.
@Image4 is the composition and blocking to match for the opening framing.

<approved_prompt_block for MARA VOSS, pasted verbatim>
<wardrobe_negative>

Action (one): Mara steps off the last stair and stops, listening.
Camera: slow push-in, 35mm, no zoom, no cuts.
0–3s: … 3–8s: …
Palette: #1B2A38, #C97B3A, #E8E2D3, #4A5A4E.
Never: beard, jewellery, braided hair, tactical gear, smiling.
```

Rules the asset director holds you to:
- **One action per shot.** Two actions is two shots.
- **Longer single takes over stitching** — 6–10s that holds identity beats
  three 3s fragments.
- **The passport is verbatim.** `approved_prompt_block` is pasted, never
  paraphrased; it is inside the approval receipt's hash.
- **No dialogue the writer did not write.** `generate_audio` stays off unless
  the scene plan asks for it; lines are protected lines verbatim or nothing.
- **No readable text.** Titles come from the locally rendered poster assets.
- **Scene endpoint, not shot endpoint.** Every candidate and every selected
  take in a scene runs on the scene's `model_endpoint`. Switching to Seedance
  is a scene-level `model_override` with a reason, valid only on an
  `entity_free` scene.

## Anti-drift on Kling o3

- Cull, don't flood: hero + wardrobe + establishing + storyboard is the working
  pack. Every extra reference eats the identity budget.
- If the face morphs: shorten the duration first (5–6s), then tighten anchor
  lines, then switch that character to the `elements` form (frontal + up to 3
  views) — in that order.
- If wardrobe drifts: the `wardrobe_negative` line is missing or too soft; make
  the garment category and colour explicit.
- A take that drifts mid-shot is `usage_status: rejected`, not trimmed around.

## Verification checklist for every take

- [ ] Identity matches the approved hero and wardrobe views (not the previous take)
- [ ] Composition honours the storyboard frame where the prompt asked for it
- [ ] Exactly one action; camera did what the prompt said and nothing it negated
- [ ] No rendered text
- [ ] `references_applied` in the manifest lists every packed reference with its role and entity id
- [ ] A generation receipt exists whose `output_sha256` matches the file and whose `model_endpoint` is the scene endpoint
- [ ] ffprobe-valid video stream, duration as requested, audio present iff `generate_audio`

## Sources

- fal.ai Kling o3 pro reference-to-video: https://fal.ai/models/fal-ai/kling-video/o3/pro/reference-to-video
- Contract fixture: `tests/fixtures/providers/fal-ai-kling-video-o3-pro-reference-to-video.json` (authoritative over this prose)
- Evidence: `PLAN-REVIEW-LOG.md` → "Probes (2026-08-25 late)" and "D4 revised"; working probe `scripts/probe_b_kling.py`
