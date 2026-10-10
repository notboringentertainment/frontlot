# OpenMontage

**MANDATORY: Read `AGENT_GUIDE.md` before responding to ANY user message.**

Do not act on the user's request until you have read AGENT_GUIDE.md.
It contains routing rules that determine your first action based on what the user asked.
Skipping it WILL cause you to take the wrong action.

There are no instructions in this file. All instructions are in AGENT_GUIDE.md.


<claude-mem-context>
# Memory Context

# [OpenMontage] recent context, 2026-08-28 5:06pm PDT

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (34,375t read) | 1,325,733t work | 97% savings

### Aug 26, 2026
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
13109 9:23p ⚖️ Code review scope and governance framework initialization
13110 9:24p 🔵 QC-D19 post-build review preparation: plan, review history, and implementation diff gathered
13113 9:25p ✅ D19 code structure mapped: 18 modules indexed, symbols and line ranges extracted
13121 9:28p 🟣 D19: Config 1.3 gates legacy sheet roles and requires signed QC verdicts
13122 9:29p 🔵 D19: Config 1.1 multi-provider egress binding + QC block; manifest 1.3 dual-read with sheet_judge tool
13123 9:30p 🟣 Cost tracker atomic transactions + origin-binding immutability in reference_import and headshots
13124 " 🔵 Project directory routing: canonical PROJECTS_DIR root governs event attribution and checkpoint writes
13125 9:31p 🔵 _is_qc_manifest() gates QC requirement across sheet verification, gate approval, and sheet_run
13126 " 🟣 D19 implementation: shared verifier, pre-submit hook, qc_call_context, qc_override gate
13127 9:32p 🔵 D19.6: builder_policy_sha256 seals prompt_recipe to boundary builder's policy hash
13132 9:38p 🔴 Nine QC verification and provider idempotency findings addressed
13133 9:39p 🔵 D19 post-build inspection round 1 — commit 27a8807 implementation verified across nine findings
13134 " 🔵 D19 post-build inspection round 1 — all nine findings verified in production code
13135 " 🔵 D19 ledger and verifier implementation — findings #3, #6 confirmed at function level
13137 9:40p 🔵 D19 gate and judge implementation — all remaining findings verified in production code
13138 " 🔵 D19 cost tracking and attempt ledger — findings #6, #7 confirmed in reservation and event tracking
13139 " 🔵 Sheet run attempt recovery and verdict reuse — finding #6 complete implementation path
13145 9:41p 🔵 D19 Sheet QC implementation verified: all nine findings correctly deployed

Access 1326k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>