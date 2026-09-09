# Headshots Director — Authored-Film Pipeline

## When To Use

Fourth stage (`headshots`), between `look_lock` and `visual_bible` (manifest
1.2). A face is approved as its own stage before any sheet or shot spend
(D17). Per-entity flow (D18): a character whose look is ratified may have its
face generated and chosen while other cast members still have no look — the
`look_lock` checkpoint may be `in_progress` with a partial packet, and your
`pending` packet may carry a single character. Only characters PRESENT in your
packet need a current active look; `completed` still needs every character.
For every character in `proposal_packet.cast.character_ids` you get
**one approved hero**: either the writer's attested `imported_synthetic`
image, or one of up to four candidates generated from the ratified look. The
writer chooses through a selection gate; the gate handler writes the approval
record; you never do. Locations are not in this stage — their references are
handled inside `visual_bible`.

Read `pipelines/authored-film/canon-guard` first. The look packet is the
writer's decision about appearance; you render it, and you render nothing the
block does not say.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/headshot_packet.schema.json` (discriminated on `state: pending \| approved`) | Artifact validation at both `awaiting_human` and `completed` |
| Prior artifacts | `look_packet` (active `look_hash` per cast entity), `proposal_packet` (v1.1, `cast.character_ids`), `canon_packet` (v1.1) | Who, and what they look like |
| Prompt builder | `tools/prompt_builder` | Assembles the prompt from `look_spec` fields; emits `prompt_recipe {look_hash, builder_version, fields_used[], rendered_sha256}` |
| Tools | `seedream_image` `text_to_image` (Seedream 5 Pro); `flux_image` fallback | Candidate generation |
| Gate | `scripts/gate_approve.py` kind `headshot` — **selection mode** | The writer picks or rejects; the handler signs |
| Project config | `projects/<slug>/project.yaml` | `budget_usd_cap`, `provider_egress`, run lease, config digest — same four hard preconditions as `visual_bible` |
| Layer 3 | `.agents/skills/seedream/SKILL.md` | Provider prompting guidance |

Hard preconditions are identical to `visual_bible`: approved config digest,
`provider_egress` recorded, run lease held, budget preflight (4 candidates per
character without an imported hero, plus redo margin) against the cap.

## Why This Stage Exists

Sheets are derived by `edit` calls that take the hero as the identity anchor.
Every `visual_bible` character generation call **requires** `headshot_ref
{entity_id, asset_id, approval_receipt_id}`; the tool verifies the asset and
the receipt against the current `headshot_packet` before upload and binds the
ref into its generation receipt; enforcement rejects any sheet ImageRef whose
receipt lacks a matching `headshot_ref`. So without an approved headshot **no
sheet can be generated** — not by you, not by the visual bible director, not
by hand. A rejected candidate keeps its generation receipt as history but can
never satisfy `headshot_ref`.

## Process

### 1. One character at a time, in cast order

Work through `cast.character_ids` in order. **Never open a second character
while the current one has no approved hero.** Record progress in
`metadata.partial_progress` (which characters are approved, which are pending,
which receipt ids).

For each character, read its entry in `look_packet.looks[]`. Confirm the
receipt is the active tip for that key (`lib/look_ingest` exposes the check);
a retired or `shape_only` look stops the stage with the reason at the gate.

### 2. If an `imported_synthetic` hero exists, it is the single candidate

If the writer imported a synthetic image for this character during
`look_lock` (the look packet carries the import receipt id and
`origin: imported_synthetic`), do **not** generate. The imported object is the
one candidate: verify its `generator_kind: imported` receipt and
`attestation_receipt_id` resolve, then present it alone at the selection gate
(the writer confirms or rejects it). A rejected import means the writer wants
generated candidates — proceed to §3 with their note logged. The import is
never mixed into a generated set of four.

A `casting_inspiration` import is **not** a candidate and is never presented,
opened, or referenced here. If the only import for a character is
`casting_inspiration`, generate from the look as if there were no image.

### 3. Otherwise, build the prompt with `tools/prompt_builder`

Call `tools/prompt_builder` with the entity key and `look_hash`. It reads the
ratified block from the look packet and assembles the prompt from fields:
`prompt_safe_description`, `age_band`, `build`, `hair`,
`distinguishing_marks[]`, `default_wardrobe.pieces[]`,
`era_and_class_signals`, `negative_lines[]` and `continuity_risks[]` as
negatives, plus the fixed hero framing (neutral front-facing bust, even key
light, plain background in a project-palette hue, no occlusion) and the
treatment's lens/light/grade from `proposal_packet`. It escapes, rejects
provider-disallowed content and injection-shaped lines, and returns the
rendered prompt with a `prompt_recipe`.

**Never copy text from the look block into a prompt yourself, and never edit
the rendered prompt.** The recipe's `rendered_sha256` is what the receipt
seals; a hand-edited prompt fails the fidelity check and is a defect, not a
style choice. If the builder refuses (a name pattern, a disallowed line), the
block goes back to the writer through `look_lock` supersession — you do not
work around it by rewording.

Example of what the builder produces (invented placeholder, never a real
person or character):

```
Hero portrait, front-facing bust, neutral expression, even soft key light.
Subject: TESSA MARLOWE-QUINT — 40s, slight, cropped ash-blonde hair,
small healed burn on the right hand, charcoal wool coat over a grey
turtleneck. Era: 1970s civil service. Background: flat #2A2F36.
Photoreal, 85mm, shallow depth, muted grade, fine film grain.
Never: glasses, jewellery, smiling, uniform, tattoos.
```

### 4. Generate 4 candidates with `look_refs`

Four `seedream_image` `text_to_image` calls (record seeds), each governed
exactly like a `visual_bible`-stage call: `stage: headshots` is a
`visual_bible`-class stage for governance, so every call **must** carry
`look_refs: [{entity_kind: character, entity_id, look_hash}]` and can never be
entity-free. `paid_call_context` verifies the look ref against the active
receipt before any upload, binds `look_refs` and the `prompt_recipe` into the
signed generation receipt, and refuses a call whose prompt does not hash to
the recipe. Each output lands content-addressed under
`canon/visual/objects/<sha256>.png` with its receipt; you never pre-name
outputs.

Photoreal or stylized per the treatment — never mixed across candidates.

### 5. Write `headshot_packet state: pending` and stop

Append the character's entry to the pending packet: `{entity_kind,
entity_id, look_ref, prompt_recipe, candidates[1..4]: ImageRef}` with no
`hero`. Checkpoint `awaiting_human` with the candidates (or the single
imported candidate), the rendered prompt, seeds, cost against cap, and the
look hash. END YOUR TURN.

### 6. The writer selects — the handler signs

Selection happens only in `gate_approve.py` selection mode, from a real
terminal:

```
python scripts/gate_approve.py --project <slug> --request <id>
```

The handler reads the `pending` packet from the `awaiting_human` checkpoint,
verifies each preview file's bytes against the candidate asset hashes and
candidate membership, enumerates the candidates, and accepts `1..N` or
`reject-all` with a note. On a pick it **constructs the signed record itself**
— `{entity key, look_hash, chosen asset_id, normalized pixel hash, origin,
import receipt id, prompt_recipe hash, candidates_checkpoint_digest}` — and
appends the `headshot` receipt. You do not author, pre-fill, or summarize
that record; the handler's record is the approval. Headshot receipts form a
per-entity supersession chain: a replacement names `supersedes_receipt_id`
and retires the previous one atomically, and consumers accept only the unique
ledger tip.

Note the difference from every other gate you have used: elsewhere you write
the record and the human approves it; here the human's choice *is* the
record, so there is nothing for you to write.

### 7. Reject-all → regenerate, with the note logged

On `reject-all`, the handler records the note and the declined request. On
your next turn: log the note in the decision log (`category: revision`, the
writer's words verbatim), then rebuild the prompt through `tools/prompt_builder`
— the note may select different `fields_used` or change framing, but the
fields come from the ratified block, never from the note. If the note asks
for something the block does not say ("give her a scar"), that is a look
change: stop, and route it to `look_lock` supersession. Regenerate four,
write a new `pending` packet, stop again. Each reject-all counts against
`max_revisions_per_stage`.

### 8. Completion

After each approval, rewrite the packet as `state: approved` for the
characters approved so far and checkpoint `in_progress` — that character's
sheet may start in `visual_bible` now (D18). When every character in
`cast.character_ids` has a headshot receipt at the
ledger tip: rewrite the packet as `state: approved` with, per character,
`{entity_kind, entity_id, look_ref, hero: ImageRef, origin: generated |
imported_synthetic, import_receipt_id?, normalized_pixel_hash,
approval_receipt_id, candidates_rejected[]}`. Checkpoint `awaiting_human` with
the contact sheet (hero per character, receipt ids, cost table), END YOUR
TURN, then `write_checkpoint(status="completed", human_approved=True)` on
approval. Enforcement requires one approved hero per cast character whose
`look_ref.look_hash` is the current active look, an import receipt for every
`imported_synthetic` hero, and no lineage that touches a tainted hash.

## Invalidation

A `look_lock` retire receipt invalidates this stage's completed checkpoint and
everything downstream; a `headshot` receipt that supersedes a prior hero
invalidates `visual_bible` onward. Both are derived from the ledger on every
validation. Replacing a hero after sheets exist means regenerating every
sheet, storyboard, and take that descends from the old one — say the cost at
the gate before the writer chooses.

## Anti-Patterns (Each Is a Defect)

- **Writing the approval record.** Composing a `headshot` record, "selecting"
  a candidate in the artifact, or writing `state: approved` before the
  handler's receipt exists at the ledger tip.
- **Copying the look block into a prompt**, editing the builder's output, or
  adding a detail the block does not carry.
- **Generating when an imported hero exists**, or mixing an import into a
  generated set.
- **Touching a casting-inspiration image** in any way.
- **Calling without `look_refs`** or with a stale `look_hash`.
- **Batching ahead**: candidates for the second character before the first
  hero is approved.
- **Treating a rejected candidate as usable** downstream because it has a
  receipt.
- **Working around a builder refusal** by rewording.

## Gate Reminder (Binding)

`human_approval_default: true`. The hero is chosen by the writer in the gate
handler, never by you. Present and END YOUR TURN.
