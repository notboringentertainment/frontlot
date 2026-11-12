# The trailer paper cut

A trailer starts as an unsigned draft. The app’s creation form records the name, target length and total ceiling, then opens a creation request in the existing signing rail. Signing creation selects the trailer production definition. It approves no stage, provider consent or paid work. The settings draft remains unsigned.

The editor saves beats, spoken text, cards and shots together. The temporary sequence contains labelled placeholders. It supplies timing for review and has no generated media. Finished-media editing and stage signing remain separate later work.

Each save preserves the previous version and refuses to overwrite a newer save from another window. A changed story, treatment, script or target makes the current draft require another look. Reading a previous version does not restore an approval.

## For the tools

The authoritative current file is `artifacts/edit_decisions.json`. `lib.trailer_edit.save_revision` writes immutable history under `artifacts/edit-history/` before atomically replacing the current file, under an interprocess lock. The caller must supply the current `edit_revision_sha256`, or `None` for the first save. `previous_revision` links versions; the digest excludes only its own field.

`timeline.fps` is 24. `timeline.picture` entries bind a stable shot `id`, `kind: placeholder`, `start_frame` and `duration_frames`. The intervals are half open. The paper cut lays these picture intervals consecutively. `timeline.cards` binds exact text, `source_ref`, `start_frame` and `duration_frames`. `timeline.audio` is empty because no sound exists yet. Planned spoken wording and frame timing remain in `paper_cut.lines`. Later cutting work must extend this frame timeline with approved media bindings and source offsets, rather than invent a second timing authority. Existing productions and their legacy timing contracts are unchanged.

`unfinished: true` and placeholder picture entries make `validate_stage_contract` refuse an edit-stage completion. The legacy `cuts` array stays empty. No asset manifest, generation receipt or completed checkpoint is written by this editor. Downstream trailer approval records include the current paper-cut revision when one exists.

The app routes are capability- and origin-protected `POST /api/trailers` and `POST /api/project/{project_id}/paper-cut`. Read-only version access uses `GET /api/project/{project_id}/paper-cut/{revision}`. The embedded assistant’s free operation is `trailer_paper_save`; its adapter snapshots the document and upstream files and dispatches `scripts.trailer_run`. The operation is listed only in trailer assistant instructions and cannot save an ordinary production.
