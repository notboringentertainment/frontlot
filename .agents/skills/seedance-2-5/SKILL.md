---
name: seedance-2-5
description: |
  Generate reference-conditioned video with ByteDance Seedance 2.5 on fal.ai — NO LONGER the authored-film default (D4 revised 2026-08-25: on FAL it rejects any human face as a reference, photoreal or drawn; the default is `kling-o3-reference`). Use ONLY for scenes marked entity_free (no character refs) through a scene-level model_override with a reason. Use when: (1) every shot must hold identity against approved character/location sheets, (2) references must be addressed in the prompt as @Image1…@ImageN (up to 30 images), (3) a storyboard frame should steer composition — as a reference, since 2.5 has NO start-frame parameter, (4) a scene's `model_endpoint` is Seedance 2.5 (the project default). Accessible via fal.ai only (`seedance_video` with `model_version: "2.5"`, FAL_KEY). For Seedance 2.0's broader gateway menu and the general prompting methodology, read `seedance-2-0`; this skill covers only what differs.
allowed-tools: Bash, Read, Write
metadata:
  openclaw:
    requires:
      env_any:
        - FAL_KEY
---

# Seedance 2.5 (ByteDance) — reference-to-video contract

Read `.agents/skills/seedance-2-0/SKILL.md` for the prompt methodology
(opener declaration, temporal markers, camera negation, identity-anchor
phrases). Everything there applies. This file records what is **different
and binding** for 2.5 as verified against the live FAL contract on 2026-08-25.

## The contract facts (verified; deviations from the plan are logged)

| Fact | Consequence |
|---|---|
| **No start-frame / first-frame parameter** | A storyboard frame is a *reference*, never a first frame. Do not promise the shot opens on it. |
| References are prompt-addressable: `@Image1 … @ImageN`, **up to 30 images** | The prompt must name each slot and what it governs; unaddressed references are decoration. |
| Pricing ≈ $0.47/s at 720p | A 10s take ≈ $4.70; ~3 candidates per shot ≈ $14; 12–20 shots ≈ $170–$280 in video alone. Reserve before every call. |
| FAL has no client idempotency key | The tool sends `X-Fal-No-Retry: 1`, persists the reservation `submitting` before the request, writes the request id on return. A crash in between → `indeterminate` → halt for human reconciliation. Never resubmit. |
| Allowlisted hosts only: `fal.run`, `queue.fal.run`, `fal.media` | Any other status/result host is refused. |

Endpoint: `bytedance/seedance-2.5/reference-to-video`. The 2.0 endpoints and
payload stay intact under `model_version: "2.0"`.

## Calling it inside OpenMontage

```python
from tools.tool_registry import registry
registry.ensure_discovered()
seedance = registry.get("seedance_video")
seedance.execute({
    "model_version": "2.5",
    "operation": "reference_to_video",
    "prompt": PROMPT,                       # addresses @Image1..@ImageN
    "reference_image_paths": [              # ORDER = slot number
        ".../objects/<hero_a>.png",         # @Image1  character A hero
        ".../objects/<wardrobe_a>.png",     # @Image2  character A wardrobe
        ".../objects/<establishing>.png",   # @Image3  location establishing
        "projects/<slug>/assets/images/storyboard/s03-sh02.png",  # @Image4 storyboard, last
    ],
    "duration": "8",
    "aspect_ratio": "21:9",
    "resolution": "720p",
    "generate_audio": True,
    "seed": 4402,
    "project_id": "<slug>",
    "shot_id": "s03-sh02",
    "take_id": "t01",
})
```

Reference packing is not a creative choice; the asset director packs
deterministically (hero + wardrobe per `character_refs` entry, establishing
for `location_ref`, storyboard frame last) and preflight fails above the cap.
Paths are resolved strictly inside the project root and re-hashed before
upload; every image must carry a generation receipt (pipeline output only —
no real-person photos, no hand-copied files).

## Prompt shape for a bible-conditioned shot

```
[opener — single continuous shot / multi-shot declaration, film stock, "no 3D, no cartoon"]

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
Sound: room tone, one distant door, no music.
```

Rules the asset director holds you to:
- **One action per shot.** Two actions is two shots.
- **Longer single takes over stitching** — 6–10s that holds identity beats
  three 3s fragments.
- **The passport is verbatim.** `approved_prompt_block` is pasted, never
  paraphrased; it is inside the approval receipt's hash.
- **No dialogue the writer did not write.** Trailer shots use protected lines
  verbatim or no lines. Lip-sync syntax (`Character says: "…"`) only with a
  protected line, quoted exactly.
- **No readable text.** Titles come from the locally rendered poster assets.
- **Scene endpoint, not shot endpoint.** Every candidate and every selected
  take in a scene runs on the scene's `model_endpoint`. Do not "just try 2.0"
  on a hard shot — a scene-level `model_override` with a reason is the only
  path, and it is the writer's call at the scene-plan gate.

## Anti-drift on 2.5

- Cull, don't flood: hero + wardrobe + establishing + storyboard is the working
  pack. Adding all six sheet views rarely helps and eats the identity budget.
- If the face morphs: shorten the duration first (5–6s), then tighten anchor
  lines, then add the front sheet view as an extra reference — in that order.
- If wardrobe drifts: the `wardrobe_negative` line is missing or too soft; make
  the garment category and colour explicit ("navy field jacket, open, no
  hood").
- A take that drifts mid-shot is `usage_status: rejected`, not trimmed around.

## Verification checklist for every 2.5 take

- [ ] Identity matches the approved hero and wardrobe views (not the previous take)
- [ ] Composition honours the storyboard frame where the prompt asked for it
- [ ] Exactly one action; camera did what the prompt said and nothing it negated
- [ ] No rendered text
- [ ] `references_applied` in the manifest lists every packed reference with its role and entity id
- [ ] A generation receipt exists whose `output_sha256` matches the file
- [ ] ffprobe-valid video stream, duration as requested

## Sources

- fal.ai Seedance 2.5 reference-to-video: https://fal.ai/models/bytedance/seedance-2.5/reference-to-video
- Contract fixture: `tests/fixtures/providers/bytedance-seedance-2.5-reference-to-video.json` (authoritative over this prose)
- Build-time findings: `PLAN-REVIEW-LOG.md` → "Act 3 — Build"
