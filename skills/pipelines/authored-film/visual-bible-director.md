# Visual Bible Director — Authored-Film Pipeline

## When To Use

Third stage (`visual_bible`), between `proposal` and `script`. You turn the
approved treatment's cast into **approved, hashed reference sheets** — one per
character and location named in `proposal_packet.cast` — plus a palette and a
poster. Everything downstream (`script`, `scene_plan`, `assets`, `edit`,
`compose`) reads this artifact; every generated shot must be conditioned on
these sheets and cite them. The sheets are **canon** (D2): content-addressed,
receipt-bound, superseded only by a logged ruling.

Read `pipelines/authored-film/canon-guard` first. It binds this stage exactly
as it binds the others: the writer wrote the characters; you are rendering them,
not redesigning them.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/visual_bible.schema.json` | Artifact validation |
| Prior artifacts | `canon_packet` (v1.1, entities carry `id`), `proposal_packet` (v1.1, `cast` + approved treatment) | Who to render, how it should look |
| Project config | `projects/<slug>/project.yaml` (validated by `schemas/project_config.schema.json`) | `budget_usd_cap`, `wall_time_minutes`, `cast_cap`, `provider_egress` |
| Tools | `seedream_image` (Seedream 5 Pro `text_to_image` + `edit`, up to 10 references), `flux_image` Kontext mode (fallback, ONE reference), `title_card` (local font rendering), local PIL/ffmpeg compositing | Generation |
| Layer 3 | `.agents/skills/seedream/SKILL.md` | Provider prompting guidance |

### Hard preconditions — do not start without all four

1. **`project.yaml` is present and its digest is approved.** The config is not
   self-attesting: its sha256 must match a `decision_log` entry of category
   `approval_policy` written through the human-gate path. A changed budget,
   cap, or egress block without a fresh approval is a rejected run, not a
   warning. If the digest has no approval, present the config at a gate and
   END YOUR TURN.
2. **`provider_egress` is recorded** (`{provider: fal, content_classes:
   [prompts, reference_images]}`). This is the writer's one-time acknowledgment
   that prompts and generated images leave the machine for FAL. Absent → the
   stage cannot start.
3. **The run lease is held.** `projects/<slug>/.run-lease` (pid, process start
   time, hostname, heartbeat). A second session fails fast; a live owner with a
   stale heartbeat blocks and reports — it is never overridden.
4. **Budget preflight passes.** Estimate the stage's paid calls (hero candidates
   + sheet views per character, establishing + angles per location, 4 poster
   candidates) against `budget_usd_cap` minus spend to date. Every paid call
   is preceded by a persisted cost reservation; a reservation that would exceed
   the cap fails preflight. You do not "try one and see."

Cast cap: `len(cast.character_ids) <= cast_cap.characters` and
`len(cast.location_ids) <= cast_cap.locations`. Over cap is a proposal defect —
send it back to the proposal gate; do not silently render a subset.

## The Approval Protocol (Binding)

Every sub-gate in this stage — each hero portrait, each sheet, each location,
the poster — is a human approval recorded as a **receipt**, and you never
record one yourself.

1. Write the stage checkpoint `awaiting_human` with the candidates (or the
   sheet) named in the summary and refresh `metadata.partial_progress`.
2. **END YOUR TURN.** Present the images (Backlot board or file paths) and the
   exact prompt that produced each one.
3. The human answers ("pick 2", "reject all, the jaw is wrong", "approve
   sheet").
4. The **gate handler** (human-gate CLI / Backlot) mints a one-use `gate_token`
   bound to `{project, stage, scope, record_sha256, expires}` and calls
   `record_human_approval(project_root, project_id, stage, scope,
   approval_record, gate_token, kind=...)`. That appends a signed receipt to
   `projects/<slug>/approvals.jsonl`. The token lives only in the handler; its
   HMAC lives under `~/.openmontage/gates/`, outside any directory you write.
5. You resume on the next turn: read the receipt id back, set the entity's
   `status: approved` and `approval_receipt_id` in the artifact, and continue.

There is no `approver` argument and no code path by which this skill sets an
approval. If you find yourself writing `status: approved` without a receipt id
that resolves in `approvals.jsonl`, stop — enforcement will reject it anyway.
Sub-gate receipts do **not** complete the stage; stage completion is still
`write_checkpoint(status="completed", human_approved=True)` after the final
gate, and enforcement additionally requires a receipt for every approved
ImageRef.

The receipt hashes the whole approved record (asset ids, roles, prompt block,
wardrobe negative, palette, pointer mapping) as RFC 8785 canonical JSON. Any
later edit to those fields — a "small" prompt tweak, a pointer swap — voids the
receipt. Re-present, re-approve.

## Process

### 1. Palette First

From the approved treatment's `visual_approach` (and the story bible's visual
style section when the canon packet carries one), fix `palette.hues[3..4]` as
hex values with one line of `notes` (e.g. "cold key, warm practicals, no
saturated red"). Write `canon/visual/palette.json`. Every prompt in this stage
quotes the hues verbatim; a prompt without the palette is malformed.

### 2. One Character at a Time — Hero Portrait

Work through `cast.character_ids` in order. **Never open a second character
while the current one has an unapproved hero portrait.** Never batch ahead.

Build the hero prompt from the canon packet entity, not from taste:

| Source field | Becomes |
|---|---|
| `continuity_facts` | Positive identity lines (age band, build, hair, marks, default costume) — quoted, not paraphrased |
| `behavioral_anchors` | Posture / expression baseline ("holds still, looks slightly past camera") |
| `visual_continuity_risks` | **Explicit negative lines**, one per risk ("no beard", "hair never braided", "no visible tattoo") |
| `never_write_as` | Negative lines too — if the packet says never write them as X, the portrait may not read as X |
| Treatment `visual_approach` | Lens, lighting key, texture, grade |
| `palette.hues` | Quoted verbatim |

Fixed framing for the hero: neutral front-facing bust, even key light, plain
background in a palette hue, no occlusion (no hands, hats, glasses unless canon
says so). Photoreal or stylized per the treatment — never mixed.

Generate **4 candidates** via `seedream_image` `text_to_image` (record seeds).
Present all four with the prompt. Human picks one or rejects all. Reject-all →
revise the prompt from their note and regenerate 4; count against
`max_revisions_per_stage`. Picked → the candidate becomes `characters[].hero`
with `kind: hero` receipt.

Example (invented placeholder, never a real character name):

```
Hero portrait, front-facing bust, neutral expression, even soft key light.
Subject: MARA VOSS — late 30s, wiry, close-cropped grey hair, thin scar
through left eyebrow, faded navy field jacket over black crew neck.
Posture: still, weight back, gaze slightly past camera.
Palette: #1B2A38, #C97B3A, #E8E2D3, #4A5A4E. Background: flat #1B2A38.
Photoreal, 85mm, shallow depth, muted grade, fine film grain.
Never: beard, jewellery, braided hair, tactical gear, smiling.
```

### 3. Derive the Sheet from the Approved Hero

Only after the hero receipt exists. Every sheet view is a `seedream_image`
`edit` call with the approved hero (and, once approved, earlier sheet views) as
references — never a fresh `text_to_image`. The hero is the identity anchor;
the sheet must agree with it, not compete with it.

Views, generated in this order, each with the hero as `@Image1`:

| Role | Prompt delta |
|---|---|
| `front` | Same framing as hero, full even light, confirms identity |
| `three_quarter` | Head turned 45°, same light |
| `profile` | True profile, same light |
| `full_body` | Head-to-toe, default costume, neutral stance, plain ground |
| `expressions` | Single grid (4–6 cells): neutral, alarmed, guarded, grief-held, one story-specific from `behavioral_anchors` |
| `wardrobe` | Default costume isolated on mannequin/flat lay, every garment named in `continuity_facts` |

Present the **six views as one unit**. The human approves the sheet or names
the views to redo. Partial approval does not exist: the sheet receipt (`kind:
sheet`) hashes all six asset ids together. One redo view → re-present the whole
sheet.

If a sheet view cannot hold identity from one hero (expression grids and
turnarounds are the known weak points on this stack), do not fabricate: present
the drift, propose approving 2–3 hero angles instead, and let the writer rule.

### 4. The Passport: `approved_prompt_block` and `wardrobe_negative`

With the sheet approved, write two strings on the character entry:

- **`approved_prompt_block`** — the verbatim identity paragraph that every
  downstream prompt copies unchanged: identity lines, costume lines, palette,
  the negatives. `shot_prompt_builder.py` pastes it; the asset director never
  rewords it. Treat it like a protected line: character-for-character.
- **`wardrobe_negative`** — the one-line negative for costume drift ("no
  hood, no gloves, jacket stays navy and open"), applied whenever the
  wardrobe reference is packed.

Both are inside the sheet receipt's hashed record. Editing either later means a
new approval.

### 5. Locations

Same discipline, per `cast.location_ids`, one at a time, after all characters
(a location prompt may cite character scale but characters are never in
location plates). Per location:

1. **Establishing** plate via `text_to_image`, prompt from `visual_identity`,
   `visual_continuity_risks` as negatives, treatment lens/light, palette hues.
   4 candidates → pick or reject-all → `kind: location` sub-approval of the
   establishing plate.
2. **2–3 angles** via `edit` with the approved establishing plate as `@Image1`
   (reverse, detail/texture, entrance or the story's key vantage from the
   annex). Presented as one unit; the location receipt hashes establishing +
   angles together.
3. `palette_override` only when the location legitimately departs from the
   project palette (the writer's bible says so) — and then the override is in
   the prompt and in the receipt.

### 6. Poster (after every sheet is approved)

Completed entirely inside this stage (§10 of the plan). **The poster is
required**: `visual_bible.poster` is a schema-required field and the stage
cannot complete until `poster.status: approved` carries a verified `kind:
poster` receipt. Three assets, each with its own receipt, plus one poster
approval:

1. **Key art** — `seedream_image` `edit` with the relevant approved sheets as
   references (hero + wardrobe of the featured characters, establishing of the
   featured location; respect the 10-reference cap), treatment palette, **no
   text of any kind in the prompt** ("no lettering, no logo, no title"). 4
   candidates → human picks one (`kind: poster` sub-gate on the key art).
2. **Title card** — rendered locally by the `title_card` tool from a **licensed
   font file** the writer supplies or the repo ships. Typography is never
   model-generated (D6): no Ideogram, no "add the title" edit pass, no
   in-model text. The tool is wrapped so it emits a `generator_kind: local`
   receipt (`tool`, `tool_version`, `parameters_hash`, `input_asset_ids`).
3. **`poster_final`** — composited locally by the `poster_composite` tool
   (`key_art_path` + `title_card_path`, `title_x`/`title_y`/`title_scale`
   layout inputs; PIL alpha composite, deterministic) into a content-addressed
   PNG with a `generator_kind: local` receipt whose `input_asset_ids` are the
   key art and title card hashes. **Both inputs must already carry verified
   generation receipts** — the compositor refuses an unreceipted file, so an
   imported image cannot be laundered into canon by compositing it.

Present key art, title card and final together; the poster receipt hashes all
three. `compose` consumes `poster_final` only as a title/end-card asset.

### 7. Storage — Content-Addressed Canon

- Every image is written **once** to
  `projects/<slug>/canon/visual/objects/<sha256>.png`. The tool wrapper stages
  under `.staging/`, re-encodes deterministically as PNG (metadata stripped),
  hashes the re-encoded bytes, then moves atomically. You never know the
  destination path before generation finishes — do not pre-name outputs.
- Human-readable names are pointer files:
  `canon/visual/characters/<id>/sheet.json`, `canon/visual/locations/<id>/sheet.json`,
  `canon/visual/poster/sheet.json` — each mapping `role → asset_id`. The
  artifact's `ImageRef.path` points at the object, `ImageRef.role` names the
  view.
- Every `ImageRef.provenance.generation_receipt_id` resolves to a line in
  `generation-receipts.jsonl` whose `output_sha256` equals `asset_id`. **No
  receipt, not canon.** This is the synthetic-only proof (D3): an image that
  did not come through a wrapped tool call cannot enter the bible, and a
  real-person photo has no path in.
- **Every provenance field is compared against the signed receipt**, not just
  the id and hash: `generator_kind`, `model_endpoint`, `prompt`, `seed` (when
  stated), and for local derivations `tool`, `tool_version`,
  `parameters_hash`, `input_asset_ids`. Write provenance from the tool's
  result (`data.prompt`, `seed`, `metadata`), never from memory — a prompt
  reworded after generation is a violation.
- Old objects are never deleted.

### 8. Supersession

An approved sheet is canon. Replacing any part of it (new hero, one redone
view, new palette) requires, in this order:

1. A `decision_log` entry `category: canon_ruling`, `question_id:
   visual:<entity-id>`, stating what changed and why — the writer's ruling,
   not yours.
2. New objects written; the pointer file updated; the previous entry marked
   `status: superseded` with `superseded_by` set.
3. A fresh approval receipt for the new record through the gate protocol.

Missing any one of the three → enforcement rejects the artifact. Downstream
assets generated against the superseded sheet are stale and must be regenerated
or explicitly re-approved by the writer.

### 9. Stage Gate Presentation

When every cast entity is approved, palette is written, and the poster is
approved: checkpoint `awaiting_human` with the full bible — per character:
hero + six views + passport text; per location: establishing + angles; palette
swatches; poster triptych; cost table (per entity, per call, total vs cap);
receipt ids. END YOUR TURN. On approval, `write_checkpoint(status="completed",
human_approved=True)`. Enforcement checks cast coverage, file existence + hash
match, approval and generation receipts for every approved ImageRef, palette
present, poster approved.

## Cost Discipline

- Each paid call: reservation persisted `submitting` before the network call,
  `X-Fal-No-Retry: 1` on the request, request id written on return,
  reconciliation in `finally`. If you resume and find a reservation in
  `submitting` with no request id, it is `indeterminate`: **halt and ask the
  human to reconcile against the FAL dashboard.** Never resubmit on your own.
- Budget the realistic count before the first call: per character ~4 hero + 6
  sheet (+ redo margin), per location ~4 + 3, poster 4 + local. Say the number
  at the config gate.
- Flux Kontext is the fallback only when Seedream is unavailable, and it takes
  one reference — a sheet derived on Kontext is single-anchor by construction;
  say so in the gate summary.

## Anti-Patterns (Each Is a Defect, Not a Style Choice)

- **References listed, never applied.** A generation record that names the hero
  as a reference while the prompt or payload never carried it. The EP
  spot-checks payloads against receipts.
- **Mixing consistency strategies.** A sheet where some views came from
  `text_to_image` and some from `edit`, or a hero from one model family and a
  sheet from another, or a LoRA sneaking in "for the hard view." One anchor,
  one strategy, per entity.
- **Generating video before sheets.** No `kling_reference_video` (or `seedance_video`) call exists in this
  stage. If a storyboard or motion test feels necessary to judge a design, that
  is the `assets` stage's storyboard sub-step, after this stage completes.
- **Self-approving.** Writing `status: approved`, inventing a receipt id,
  reusing a receipt for a changed record, or treating the writer's earlier
  "looks great" as covering the next sheet. Approval is per gate and per
  receipt.
- **Batching ahead.** Rendering the second character's candidates while the
  first hero is unapproved, or a sheet before its hero receipt exists.
- **Redesigning.** Adding a scar, a colour, a prop the packet does not name.
  Gaps are `open_questions[]` for the writer, never a value you fill in.
- **Typography from a model.** Any prompt that asks for the title, a logo, or
  lettering.
- **Pre-naming outputs** or writing into `canon/visual/objects/` by any path
  other than the wrapped tool.

## Gate Reminder (Binding)

`human_approval_default: true`, and every sub-gate is receipt-bound. Present and
END YOUR TURN.
