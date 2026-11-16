# Compose Director — Authored-Film Pipeline

## Trailer scope

For a signed trailer, separately signed intertitles and an end card may be drawn from the saved edit. This overrides any later rule that titles may come only from poster assets. Use the exact signed words and licensed font file, size, presentation and whole-frame timing. Local title-tool lettering is placed by Remotion in the same preview; later export must consume this same timing and recheck the signatures. Never substitute lettering, change the wording, wrap or shrink it silently. Refuse changed or unsigned cards, missing fonts and lettering outside the safe margins. Approved poster assets are never changed; their existing final pinned requirements still apply. A preview or card signature completes no stage.

This section applies only to a production signed to the trailer definition, version 1.6. For that production, every rule in this section overrides conflicting later text, including any instruction to proceed without a gate or to treat an assistant-set approval flag as consent. All other later requirements still apply to trailers, including canon, look, portrait, reference and spending protections. Older productions keep their pinned stages, runtime, timing and approval rules. A marker alone cannot change the signed definition. Never migrate a production as a convenience.

The saved trailer contract states its target running time, at least one second and at most two minutes, and its two finishing approval points. Carry that same contract into the script, shot plan, edit, render report and final review. New trailer timing uses integer frames at twenty-four frames per second. Saved script, edit and export duration may exceed the target by at most one frame, and may never exceed two minutes. Changing an approved target requires fresh review of the affected stages; keep their old approvals as history. The saved edit is the timing authority for both preview and export; the editing piece supplies that timeline, rather than creating another story store here.

Use Remotion for local timed graphics and footage composition, and FFmpeg for trimming, colour, mixing, probing and final encoding. This fixed trailer workflow takes precedence over instructions below to offer composition runtimes. It does not establish that a runtime is installed, change an older renderer lock, or approve any creative choice for Ben.

Ben works through visible controls and ordinary conversation. Use only available Front Lot operations. Never give him a command to type, run an unavailable operation by another route, or mark a placeholder as produced footage. Say plainly when a control is not available yet and stop that step. The trailer is unfinished until those later pieces exist.

Every stage requires Ben's signed approval through the existing gate and receipt machinery. A bare approval flag, the assistant's words and a hand-written stage file never complete a trailer stage. Technical checks and the assistant's review never approve a stage. In Delivery, Ben uses “Review and sign the saved rough cut”, then “Make the final file”. After watching and listening, he uses “Review and sign final approval” for that exact exported file. “Show approved file in Finder” reveals the approved local file; nothing is uploaded. Earlier stage reviews and the separate story review remain binding. Other whole-stage signing screens that are not available, including looks, portraits and visual references, remain unfinished. The rough-cut signature binds the exact saved edit revision; final approval also binds the file and its measurements. Changed inputs require fresh downstream review while preserving history. Never invent a receipt.

Record matching edit revision and export content digests in the render report and final review. Default delivery is widescreen high-definition video, at twenty-four frames per second, encoded as an H.264 MP4 with stereo AAC sound. Verify the actual export, not an intermediate. A passing review leaves final approval waiting for Ben.


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

## Approval defaults for older productions only

Trailers do not use this default. Use Delivery for Ben's signed final-file approval. Existing earlier reviews and the separate story review remain required; no technical pass completes the stage.

`human_approval_default: false`, but a failed canon pass is presented
`awaiting_human` — a render that breaks canon never self-certifies.
