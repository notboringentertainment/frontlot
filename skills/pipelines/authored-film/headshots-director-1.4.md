---
# Headshots Director — Authored-Film Pipeline (manifest 1.4)

## When To Use

Fourth stage (`headshots`), between `look_lock` and `visual_bible`, for a
project pinned to **authored-film 1.4**. (Projects on 1.2 / 1.3 use
`pipelines/authored-film/headshots-director`, which is frozen.) Everything the
1.2 director says about the stage's purpose still holds: one approved hero
per character in `proposal_packet.cast.character_ids`, chosen by the writer
through the selection gate, before any sheet or shot spend (D17), one
character at a time (D18). What changed in 1.4 is *who does the work*: the
stage is a command, and a machine judge checks every candidate before the
writer is asked (D20).

Read `pipelines/authored-film/canon-guard` first.

## The one instruction

```
python scripts/headshot_run.py --project <slug> --entity <entity_id> [--open]
```

- **Generate (default):** builds the hero prompt through `tools/prompt_builder`
  (role `hero`), generates candidates one paid call at a time under the signed
  hero budget (`project.yaml` 1.2 `qc.max_hero_attempts` per entity per look),
  judges every candidate with the `hero` checklist, keeps only unique passing
  candidates (up to `--candidates`, default 4), writes the pending
  `headshot_packet` 1.1 (each candidate carries `qc_receipt_id`), checkpoints
  `awaiting_human`, writes the selection-gate request, and stops.
- **Import (`--import <file> --origin-tool <name>`):** the writer's own
  generated image of the character. The command normalizes it, writes the
  `reference_import` gate request, and stops; after the writer signs it, the
  next invocation finalizes the import, **judges it like any other candidate**,
  and presents it alone at the selection gate.
- **Resume:** run the same command again after every gate. The command finishes
  exactly the request its run state names (`checkpoint_headshots.json`
  `metadata.run_state[<entity>]`) and refuses anything else.
- **Reject-all:** the writer declines at the gate with a note. The next run
  consumes that declined request, logs the note verbatim in the decision log,
  and regenerates with the **same** prompt against the same budget. A note
  asking for a different appearance is a look change: the command refuses and
  points to `look_run.py --supersede`.
- **`--replace`:** supersede an already approved hero (prints the downstream
  invalidation cost first).
- **`--grandfather`:** (1.3 → 1.4 migration only) judge the active legacy hero
  and write the `headshot_grandfather` attestation request. Required for every
  cast character before a 1.4 `pipeline_migration` can be signed.

Never assemble a packet, a candidate list or a gate request by hand; never
generate a candidate outside the command; never touch a casting-inspiration
image (that import happens in `look_lock` via `look_run.py --casting` and is
for the writer's eyes only). Present only what the run produced.

## The hero checklist (what the judge asks)

| id | question | severity |
|---|---|---|
| single_subject | Exactly one person, no second face or reflection? | fail |
| bust_front | A head-and-shoulders portrait facing the camera, both eyes visible? | fail |
| neutral_expression | A neutral or near-neutral expression, mouth closed or naturally parted? | fail |
| plain_background | A plain, uncluttered background with no scenery, text or props? | fail |
| no_occlusion | Face unobstructed: no hands, glasses, hat, mask, hair across the eyes? | fail |
| hair_matches | Does the hair match the described hair? | fail |
| age_matches | Does the apparent age fall in the described band? | warn |
| build_matches | Does the visible build match the described build? | warn |
| no_text | Free of text, watermark, logo? | fail |
| photoreal_or_treatment | Rendering style consistent within the candidate? | warn |

Local pre-checks (no model): PNG, square or portrait, long edge ≥ 1024, no
alpha. A local failure cannot be overridden; a judge failure can be accepted
item by item only through a signed `qc_override` at the gate.

## Acceptance at the gate

The gate handler re-verifies every candidate (lineage, look binding, sealed
recipe, import-receipt character binding, hero verdict chain within the
budget) and constructs the signed `headshot` record 1.1 itself — it seals the
generation (or import) receipt id and the `qc_receipt_id` it relied on. You
never author that record.

## Gate Reminder (Binding)

`human_approval_default: true`. Run the command, present its output, END YOUR
TURN.
