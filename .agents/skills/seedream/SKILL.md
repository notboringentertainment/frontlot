---
name: seedream
description: |
  Generate and reference-edit still images with ByteDance Seedream 5 Pro on fal.ai — the OpenMontage sheet generator for the authored-film visual bible. Use when: (1) producing a hero portrait or establishing plate from text (`text_to_image`), (2) deriving consistent views from an approved anchor image — turnarounds, full body, expression grids, wardrobe isolates, location angles (`edit`, up to 10 reference images), (3) generating storyboard frames or poster key art that must match approved character/location sheets, (4) any image that will later be cited as a `@ImageN` reference in a Seedance 2.5 prompt. Accessible via fal.ai only (`seedream_image` tool, FAL_KEY). Never use it to render titles, logos, or lettering.
allowed-tools: Bash, Read, Write
metadata:
  openclaw:
    requires:
      env_any:
        - FAL_KEY
---

# Seedream 5 Pro (ByteDance) — text-to-image and multi-reference edit

Seedream 5 Pro is the image model OpenMontage uses wherever a still must agree
with other stills: character sheets, location plates, storyboard frames, key
art. Its `edit` endpoint accepts **up to 10 reference images** and honors
them as identity/composition anchors, which is what makes "derive the sheet
from the approved hero" possible on one model with one strategy.

## fal.ai endpoints (used by `seedream_image`)

```
bytedance/seedream/v5/pro/text-to-image     # operation: text_to_image
bytedance/seedream/v5/pro/edit              # operation: edit, image_urls[1..10]
```

Verified request contracts live in `tests/fixtures/providers/<endpoint>.json`;
the tool sends only the fields those fixtures verify. Every call goes out with
`X-Fal-No-Retry: 1` — FAL otherwise auto-retries on 503/504 and can double-
charge — and is preceded by a persisted cost reservation. Outputs are staged,
re-encoded to PNG deterministically, hashed, and (for canon) moved to
`canon/visual/objects/<sha256>.png`. You never choose the output filename.

Fallback: `fal-ai/flux-pro/kontext` via `flux_image` (Kontext mode) takes
**one** reference image. It is a single-anchor fallback, not an equal — say so
in any gate summary when it was used.

## Calling it inside OpenMontage

```python
from tools.tool_registry import registry
registry.ensure_discovered()
seedream = registry.get("seedream_image")

# Hero portrait candidates (4 calls, record each seed)
seedream.execute({
    "operation": "text_to_image",
    "prompt": PROMPT,
    "aspect_ratio": "3:4",
    "seed": 1101,
    "project_id": "<slug>",
    "canon": True,            # route through staging → canon/visual/objects/
})

# Derived sheet view (hero as @Image1)
seedream.execute({
    "operation": "edit",
    "prompt": "Same person as @Image1, head turned 45 degrees to camera-left, "
              "identical lighting and background, identical hair and scar. " + PASSPORT,
    "image_paths": ["projects/<slug>/canon/visual/objects/<hero_sha256>.png"],
    "aspect_ratio": "3:4",
    "project_id": "<slug>",
    "canon": True,
})
```

`image_paths` are resolved strictly (inside the project root, no symlinks,
re-hashed immediately before upload). Order is meaning: the first path is
`@Image1`.

## Prompting

**Address every reference by slot.** "@Image1 is the face and hair — keep
them exact. @Image2 is the costume — keep garment category and colour." A
reference you upload but never mention is a reference the model is free to
ignore.

**Anchor lines that hold identity across views** (stack them):
- `the same person as @Image1, identical face, identical hair`
- `no change to age, build, skin, scar, eye colour`
- `identical lighting, identical background`
- `do not alter clothing category or primary colour`

**Negatives are literal lines**, not a separate parameter. Put each continuity
risk on its own "Never:" line at the end of the prompt. The model reads them
better as a short list than as a paragraph.

**Palette in every prompt.** Quote the hex hues verbatim. Seedream follows
named hex reasonably well; it follows "warm" and "moody" loosely.

**One change per edit call.** Turn the head *or* change the framing *or*
isolate the costume. Two changes in one call drift the identity.

**Expression grids**: ask for "a single image, 2x3 grid of the same face,
cells labelled by nothing, expressions: …". Do not ask for text labels — that
is typography and it will be wrong.

## What it must never do here

| Don't | Why |
|---|---|
| Render a title, logo, tagline, or any lettering | Typography is rendered locally from a licensed font (`title_card` tool). Glyph errors are documented; the plan forbids model text |
| Generate a "fresh" view with `text_to_image` after a hero is approved | Breaks the single-anchor strategy; the sheet must derive from the hero |
| Upload a photo of a real person as a reference | Synthetic-only cast; no likeness release path exists. Only receipt-bearing pipeline outputs are valid inputs |
| Exceed 10 references | Preflight fails; never trim silently |
| Pre-name or hand-copy an output into `canon/visual/objects/` | Only the wrapped tool writes there; the hash is the name |

## Iteration

1. Hero: 4 candidates at fixed framing, different seeds, same prompt. Present
   all four with the prompt; the human picks or rejects all.
2. Sheet: derive each view from the approved hero; when a view drifts, tighten
   the anchor lines and re-run that view — but the sheet is re-presented whole.
3. Storyboard frames and key art: sheets as references (hero + wardrobe per
   character, establishing per location), then the composition brief. Never
   more than 10 total.

## Sources

- fal.ai Seedream 5 Pro edit: https://fal.ai/models/bytedance/seedream/v5/pro/edit
- fal.ai Seedream 5 Pro text-to-image: https://fal.ai/models/bytedance/seedream/v5/pro/text-to-image
- Research brief: `docs/research/2026-08-25-visual-bible-claudex-research.md`
