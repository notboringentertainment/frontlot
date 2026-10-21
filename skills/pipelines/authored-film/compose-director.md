# Compose Director — Authored-Film Pipeline

## When To Use

Final stage (`compose`). You render the film, verify it technically, and run
the **canon pass** — the final check that what renders is what was written.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/render_report.schema.json`, `final_review.schema.json` | Artifact validation |
| Prior artifacts | `edit_decisions`, `asset_manifest` | The assembly |
| Optional | `scene_plan`, `canon_packet` | Canon pass source |
| Optional | `visual_bible` | `poster.poster_final` consumed only as a title/end-card video asset |
| Tools | `video_compose`, `audio_mixer` (+ optional stitch/trim/grade/enhance) | Rendering |

## Process

### 1. Render Per the Locked Runtime

The renderer family locked in the proposal is binding — a silent swap is a
CRITICAL governance violation, exactly as in the cinematic pipeline.

`video_compose` routes on `edit_decisions.render_runtime` (remotion,
hyperframes, or ffmpeg) — the value locked at proposal and carried through
edit unchanged. If the locked `render_runtime` is unavailable at compose time
(HyperFrames missing, Remotion not installed), that is a BLOCKER: surface it
per "Escalate Blockers Explicitly", get writer approval, and log a new
`render_runtime_selection` decision before rendering on anything else. Record
the runtime that actually ran in
`final_review.checks.promise_preservation.render_runtime_used` and set
`runtime_swap_detected` honestly.

### 1a. Poster Assets Are Cards, Not Deliverables

`visual_bible.poster.poster_final` enters the composition only as the title
card / end card still the edit placed. `render_report.outputs` stays
**video-only**: the poster PNG is not listed as an output, not re-exported, and
not re-rendered with different type. The poster ships alongside the film from
its canon path with its own receipts.

### 2. Standard Technical Verification

ffprobe validation, frame sampling, audio levels, subtitle checks, delivery
promise verification — the full standard battery.

### 3. The Canon Pass (final_review)

Beyond the technical review, `final_review` records a structured
`checks.canon_pass` object (see `final_review.schema.json`):

- **`locks[]`** — every lock id from the canon packet with a verdict
  (`honored` / `violated` / `uncertain`) and evidence.
- **`protected_lines[]`** — every protected line with `present_verbatim`
  (subtitle/transcript check) and `audible` (mix check).
- **`continuity_spotchecks[]`** — tracked characters/locations sampled across
  scenes for drift, judged against the approved `visual_bible` sheets (the
  hero and wardrobe views are the reference, not the previous shot).
- **`tone.must_never_feel_like_verdict`** — an honest sentence against
  `must_never_feel_like`. If the render feels like the thing it must never
  feel like, say so plainly; the writer decides whether it ships.
- **`status`** — `pass` only when every lock is honored and every protected
  line is verbatim and audible; anything else is `fail`.

Runtime-enforced on `completed`: `canon_pass.status` must be `pass`; every
lock `honored`; every protected line `present_verbatim` AND `audible`; every
tracked character and location spot-checked `consistent`; a non-empty tone
verdict; `unresolved_flags` empty. The checkpoint writer also verifies each
`render_report` output exists inside the project workspace and passes ffprobe
with a real VIDEO stream, duration matching the report (±15%), and matching
dimensions — an audio-only file is not a film. A failing canon pass is still
fully writable as `awaiting_human`: that is the escalation path, and the
structured (failing) canon_pass rides along so the writer sees exactly what
broke.

### 4. Production Record for the Writer

Offer the EP's handoff file: locks that governed, gate rulings, production
lines added, any canon changes — shaped for carrying back into the writer's
development system.

## No Gate By Default

`human_approval_default: false`, but a failed canon pass is presented
`awaiting_human` — a render that breaks canon never self-certifies.
