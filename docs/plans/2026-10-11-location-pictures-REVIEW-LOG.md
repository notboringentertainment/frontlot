# Plan review log: location pictures (G10, light)
2026-10-11. One Codex round by Ben's instruction (model gpt-6.1-sol, read-only, thread 01a12d72-1e27-7191-9dd3-26ebaff672ef).

## Round 1 — Codex (VERDICT: REVISE)
1. Editing the pinned 1.5 manifest changes its digest and forces a migration signature (pipeline_loader.py:88, pipeline_pin.py:228).
2. Signed imports must be finalized (reference_import.py:627) before sealing, or the entry lacks lineage (canon_enforcement.py:1933).
3. The run lease releases on exit; a run waiting on a signature is only visible through persisted metadata.run_state (sheet_run.py:275, :577).
4. Board can't place imports under a location from a label: hash scope (reference_import.py:525), roster characters-only (board.js:1755, :1802).

### Claude's response
All four accepted; 1 and 2 verified in code. Plan updated in place (marked "Codex R1#n"). No second round, per Ben.
