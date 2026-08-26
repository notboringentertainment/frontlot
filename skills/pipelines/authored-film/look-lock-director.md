# Look Lock Director — Authored-Film Pipeline

## When To Use

Third stage (`look_lock`), between `proposal` and `headshots` (manifest 1.2).
The approved proposal named a cast; nobody has yet decided what any of those
people or places **look like**. That decision is the writer's, and it is made
where all their other story decisions are made: a story-wayfinder ticket. Your
job is to get one resolved look ticket per cast entity, ingest each resolved
ticket into a `look_packet`, and write one `look_lock` gate request per entity.
You never write a look. You never fill a gap in one. You carry the writer's
ratified answer into the pipeline and stop.

Read `pipelines/authored-film/canon-guard` first. It binds this stage harder
than any other: a look is canon the moment its receipt exists, and "never
invent canon" here means never invent a face.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/look_spec.schema.json`, `schemas/artifacts/look_packet.schema.json` | Look block validation; stage artifact validation |
| Prior artifacts | `canon_packet` (v1.1, unchanged — this stage never bumps it), `proposal_packet` (v1.1, `cast {character_ids, location_ids}`) | Which entities need looks |
| Writer's system | The project's `wayfinder/` directory (MAP.md, `tickets/`, `resolved/`) | Where looks are written and resolved |
| Skill | `story-wayfinder` (user skill), procedure **"cast for production"** | Creating look tickets, the reference-image question, close rules |
| Library | `lib/look_ingest` | The one ingestion routine: ticket authority, block validity, `look_hash`, receipt verification, uniqueness/supersession |
| Gate | `scripts/gate_approve.py` kinds `look_lock`, `reference_import` | Ratification; reference import |
| Project config | `projects/<slug>/project.yaml` | `cast_cap`, run lease (already approved before this stage) |

No paid calls happen in this stage. Budget preflight is trivially satisfied;
the run lease and config-digest rules still apply.

## What A Look Is

A `look_spec` is a strict, versioned block (`version: "1.0"`, `oneOf` on
`entity_kind: character | location`), keyed by `(entity_kind, entity_id)` where
`entity_id` is the **bare canon_packet id, unchanged**. Common fields:
`source_ticket_ref`, `fictional_subject_attestation: true`, `minor: false`,
`prompt_safe_description` (20–80 words, present tense), `continuity_risks[]`,
`negative_lines[]`, `spoiler`, `depends_on[]`, `shape_only`. Characters add
`age_band`, `build`, `hair`, `distinguishing_marks[]`, `default_wardrobe`,
`wardrobe_variants[]`, `props[]`, `era_and_class_signals`, optional
`heritage_note`. Locations add `establishing_view`, `time_of_day_default`,
`palette_anchors[3..4]`, `architecture_or_terrain`, `dressing[]`,
`weather_or_light_rules`.

Three things are deliberately **not** in the block: ratification status, the
hash, and supersession. `look_hash` is sha256 of the RFC 8785 canonical JSON of
the validated payload and lives only in the `look_lock` receipt, together with
`supersedes_look_hash` and `promotion_refs[]`. A look is ratified when, and only
when, a signed `look_lock` receipt for its `look_hash` exists in
`approvals.jsonl` and no later receipt for the same key retires it. Nothing in
the ticket, the block, or the writer's other tools is proof.

## Process

### 1. Enumerate the cast

Read `proposal_packet.cast`. Every id in `character_ids` and `location_ids`
needs a look; nothing else does (D15 — no fan-out beyond the approved cast).
Confirm each id resolves to a `canon_packet` entity. An id that does not
resolve is a proposal defect: send it back to the proposal gate.

Work the list one entity at a time and record progress in
`metadata.partial_progress` so a resumed run knows which tickets exist,
which are resolved, and which receipts are minted.

### 2. Create or locate the wayfinder look ticket

For each entity, follow the **"cast for production"** procedure in the
`story-wayfinder` skill. In short:

- Look in `wayfinder/tickets/` and `wayfinder/resolved/` for
  `look-character-<slug>.md` / `look-location-<slug>.md` matching the entity.
  If a resolved one exists, skip to step 5.
- Otherwise create the ticket through the wayfinder procedure: `area: look`,
  `type: grill`, `mode: hitl`, a fresh immutable `id: wf-<8hex>`, blockers
  named by title in `blocked-by`. The PitchStudio export's `Non-canon casting
  notes` heading (if present) and the canon packet's `visual_continuity_risks`
  / `reference_assets_needed` are **hints for the Question only**.
- **You may draft the `## Question`. You never write the `## Answer` and never
  write a `## Look spec` block.** Drafting an Answer, pre-filling fields "for
  the writer to edit", or proposing "a starting look" is the same defect as
  writing a lock: the ticket is a decision, the writer decides it.

A legacy ticket (no `id:` field) is never edited — its content hash is a
memory key. Reference it by `{path, content_sha256}` in `source_ticket_ref`.

### 3. The reference-image question (asked while the ticket is drafted)

The wayfinder procedure asks the writer, per entity, **before the block is
resolved**: *Do you already have a reference image for this entity?* Record
the answer on the ticket. Three outcomes:

| Answer | Origin class | What it is |
|---|---|---|
| No | — | The look is words only; headshots will be generated from the recipe |
| Yes, generated elsewhere | `imported_synthetic` | A machine-generated image depicting no real person |
| Yes, a real person as casting inspiration | `casting_inspiration` | A photo of a real human being, for the writer's eyes only |

The question is asked now, not later, because any type field the writer
derives from an image (age band, build, era signals, hair category) must be in
the block **the writer resolves**. `lib/look_ingest` hashes the resolved
ticket as-is; nothing patches a field at approval time. If the writer wants to
change a field after seeing an image, that is a ticket edit before resolution,
or a supersession after (§7) — never an edit by you.

### 4. Reference import gate (only when the writer has an image)

Import happens in this stage, before ratification, through the gate kind
`reference_import`. Accepted formats: JPEG, HEIC, PNG. The import routine
normalizes deterministically (EXIF orientation applied, sRGB 8-bit, alpha
flattened for JPEG/HEIC sources and preserved for PNG, single frame only,
size limits enforced, re-encoded PNG with no metadata chunks) and hashes the
result — the `normalized_pixel_hash`. The original file is never copied into
the project.

Write the request under `.gate-requests/<id>.json` with the origin class the
writer stated and the attestation text they must confirm, then END YOUR TURN.
The two classes have different rules and you follow them exactly:

**`imported_synthetic`** — attestation: *"machine-generated, depicts no real
person."* On approval the import routine writes a generation receipt with
`generator_kind: imported`, `origin_tool`, `attestation_receipt_id`, and the
normalized hash, and stores the PNG under `canon/visual/objects/<sha256>.png`.
It is a legitimate **lineage root**: `headshots` will use it as the single hero
candidate; sheets and shots may descend from it. It is the one way an image
made outside the pipeline enters canon, and only with the signed attestation.

**`casting_inspiration`** — attestation: the writer attests it is inspiration
for a fictional subject. On approval the import routine stores the PNG under
`canon/visual/casting-inspiration/` (**outside** `objects/`) and the signed
receipt binds `{origin_class, normalized_pixel_hash, attestation_text,
attestation_receipt_id}`. From that moment the hash is in the project-wide
**taint set**, derived from the ledger on every validation. Binding rules:

- The image is **displayed to the writer only**, by the gate handler or the
  Backlot board. **You never open it, never pass its path or bytes to any
  tool or model, and never describe the face** — not in a ticket, a prompt, a
  gate summary, a decision-log entry, or your own reasoning. If a tool call
  would upload it, `paid_call_context` refuses before upload; you should never
  get that far.
- The writer types the allowlisted type fields into the block themselves
  (`age_band`, `build`, `era_and_class_signals`, `hair` as a category).
  Facial descriptors are not fields and must not appear in
  `prompt_safe_description`.
- A tainted hash can never be re-imported as `imported_synthetic`, never
  appear in a `reference_manifest`, never appear in any lineage. Identical
  pixels under any other class are refused. There is no procedure that
  un-taints a hash.

If the writer is unsure which class an image is, it is `casting_inspiration`.
If the writer wants to use a real person's photo as a generation reference,
the answer is no, with the rule quoted; there is no override and no gate that
grants one.

### 5. Wait for the writer to resolve the ticket

Resolution is a wayfinder session, not a pipeline turn: the writer writes the
`## Answer` with exactly one valid `## Look spec` fenced block and moves the
ticket to `resolved/`. Close rules (enforced by the wayfinder rubric and
re-checked by `lib/look_ingest`): block validates; `fictional_subject_attestation:
true`; `minor: false`; `spoiler` set deliberately; every `blocked-by` ticket
resolved. `shape_only: true` is a legitimate resolution for the writer but is
**never sufficient for generation** — a `shape_only` look cannot pass the
`visual_bible` prerequisite, so say so at the gate rather than discovering it
two stages later.

While any cast ticket is unresolved, checkpoint `awaiting_human` listing the
open tickets by path and END YOUR TURN. Do not resolve the ticket. Do not
suggest an Answer. Do not proceed to another entity's ratification "to save
time" — one entity at a time, in cast order.

### 6. Ingest and request ratification

For a resolved ticket, call `lib/look_ingest` with the ticket path. It verifies
authority (`type: grill`, `mode: hitl`, resolved), validates the block,
computes `look_hash`, derives `depends_on` from blockers, runs the
prompt-injection scan, checks key uniqueness against every active receipt, and
returns the validated payload. A failure is reported to the writer verbatim as
a ticket problem; you do not fix the ticket.

Then write the `look_lock` gate request: record = the validated payload;
summary shows the entity key, `look_hash`, `source_ticket_ref`, the writer's
`fictional_subject_attestation`, the refusal rule for real-person references,
and — when a reference was imported — the import receipt id and origin class
(never the casting image itself). `supersedes_look_hash` is set only in the
supersession path (§7). Checkpoint `awaiting_human` and END YOUR TURN.

The writer approves from a terminal (`python scripts/gate_approve.py --project
<slug> --request <id>`). On the next turn, read the receipt id back and append
`{entity_kind, entity_id, look_spec, look_hash, receipt_id, source_ticket_ref}`
to `look_packet.looks[]`.

### 7. Supersession

A ratified look changes only by: (1) the writer reopening the wayfinder ticket
(their reopening procedure, a new `id` if the old ticket is legacy), (2) a
`look_lock` receipt with `action: retire` for the old `look_hash` — a
tombstone the writer approves, which retires the active look until a new one
is ratified — and (3) a new `look_lock` receipt for the new hash naming
`supersedes_look_hash`. Invalidation is derived from the ledger: a retire
receipt invalidates `headshots` onward, and every completed downstream
checkpoint written after it is treated as not completed until re-approved.
Say that cost at the gate before the writer retires anything.

### 8. Spoiler looks and the trailer rule

`spoiler: true` marks a look that reveals a late-story fact (a scar acquired in
act three, a location's ruined state). The flag is the writer's, set
deliberately at close. It propagates into `look_packet` unchanged. When
`proposal_packet.runtime_shape.format` is `trailer` or `teaser`, `visual_bible`
**refuses spoiler cast**: if a cast entity's active look is `spoiler: true`,
say so at this stage's gate — the writer either changes the cast at the
proposal gate or resolves a non-spoiler look (a separate ticket for the
pre-reveal state). Do not strip the flag and do not edit the description.

### 9. Stage gate

When every cast entity has an active receipt: checkpoint `awaiting_human` with
the full `look_packet` — per entity: key, `look_hash`, receipt id, ticket path,
`spoiler` / `shape_only` flags, import receipt and origin class if any.
END YOUR TURN. On approval, `write_checkpoint(status="completed",
human_approved=True)`. Enforcement checks: one active non-`shape_only`,
non-minor receipt per cast entity; every `look_hash` recomputes from its block;
no tainted hash anywhere in the packet.

## Anti-Patterns (Each Is a Defect)

- **Writing the Answer.** Drafting a look spec, "suggesting fields", filling
  `heritage_note`, or converting `visual_continuity_risks` into a block. The
  writer resolves; you carry.
- **Patching at approval.** Editing a resolved block to make it validate, to
  add a field the writer forgot, or to fold in an image-derived detail. The
  ticket goes back to the writer.
- **Looking at the casting image.** Opening, reading, describing, thumbnailing,
  or passing a `casting_inspiration` file to any tool, model, or vision
  process. There is no legitimate reason for you to touch it.
- **Laundering.** Re-importing a tainted image as synthetic, copying a
  casting image into `objects/`, or attesting on the writer's behalf.
- **Self-ratifying.** Treating a resolved ticket, a WriterOS promotion, or the
  writer's "that's right" in chat as ratification. Only the signed
  `look_lock` receipt counts.
- **Fan-out.** Creating look tickets for entities outside `proposal.cast`, or
  at charting time.
- **Batching.** Requesting three ratifications while their tickets are still
  being argued, or proceeding past an unresolved blocker.
- **Editing legacy tickets.** Their hashes are memory keys.

## Gate Reminder (Binding)

`human_approval_default: true`. Every look is a receipt; every reference import
is a receipt. Present and END YOUR TURN.
