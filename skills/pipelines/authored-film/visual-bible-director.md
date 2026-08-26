# Visual Bible Director — Authored-Film Pipeline

## When To Use

Fifth stage (`visual_bible`), between `headshots` and `script` (manifest 1.2).
You turn the approved treatment's cast into **approved, hashed reference
sheets** — one per character and location named in `proposal_packet.cast` —
plus a palette and a poster. Everything downstream (`script`, `scene_plan`,
`assets`, `edit`, `compose`) reads this artifact; every generated shot must be
conditioned on these sheets and cite them. The sheets are **canon** (D2):
content-addressed, receipt-bound, superseded only by a logged ruling.

Two stages now precede you and you invent nothing they did not decide: every
cast entity's appearance is a ratified `look_spec` in `look_packet` (the
writer's wayfinder decision, sealed by a `look_lock` receipt), and every cast
character's face is the approved hero in `headshot_packet` (chosen by the
writer in the selection gate). This stage no longer generates hero portraits;
it derives sheets from the approved headshot and prompts from the look.

Read `pipelines/authored-film/canon-guard` first. It binds this stage exactly
as it binds the others: the writer wrote the characters; you are rendering them,
not redesigning them.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/visual_bible.schema.json` | Artifact validation |
| Prior artifacts | `canon_packet` (v1.1, entities carry `id`), `proposal_packet` (v1.1, `cast` + approved treatment), `look_packet` (active `look_hash` + receipt per cast entity), `headshot_packet` (`state: approved`, one hero per cast character) | Who to render, how it should look, whose face |
| Project config | `projects/<slug>/project.yaml` (validated by `schemas/project_config.schema.json`) | `budget_usd_cap`, `wall_time_minutes`, `cast_cap`, `provider_egress` |
| Prompt builder | `tools/prompt_builder` | Every prompt is assembled from `look_spec` fields; emits `prompt_recipe {look_hash, builder_version, fields_used[], rendered_sha256}` |
| Tools | `seedream_image` (Seedream 5 Pro `text_to_image` + `edit`, up to 10 references), `flux_image` Kontext mode (fallback, ONE reference), `title_card` (local font rendering), local PIL/ffmpeg compositing | Generation |
| Gate | `scripts/gate_approve.py` kinds `sheet`, `location`, `poster`, `reference_import` | Sub-gates; location reference import |
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
4. **Budget preflight passes.** Estimate the stage's paid calls (sheet views
   per character, establishing + angles per location, 4 poster candidates)
   against `budget_usd_cap` minus spend to date. Every paid call
   is preceded by a persisted cost reservation; a reservation that would exceed
   the cap fails preflight. You do not "try one and see."

Cast cap: `len(cast.character_ids) <= cast_cap.characters` and
`len(cast.location_ids) <= cast_cap.locations`. Over cap is a proposal defect —
send it back to the proposal gate; do not silently render a subset.

**Look and headshot prerequisites (runtime-enforced, not prose).** The
checkpoint writer runs `_check_look_packet` before this stage may be
`in_progress`: every cast entity has an active `look_lock` receipt at the
ledger tip, non-`shape_only`, non-minor; for `trailer` / `teaser` formats no
cast entity's active look is `spoiler: true`; every cast character has an
approved hero at the tip of its `headshot` chain. A missing or retired look, a
`shape_only` look, or a pending headshot stops the stage before the first
call. You do not work around any of these — the fix lives in `look_lock` or
`headshots`.

## The Approval Protocol (Binding)

Every sub-gate in this stage — each sheet, each location, each reference
import, the poster — is a human approval recorded as a **receipt**, and you
never record one yourself. (There is no hero sub-gate here any more: the hero
was approved in `headshots`.)

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

The receipt hashes the whole approved record (asset ids, roles, `look_ref`,
`headshot_ref`, `prompt_recipe`, wardrobe negative, palette, pointer mapping,
`sheet_revision`) as RFC 8785 canonical JSON. Any later edit to those fields —
a "small" prompt tweak, a pointer swap — voids the receipt. Re-present,
re-approve.

## Process

### 1. Palette First

From the approved treatment's `visual_approach` (and the story bible's visual
style section when the canon packet carries one), fix `palette.hues[3..4]` as
hex values with one line of `notes` (e.g. "cold key, warm practicals, no
saturated red"). Write `canon/visual/palette.json`. Every prompt in this stage
quotes the hues verbatim; a prompt without the palette is malformed.

### 2. One Character at a Time — The Hero Comes From `headshots`

Work through `cast.character_ids` in order. **Never open a second character
while the current one has an unapproved sheet.** Never batch ahead.

For each character, read its entry in `headshot_packet` (`state: approved`)
and copy `{entity_id, asset_id: hero.asset_id, approval_receipt_id}` into the
character's `headshot_ref`, and its `look_packet` entry into `look_ref
{entity_kind, entity_id, look_hash, receipt_id}`. Verify both resolve at the
ledger tip. The hero ImageRef on the character entry is the headshot packet's
hero, by reference — you do not generate one, and there is no `kind: hero`
receipt in this stage. `characters[].sheet_revision` starts at 1 and
increments on every supersession; the entity id never changes.

Prompts are not written from the canon packet any more. Every prompt in this
stage is assembled by `tools/prompt_builder` from the ratified `look_spec`
(description, age band, build, hair, marks, wardrobe, era signals,
`negative_lines[]` and `continuity_risks[]` as negatives), plus the view
delta, treatment lens/light/grade, and the palette hues. The builder returns
the rendered prompt and a `prompt_recipe {look_hash, builder_version,
fields_used[], rendered_sha256}`. **Never copy text from the look block by
hand and never edit the rendered prompt** — `paid_call_context` refuses a
prompt that does not hash to the recipe. The canon packet's
`behavioral_anchors` still inform the expressions view (posture, baseline
expression); `never_write_as` still applies as a review filter on what comes
back.

Example of a builder-rendered view prompt (invented placeholder, never a real
person or character):

```
Three-quarter view, head turned 45 degrees, same even soft key light as
@Image1. Subject: ORRIN HALE-BASKET — 60s, heavy-set, white beard cut short,
missing the tip of the left little finger, brown corduroy jacket over a
collarless shirt. Era: 1950s river trade. Palette: #1B2A38, #C97B3A,
#E8E2D3, #4A5A4E. Background: flat #1B2A38. Photoreal, 85mm, muted grade.
Never: hat, spectacles, clean-shaven, modern fabrics, smiling.
```

### 3. Derive the Sheet from the Approved Headshot

Every sheet view is a `seedream_image` `edit` call with the approved hero (and,
once approved, earlier sheet views) as references — never a fresh
`text_to_image`. The hero is the identity anchor; the sheet must agree with
it, not compete with it.

**Every sheet call carries `headshot_ref` and `look_refs` — enforced, not
prose.** The tool verifies `headshot_ref` (asset bytes + approval receipt)
against the current `headshot_packet` and each `look_refs[]` entry against
the active `look_lock` receipt before any upload, then binds both into its
signed generation receipt. Enforcement rejects a sheet ImageRef whose receipt
lacks a matching `headshot_ref`. A rejected headshot candidate has a receipt
but cannot satisfy this check; neither can any image that did not come
through `headshots`. `visual_bible`-stage calls can never be entity-free.

Views, generated in this order, each with the hero as `@Image1`:

| Role | Prompt delta |
|---|---|
| `front` | Same framing as hero, full even light, confirms identity |
| `three_quarter` | Head turned 45°, same light |
| `profile` | True profile, same light |
| `full_body` | Head-to-toe, `default_wardrobe`, neutral stance, plain ground |
| `expressions` | Single grid (4–6 cells): neutral, alarmed, guarded, grief-held, one story-specific from `behavioral_anchors` |
| `wardrobe` | `default_wardrobe.pieces[]` isolated on mannequin/flat lay, every piece the look names |

Present the **six views as one unit**. The human approves the sheet or names
the views to redo. Partial approval does not exist: the sheet receipt (`kind:
sheet`) hashes all six asset ids together. One redo view → re-present the whole
sheet.

If a sheet view cannot hold identity from one hero (expression grids and
turnarounds are the known weak points on this stack), do not fabricate: present
the drift, propose approving 2–3 angle views as additional anchors instead,
and let the writer rule. The hero itself is not replaced here — a different
face is a `headshots` supersession.

### 4. The Passport: `prompt_recipe` and `wardrobe_negative`

With the sheet approved, the character entry carries:

- **`prompt_recipe`** `{look_hash, builder_version, fields_used[],
  rendered_sha256}` — the identity paragraph's recipe, not its text. This
  **replaces** the former `approved_prompt_block` string. Downstream, the
  asset director rebuilds the paragraph through `tools/prompt_builder` from
  the same recipe and verifies `rendered_sha256` before use; nobody pastes or
  rewords a string. Fidelity to the ratified look is proven by the hash, not
  by copy discipline.
- **`wardrobe_negative`** — the one-line negative for costume drift ("no
  hood, no gloves, jacket stays navy and open"), built from
  `default_wardrobe` and `negative_lines[]`, applied whenever the wardrobe
  reference is packed.

Both are inside the sheet receipt's hashed record, alongside `look_ref`,
`headshot_ref` and `sheet_revision`. Editing any of them later means a new
approval.

### 5. Locations

Same discipline, per `cast.location_ids`, one at a time, after all characters
(a location prompt may cite character scale but characters are never in
location plates). Per location:

1. **Reference import, if the writer has one.** Locations have no `headshots`
   stage; the reference-image question was asked on the look ticket and the
   import happens here, through the same `reference_import` gate and the same
   two origin classes as characters (see `look-lock-director.md` §4, which
   binds verbatim). An `imported_synthetic` plate (attested machine-generated,
   `generator_kind: imported` receipt, normalized pixel hash) becomes the
   single establishing candidate and a lineage root. A `casting_inspiration`
   image (a real place photographed, a real building the writer is thinking
   of) is for the writer's eyes only: never opened, described, uploaded, or
   referenced by you; tainted project-wide. JPEG/HEIC/PNG; normalized and
   hashed by the import routine; the original is never copied in.
2. **Establishing** plate via `text_to_image` (unless imported), prompt from
   `tools/prompt_builder` over the location's look (`establishing_view`,
   `time_of_day_default`, `palette_anchors`, `architecture_or_terrain`,
   `dressing[]`, `weather_or_light_rules`, negatives), treatment lens/light,
   palette hues; `look_refs` on the call. 4 candidates → pick or reject-all →
   `kind: location` sub-approval of the establishing plate.
3. **2–3 angles** via `edit` with the approved establishing plate as `@Image1`
   (reverse, detail/texture, entrance or the story's key vantage from the
   annex), `look_refs` on every call. Presented as one unit; the location
   receipt hashes establishing + angles together with `look_ref`,
   `prompt_recipe` and `sheet_revision`.
4. `palette_override` only when the location's look `palette_anchors`
   legitimately depart from the project palette — and then the override is in
   the prompt and in the receipt.

### 6. Poster (after every sheet is approved)

Completed entirely inside this stage (§10 of the plan). **The poster is
required**: `visual_bible.poster` is a schema-required field and the stage
cannot complete until `poster.status: approved` carries a verified `kind:
poster` receipt. Three assets, each with its own receipt, plus one poster
approval:

1. **Key art** — `seedream_image` `edit` with the relevant approved sheets as
   references (hero + wardrobe of the featured characters, establishing of the
   featured location; respect the 10-reference cap), `look_refs` for every
   featured entity and `headshot_ref` for every featured character, treatment
   palette, **no text of any kind in the prompt** ("no lettering, no logo, no
   title"). 4 candidates → human picks one (`kind: poster` sub-gate on the key
   art).
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
  receipt, not canon.** This is the synthetic-only proof (D3): every lineage
  root is a pipeline generation receipt or an attested `imported_synthetic`
  import — nothing else. An image that did not come through a wrapped tool
  call or the import gate cannot enter the bible; a real-person photo has no
  path in, and a `casting_inspiration` hash anywhere in a lineage fails every
  gate.
- **Every provenance field is compared against the signed receipt**, not just
  the id and hash: `generator_kind` (`model` | `local` | `imported`),
  `model_endpoint`, `prompt`, `seed` (when stated), `look_refs`,
  `headshot_ref`, `references_applied`, and for local derivations `tool`,
  `tool_version`, `parameters_hash`, `input_asset_ids`. Write provenance from
  the tool's result (`data.prompt`, `seed`, `metadata`), never from memory —
  a prompt reworded after generation is a violation.
- Reference lineage is recursive: every local reference you pack must itself
  carry a verified receipt whose inputs are receipted, down to a root. Remote
  reference URLs are refused outright by the governed tools.
- Old objects are never deleted.

### 8. Supersession

An approved sheet is canon. Three different things can change it, and each
starts upstream of you when the change is upstream:

- **The look changed** — the writer reopened the wayfinder ticket and a
  `look_lock` `retire` receipt (tombstone or supersession) landed. That
  invalidates `headshots` onward; you cannot touch the sheet until a new look
  is ratified and a new hero approved.
- **The face changed** — a new `headshot` receipt superseded the old hero.
  That invalidates `visual_bible` onward; every sheet for that character is
  re-derived from the new hero as a new `sheet_revision`.
- **A view or the palette changed** with the same look and hero — the only
  supersession that begins in this stage.

In all three cases the sheet-level procedure is, in this order:

1. A `decision_log` entry `category: canon_ruling`, `question_id:
   visual:<entity-id>`, stating what changed and why — the writer's ruling,
   not yours.
2. New objects written; the pointer file updated; `sheet_revision`
   incremented (the entity id stays stable); the previous revision marked
   `status: superseded` with `superseded_by` set. Old objects stay on disk.
3. A fresh approval receipt for the new record (with the current `look_ref`,
   `headshot_ref`, `prompt_recipe`) through the gate protocol.

Missing any one of the three → enforcement rejects the artifact.

**Invalidation is derived from the approval ledger on every validation**, not
from anything you write: `retire` receipts mark the `visual_bible` checkpoint
carrying that `look_ref` / `headshot_ref` and every completed downstream
checkpoint (`script`, `scene_plan`, `assets`, `edit`, `compose`) written after
it as not completed until each is re-approved. `invalidations.jsonl` is a
rebuildable cache, never an authority. Say the downstream cost at the gate
before the writer retires anything.

### 9. Stage Gate Presentation

When every cast entity is approved, palette is written, and the poster is
approved: checkpoint `awaiting_human` with the full bible — per character:
hero (by `headshot_ref`) + six views + `prompt_recipe` + `wardrobe_negative`;
per location: establishing + angles; `look_ref` and `sheet_revision` per
entity; palette swatches; poster triptych; cost table (per entity, per call,
total vs cap); receipt ids. END YOUR TURN. On approval,
`write_checkpoint(status="completed", human_approved=True)`. Enforcement checks
cast coverage, file existence + hash match, approval and generation receipts
for every approved ImageRef, `look_ref.look_hash` equal to the active look for
every entity, a matching `headshot_ref` in every character sheet receipt, no
tainted hash in any lineage, palette present, poster approved.

## Cost Discipline

- Each paid call: reservation persisted `submitting` before the network call,
  `X-Fal-No-Retry: 1` on the request, request id written on return,
  reconciliation in `finally`. If you resume and find a reservation in
  `submitting` with no request id, it is `indeterminate`: **halt and ask the
  human to reconcile against the FAL dashboard.** Never resubmit on your own.
- Budget the realistic count before the first call: per character ~6 sheet
  views (+ redo margin; the hero was paid for in `headshots`), per location
  ~4 + 3 (0 + 3 with an imported plate), poster 4 + local. Say the number at
  the config gate.
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
- **Batching ahead.** Rendering the second character's views while the first
  sheet is unapproved.
- **Generating a hero here.** A `text_to_image` portrait in this stage, a
  sheet call without `headshot_ref`, or a sheet derived from a rejected
  headshot candidate "because it has a receipt".
- **Hand-written prompts.** Copying look text into a prompt, editing the
  builder's output, or writing an `approved_prompt_block` string — the
  recipe is the contract.
- **Redesigning.** Adding a scar, a colour, a prop the look does not name.
  Gaps go back to the writer through `look_lock` supersession, never a value
  you fill in.
- **Touching a casting-inspiration image** in any way, or packing any
  reference without a receipted lineage.
- **Typography from a model.** Any prompt that asks for the title, a logo, or
  lettering.
- **Pre-naming outputs** or writing into `canon/visual/objects/` by any path
  other than the wrapped tool.

## Gate Reminder (Binding)

`human_approval_default: true`, and every sub-gate is receipt-bound. Present and
END YOUR TURN.
