# Location pictures (G10), light version — plan

Date: 2026-10-11. Replaces the paused D21 plan (`docs/plans/2026-09-01-location-run.md`, branch `d21-location-run`, kept but not resumed).

## Goal

A location with a locked look gets approved reference pictures in the visual bible, signed in Front Lot, with no terminal and no new pipeline version, config re-sign or migration signature. First use: the Parlor (Ben's clean and aftermath images) and Sebastian's home (his two Midjourney images).

## What already exists (verified 2026-10-11)

- **Locked location looks.** `active_look_for(root, "location", id)` returns them. Bloodless has `parlor` and `sebastians-home`.
- **The visual bible already has a `locations` list.** Each entry has one establishing picture plus `angles`, a look reference and a status. It is checked by `lib/canon_enforcement.py`.
- **Quality-check verdicts are not required for locations.** Only character entries need them. That means no config change.
- **A `location` gate kind already exists** (`scripts/gate_approve.py:1063`, `location_approval_record`). It signs a location entry and checks its images.
- **Reference imports can already be tied to a location id** (`gate_approve.py` around line 520). The file lands in `canon/visual/objects/` with an `imported` lineage. That lineage is accepted later by supervised shots and by the asset stage.
- **Image roles are free text.** An extra picture can be labelled `aftermath` rather than forced into "angle".
- **Front Lot ops are a registry** (`backlot/claude_ops.py` `OPERATIONS`). Adding an op makes it available to Front Lot's Claude and the board.

## The one real blocker

The bible schema requires every location to have **2 or 3 angles** (`schemas/artifacts/visual_bible.schema.json`, `angles.minItems: 2`). A location with only an establishing picture, or with an establishing picture plus one aftermath picture, can't be saved.

**Change:** allow 0–3 extra pictures. The schema is not pinned by any signature. **The pinned manifest `authored-film@1.5.yaml` is NOT edited:** its whole file is hashed and bound by the migration receipt (`lib/pipeline_loader.py:88`, `lib/pipeline_pin.py:228`), so even a prose edit would force a new migration signature (Codex R1#1). Its "establishing plus angles" line stays; the unpinned director skill and the Front Lot brief carry the new wording.

## What gets built

### 1. Schema change

Set `angles.minItems` from 2 to 0 and update the unpinned `visual-bible-director.md` wording to match. Add a test that a location entry with zero extra views validates and a golden test that `authored-film@1.5.yaml` still hashes to its pinned digest.

### 2. `scripts/location_run.py`, import mode

```
location_run.py --project <slug> --entity <location-id> --add-image <path> [--label establishing|<free label>]
location_run.py --project <slug> --entity <location-id> --seal
location_run.py --project <slug> --entity <location-id> --finish
```

- **`--add-image`** files a reference import bound to the location, reusing `normalize_reference_import` and `publish_import_request` unchanged. Ben signs it. The signing question already asks whether the image shows real people. The first image becomes the establishing picture, and later images are extra views with Ben's label, for example `aftermath`.
- **`--seal`** first finalizes each signed, location-bound import with `finalize_reference_import` (moves the pixels into `canon/visual/objects/` and writes the imported generation receipt, which enforcement needs as lineage; `lib/reference_import.py:627`, `lib/canon_enforcement.py:1933`) and builds each image reference with `imported_image_ref`. Then it writes the pending bible entry with run state and files a `location` request bound to the checkpoint digest. Ben signs once and sees all of that location's pictures together (Codex R1#2).
- **`--finish`** absorbs the signed `location` receipt and marks the entry approved, following the same pattern as `sheet_run --finish`.

**Signatures for the Parlor:** two imports plus one seal, so three in all. That's one per picture plus one to lock the set. The import signature is kept because it is where the "no real people" promise is recorded. Dropping it would mean changing what the location gate checks.

**Refusals:**
- No locked look.
- The picture is not landscape or square, or its long edge is under 1024 px. This check is reused from the paused branch, by copying, not merging.
- A different run is open on the visual bible checkpoint. Two checks, both reused: the process lease (`lib/run_lease.py`) for runs that are executing right now, and the persisted `metadata.run_state` check `sheet_run` already uses (`scripts/sheet_run.py:275`) for runs waiting on a signature. A sheet waiting for signing blocks a location seal and the reverse, so neither rewrites the checkpoint under the other's signed digest (Codex R1#3).

### 3. Front Lot

- **New ops:** `location_add_image`, `location_seal` and `location_finish`. All three are free. They pass the same fingerprint flags the sheet ops pass (visual bible checkpoint digest and look hash), so no new kind of guard is invented.
- **Board:** add a "Location pictures" label for the `location` gate. A label alone can't place imports under a location, because imports are published with a hash-based scope (`lib/reference_import.py:525`) and the roster loads canon frames as characters only (`backlot/ui/board.js:1755`, `:1802`). So: the board state exposes each import's bound entity from its record, the roster resolves the location from that binding, and the location's pictures slot is filled from the bible's `locations` list (Codex R1#4).
- **`status` op:** list each locked location as "pictures approved", "pictures waiting to be signed" or "no pictures yet".
- **Brief:** add one paragraph telling Front Lot's Claude the order (add images, seal, finish) and that Ben's image files come from him. Claude never picks or edits them.

### 4. Going live

Merge, then follow the go-live rule:
1. Restart the server.
2. Restart Bloodless's Claude session.
3. Reopen the film.
4. Run `status` to show the Parlor and Sebastian's home as "no pictures yet".

Ben then adds his own images from Front Lot.

## Not in this build

- **Generating pictures for locations without images.** Every location that is locked today already has Ben's images. Ace's cleanup business and the DA's office have no look yet. Generation would be a later add-on (Seedream establishing candidates at about $0.07 each, then a Pick N choice on the signing screen) once a location needs it.
- **An AI quality check for location pictures.** It would need a config re-sign. Ben looks at every picture when he signs it.
- **Anchored angles, the poster, and completing the visual bible.** Completion still needs the two locations that have no look yet.
- **The paused D21 branch.** It is kept and not merged.

## Tests

- Unit tests with fake files, for each refusal and the full add → seal → finish path. The fixtures use generic names only.
- Full suite green, excluding the known browser-only test.
- Real run: the Parlor and Sebastian's home through Front Lot, by Ben. It's free.

## Open question for Ben

Q1. Where are the Parlor clean and aftermath image files? Sebastian's two images are in the Bloodless iCloud folder.

## As built (2026-10-11)

- `scripts/location_run.py`: `--add-image` / `--seal` / `--finish` / `--list`. The working list of added pictures lives in `<film>/.location-images/<id>.json`, outside the checkpoint, so adding a picture never disturbs a sheet waiting for signature. The location run state shares `metadata.run_state` with sheet_run, which means each blocks the other (tested both ways).
- **Changed from the plan:** the three Front Lot ops pass **no** fingerprint flags. They are free, like `sheet_finish` and `look`. The only frozen input is the snapshot of Ben's image.
- **Added:** the status op now says "signed by Ben but not finished" for a signed set that hasn't been finished yet. A test caught it reporting "waiting for Ben's signature".
- Board: places show two tabs, look and pictures. Imports appear under their place (the server sends `bound_entity` / `bound_kind`), approved pictures show with Ben's labels, and the location gate is titled "Approve location pictures".
- Front Lot's Claude copies Ben's file from his Story-drive folder into its work area, then calls `location_add_image`. Front Lot still has no upload button.
