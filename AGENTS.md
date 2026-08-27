# OpenMontage

**MANDATORY: Read `AGENT_GUIDE.md` before responding to ANY user message.**

Do not act on the user's request until you have read AGENT_GUIDE.md.
It contains routing rules that determine your first action based on what the user asked.
Skipping it WILL cause you to take the wrong action.

There are no instructions in this file. All instructions are in AGENT_GUIDE.md.


<claude-mem-context>
# Memory Context

# [OpenMontage] recent context, 2026-08-26 9:23pm PDT

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (33,158t read) | 1,103,056t work | 97% savings

### Aug 25, 2026
12577 2:21p 🔵 Final infrastructure verification — reconcile_paid_calls, resume_check, generation receipt ledger, schema constants, and storyboard receipt enforcement confirmed implemented
### Aug 26, 2026
12683 8:17a 🔵 WriterOS promotion is store-validated, not externally signed; visual-bible uses approved_prompt_block hash-binding
12684 8:18a 🔵 Visual-bible supersession requires all three: canon_ruling decision, new objects, fresh receipt; stale assets must regenerate
12685 " 🔵 D10 plan: looks as wayfinder tickets, ratified by external promotion (OpenMontage receipt OR WriterOS export), three-slice rollout
12686 8:19a 🔵 WriterOS promotion events already discriminated; approval records computed for hashing, not stored
12689 8:21a 🔵 AGENT_GUIDE.md establishes Reviewer Protocol exception: auditing source is fair when verifying governance contracts or catching silent-availability bugs
12690 " 🔵 D10 plan has completed Round 1 Codex review; 18+ concrete flaws identified with specific fixes; plan document shows R2 revisions
12691 8:22a 🔵 gsd-plan-review-convergence skill exists; orchestrates external-AI replan loops until HIGH concerns resolved
12692 " 🔵 D10 plan revised through Round 2; 10 material findings including critical self-referential-hash blocker and schema gaps; all findings accepted
12693 8:23a 🔵 Pipeline manifest is authoritative for stage order; authored-film v1.1 lacks look_lock stage; version guards check exact equality
12694 " 🔵 Tool layer already threads visual_bible_entity_id through paid_call_context, reference_manifest, and local generators (title_card, poster_composite)
12695 8:24a 🔵 poster_composite enforces "local derivation launders nothing"—both inputs must have verified receipts before composition; plan can follow exact pattern for look_lock
12696 " 🔵 Gate/receipt architecture is designed for extensibility; look_lock approval kind and look_packet artifact schema are integration points
12697 " 🔵 Visual Bible schema is currently v1.0 only; plan proposes v1.1 with look_ref + sheet_revision, versioning infrastructure already in place
12714 8:33a ⚖️ D10 Plan Round 5 Final Review Initiated: Four Rounds of Adversarial Feedback Accepted; Only Critical Issues Flagged
12724 9:27a 🔵 Slice A′ design integrated after round 6: headshots stage with reference-image import and casting-inspiration protection
12725 9:28a 🔵 Checkpoint manifest contract enforces all declared artifacts at awaiting_human; Slice A′ design splits headshots across two statuses
12726 " 🔵 Approval gate system defined; new gate kinds (reference_import, headshot, look_lock) lack approval_record schema
12727 9:29a 🔵 Plan specifies three new gate kinds (look_lock, reference_import, headshot) in narrative form; approval_record structures embedded in stage descriptions
12810 10:36a 🟣 D10 Look Lock governance system implemented (Slice A + A′): ratified visual identity with receipted lineage
12811 10:42a 🔵 Headshot packet schema uses state-driven validation
12812 10:43a 🔵 Test suite scanned for round-7 fix coverage and canon-bypass vulnerabilities
12813 10:44a 🔵 Authored-film 1.2 pipeline manifest defines look_lock and headshots stage topology
12814 " 🔵 Diff 89d1bc0..30de836: 53 files changed across core libs, schemas, tests, tools, and skills
12815 " 🔵 Director skills document pipeline stage authority and canon enforcement
12816 " 🔵 Test coverage scan: negative tests partially present, critical patterns absent
12817 " 🔵 look_spec validation implements injection scanning and canonical hashing
12818 " 🔵 look_packet schema: look_spec entries bound by receipt_id and source_ticket_ref
12819 10:45a 🔵 gate_approve.py implements approval flow: selection for headshots, yes/no for others
12820 " 🔵 pipeline_loader.py: versioning model (bare=1.1, @version=variant), manifest_digest binding
12821 " 🔵 checkpoint_digest and predecessors enforce stage ordering and source integrity
12822 " 🔵 receipts.py: one-use gate token + WAL crash safety + HMAC-signed envelopes
12823 " 🔵 APPROVAL_KINDS whitelist: look_lock, headshot, reference_import, pipeline_migration new in D10 Slice A + A′
12824 " 🔵 look_spec schema: fictional_subject_attestation required + refused if false; minor flag blocks generation; shape_only flag blocks generation
12825 10:46a 🔵 image_selector and video_selector: provider ranking with preferred_provider override and selection_reason traceability
12826 " 🔵 headshot_packet approved_entry schema: origin enum, normalized_pixel_hash, import_receipt_id conditional requirement
12827 " 🔵 reference_import: source_name optional parameter in prepare_reference_import, recorded in import_record
12828 10:48a 🔵 Exact implementation review: lineage verification (DFS+seen), ledger recording, approval filtering, receipt construction
12829 " 🔵 Envelope validation, checkpoint tuple resolution, scene entity_free enforcement, ICC profile stripping
12830 10:49a 🔵 generation_sufficient() gate implementation: shape_only, minor, fictional_subject_attestation checks
12831 10:50a 🔵 Selector tools are transparent routers: no governance, no look_refs inspection; delegate to tool.execute() with adapted inputs
12832 " 🔵 seedance_video dual-path design: model_version 2.5 (governed) vs 2.0 (legacy); project.yaml gates to 2.5 only
12890 11:31a 🔵 Look-Lock D10 governance fixes: 15 findings disposition status post-implementation
12915 11:32a 🔵 Bootstrap Implementation Files Uncommitted—Blocks Deployment Despite Resolved Findings
13055 8:14p 🔵 Adversarial code review of D19 sheet QC implementation plan initiated
13056 8:20p 🔵 Pipeline versioning and look_lock stage infrastructure already implemented
13057 " 🔵 prompt_recipe and look_ref schema implementation verified in test project
13058 " 🔵 Partial predecessor logic and invalidation tracking implemented for per-entity flow
13064 8:27p 🔵 D19 Revision 3 Sheet QC Plan: Complete infrastructure blueprint reviewed
13072 8:37p ⚖️ D19 Sheet QC Plan Revision 5 Approved for Implementation

Access 1103k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>