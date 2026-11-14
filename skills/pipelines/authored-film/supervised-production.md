# Supervised production

Use this route for Ben's supervised shot tests and revisions from established
story and visual material. Read the relevant provider's Layer 3 skill before
generation. The assistant handles the CLI and records; Ben directs and reviews.

## Prepare once, iterate together

Read the relevant Canon Note and workflow contract first. Story-drive is the
interface to story-wayfinder; ratified upstream decisions remain authoritative.
Use the existing file-based look handoff and active `look_lock` references.
Source resolution and production activation remain separate.

Show a compact shot brief: dramatic beat, relevant story facts, selected local
reference images, provider/tool and intended edit, spoiler limits, and cumulative
dollar/take allowance. Reuse Ben's settled directions. A storyboard is optional
for this supervised route. Unrelated cast, location, sheet or poster completion
does not gate it.

Record the agreed brief with `python -m scripts.supervised_shot PROJECT prepare
BRIEF.json --note 'Ben’s actual instruction'`. Brief fields are `shot_id`,
`direction`, `spend_allowance_usd`, `max_video_takes`, `allowed_tools`, `look_refs`,
`reference_manifest`, and `source_paths`. Include the Canon Note, workflow contract
and relevant resolved look tickets in source_paths. Reference entries use the
existing asset hash, local path, role and visual_bible_entity_id. Select approved
assets through existing Front Lot records; the brief is not an asset approval.

The brief records a conversational production allowance. It does not mint a
signed approval or replace project settings. Revisions keep the same shot_id,
so previously spent and outstanding money still count. A stopped shot requires
a new explicit instruction from Ben before preparing a resumed brief.

## Make the next useful thing

Translate the direction into a precise prompt and edit. Separate identity from
performance: keep the approved references while changing acting, framing or
timing. “Keep the first four seconds” means retain that range and revise the
remaining section; do not regenerate the useful part. Use existing local editing
tools to assemble selected sections into a new output, retaining source takes.

Use `request SHOT SETTINGS.json` to inspect exact tool inputs without submitting.
Settings contain prompt, a new output_path, and supported provider parameters.
`generate SHOT SETTINGS.json --tool kling_reference_video` executes one paid
call and attaches the result. It does not retry. Image calls also attach every
returned image to the notebook, bound to its existing receipt and reservation.

The helper uses `asset_class=supervised_shot`. This is a direct supervised call,
not a completed authored-film stage. Do not write fake checkpoints, label it
`shot_visual`, forge a receipt, or migrate the project to 1.6. Existing signed
stage contracts still apply when running the full pipeline.

For a starting image from the selected references, use Seedream with
`operation=edit`. Its supervised performance prompt need not be a prompt-builder
recipe or belong to a scene plan. Active-look, lineage, exact-reference and
allowance checks still run before upload. Selecting this image does not approve
a new identity or visual-bible asset.

The existing boundary verifies signed project configuration, egress, active looks,
reference lineage, and project funds. Reused reservation locking checks shot funds
and take count again at reservation. Unknown paid outcomes require reconciliation;
never automatically resubmit. `stop SHOT --note 'Stop'` stops further paid calls
for that shot. A user-wide stop means stop every active supervised shot and any
other paid work; the helper is not a global emergency cancellation service.

## Review and preserve

`attach SHOT FILE --note '…'` copies an external image or video without changing
the source. Include `--cost`, `--job-id`, and `--prompt` only when known. A genuine
completed local reservation can be linked using `--reservation-id`; it is verified
against the output. Missing metadata stays null. Imported unknown costs block
further spending within that shot; resolve them from provider records before
continuing. Attaching the same external file again with known cost updates its
accounting without deleting its earlier history.

`select SHOT TAKE_ID --start 0 --end 4 --note 'Keep the first four seconds'`
records a partial selection and shows it on the existing Decisions rail. Omit
start/end for a whole take. Retain rejected takes; record the rejection and
revision direction as the note on the next brief. No second take signature is
required. The notebook is in `production/shots/SHOT/history.jsonl`; generation
receipts and reservations remain in their existing locations.

`inspect SHOT` shows the brief, takes, selections, proposals and source warnings.
Check it before revising. Changed sources require reviewing affected material;
retain original snapshots and takes. Active look verification also refuses an old
look for a new paid call after production activation changes. This does not claim
automatic upstream propagation across every existing Front Lot asset.

## Canon and artistic judgment

Production notes about timing, expression and camera do not reopen canon. A new
ritual, identity, turning character or reveal order is an explicit story departure.
Ben may authorize an experiment; selecting its footage cannot ratify the change.
Use `propose SHOT --note '…'` to save unratified input in the project's notes/.
Route that input through the existing story-wayfinder ticket and Canon Note process.
Only a ratified upstream result returns as authority. This command does not submit
or resolve a wayfinder ticket. Preserve spoiler limits; a turning test is not
standing permission to reveal the guarded ending in publicity.

Aesthetic and continuity checks are advisory. Explain relevant concerns and let
Ben judge the result. A no-spend replay validates persistence, boundaries and the
ability to act on a revision. It cannot prove acting, timing, prompt translation
or artistic success. Broader cleanup and the paused 1.6 take/migration gates stay
deferred. Reuse the useful allowance implementation without reviving the branch.
