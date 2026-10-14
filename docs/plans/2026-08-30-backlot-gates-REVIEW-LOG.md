# Plan Review Log: Backlot Gates
Phases 0-1 (recon + interrogation) complete — plan locked with Ben 2026-08-30. MAX_ROUNDS=5. Reviewer gpt-5.6-sol.

## Round 1 — Codex (first attempt timed out at 10 min; resumed thread)
The plan is not implementation-ready. I found multiple contract and integrity failures; no files were modified.

1. **The WebSocket creates a programmatic self-approval path.** `require_tty()` only checks `isatty()`, and the spawned PTY supplies that; any local process can forge `Origin` and send `y`, contradicting the claim that pipeline code has no approval API ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:95), [gates.py](/Users/ben/Projects/OpenMontage/lib/gates.py:198)).  
   Fix: Keep signing in a user-owned native terminal, or explicitly revise the trust model and require OS-backed user presence; Origin checking is not proof of a human.

2. **`construct()` is not side-effect-free.** Several constructors call receipt readers that invoke `recover_pending_approvals()`, which can append `approvals.jsonl`, update ledgers/chains, and delete WAL state ([receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:375), [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:429)). A nominal GET can therefore write project and gate state.  
   Fix: Add a strictly read-only inspection API that disables recovery and mutation, and test it by snapshotting all project/gate files before and after reads.

3. **The documented headshot call is immediately wrong.** The plan calls `headshot_candidates(root, req)`, but the function signature is `headshot_candidates(req, root)` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:26), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:798)).  
   Fix: Correct the argument order and add a direct state-builder test using the real function.

4. **A digest cannot be shown for every pending request.** Normal headshots require a human selection before `construct()` succeeds, while `qc_override` requires the human’s typed reason ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1016), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:722)).  
   Fix: Return `record_sha256: null` for input-dependent gates and treat the digest printed after terminal input as authoritative.

5. **The board’s displayed digest can become stale before signing.** State construction and terminal construction occur at different times, and the watcher debounce provides no atomic snapshot.  
   Fix: Label board packets as snapshots and revalidate an expected dependency digest at terminal start, refusing stale packets.

6. **`receipt_lock` does not protect checkpoints from a running pipeline.** It serializes approval receipts only; `.run-lease` writers can change checkpoints/config/tickets during display or even after a pre-commit check ([gates.py](/Users/ben/Projects/OpenMontage/lib/gates.py:600), [run_lease.py](/Users/ben/Projects/OpenMontage/lib/run_lease.py:137)). “Approve-while-running is legal” is therefore unsafe.  
   Fix: Make runners and the signer share a project-wide snapshot/run lock across display through commit, or refuse signing while a live run lease exists.

7. **Project and request confinement is insufficient.** `_safe_project_dir()` accepts symlink projects and names outside the canonical slug grammar; `load_request()` does not require `request.project_id == root.name`, allowing cross-project receipts for some kinds ([server.py](/Users/ben/Projects/OpenMontage/backlot/server.py:310), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:104), [run_common.py](/Users/ben/Projects/OpenMontage/lib/run_common.py:30)).  
   Fix: Resolve through `run_common.resolve_project_root()`, reject symlink request directories/files, load the request before spawning, and require matching project ID, filename, and unique state.

8. **The copied gate command can be shell-injectable.** `gate_command()` interpolates `root.name` and the request ID without quoting, while the planned state eligibility only requires `project.yaml` and does not enforce the slug grammar ([run_common.py](/Users/ben/Projects/OpenMontage/lib/run_common.py:67)).  
   Fix: Validate both values and build the displayed command with `shlex.join()`.

9. **Archived gates cannot safely use live `construct()`.** Constructors commonly require the current checkpoint to remain `awaiting_human`; done gates later point at completed or superseded state, while `approval_receipt_id` in the request is only an unsigned marker ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:279)).  
   Fix: Render done gates from an exact verified receipt plus immutable decision-time evidence, never by reconstructing current state.

10. **The visual packet map is incomplete.** `APPROVAL_KINDS` also includes `hero`, `location`, `poster`, `storyboard_batch`, `artifact_review`, and `headshot_grandfather`, none of which has a defined board packet despite the “every approval” goal ([receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:54)).  
    Fix: Define an exhaustive renderer map with a generic safe fallback and assert its keys equal `APPROVAL_KINDS`.

11. **SSE refreshes will destroy or orphan the terminal UI.** Every refresh calls `app.innerHTML = ""`; signer writes trigger the watcher and therefore a refresh while the terminal is active ([board.js](/Users/ben/Projects/OpenMontage/backlot/ui/board.js:1054), [board.js](/Users/ben/Projects/OpenMontage/backlot/ui/board.js:1128)).  
    Fix: Mount xterm in a stable DOM subtree with explicit lifecycle ownership, and test an SSE change during an active session.

12. **Immediate SIGHUP on socket loss can corrupt the decision lifecycle.** A disconnect after receipt commit but before moving the request to `done/` leaves non-sheet bound requests permanently refused as duplicates and may duplicate unbound approvals ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1343)).  
    Fix: Make request completion recoverable for every approval kind before adding disconnect termination, then use a grace period followed by process-group TERM/KILL.

13. **`pty.fork()` is a poor fit for this threaded server.** Backlot already runs work in threads; forking a multithreaded Python process risks inherited locks and fragile child setup.  
    Fix: Use `pty.openpty()` with `subprocess.Popen(..., start_new_session=True)`, explicit binary/control framing, bounded resize values, backpressure, and process-group cleanup.

14. **“One PTY per project” is underspecified and not a security lock.** Simultaneous handshakes can race unless reservation is atomic, multiple server processes get separate registries, and native-terminal signers bypass it; an HTTP 409 is also not a normal post-upgrade WebSocket response.  
    Fix: Reserve under an application-scoped async lock before any await, enforce a single-worker invariant or interprocess mutex, and specify a WebSocket denial/close-code contract.

15. **Terminal output is an injection surface.** Request summaries, paths, and evidence are printed into xterm; control/ANSI/OSC sequences can rewrite prompts or create deceptive output.  
    Fix: Strip unsafe control sequences from all untrusted terminal output or configure a restricted renderer with explicit tests for ANSI/OSC payloads.

16. **The promised canon “look” row does not exist.** `plan_view()` returns hero, sheet roles, locations, and poster targets, but no look image ([canon_view.py](/Users/ben/Projects/OpenMontage/lib/canon_view.py:47)).  
    Fix: Remove “look” from the strip contract or derive and validate it through a separate authoritative look projection.

17. **The proposed “next command” duplicates orchestration rules.** `run_common` deliberately leaves each run’s prerequisites in its own command, so guessing look → headshot → sheet in `state.py` will recommend invalid commands ([run_common.py](/Users/ben/Projects/OpenMontage/lib/run_common.py:1)).  
    Fix: Show only validated pending `gate_command()` values, or have runners persist an explicit next-command hint after their own validation.

18. **Timeout, scale, and observability are unresolved.** A timeout around threaded `construct()` cannot cancel the worker; rebuilding every pending and archived packet on each SSE refresh can accumulate blocked work, while detached Backlot discards stdout/stderr ([backlot/__main__.py](/Users/ben/Projects/OpenMontage/backlot/__main__.py:38)).  
    Fix: Put bounded summaries in `/state`, load one request’s detail lazily, add idle/session limits, log lifecycle events outside project directories, and expose child exit reason without logging keystrokes.

The substantially simpler safe design is a read-only visual Gates panel with a validated “Copy/Open in Terminal” command. If embedding is non-negotiable, the PTY should be a separately authenticated broker rather than part of Backlot’s read-only state server.

VERDICT: REVISE
### Claude's response (round 1)
Verified: headshot_candidates(req, root) order; verified_approvals/find_approval replay the WAL (receipts.py:414/439/507 write); headshot/qc_override records need human input before a digest exists; runners release the lease when they block (hold_lease context exits on Blocked); plan_view has no look image; gate_command unquoted; board.js clears innerHTML on refresh; `open` discards stderr.
Accepted (17): raw-file state layer with no construct()/receipt-reader calls + immutability hash test; digest null in packets; snapshot labelling; refuse signing under a live lease; resolve_project_root + request validation before spawn; shlex.join for the command; archived gates from markers + raw ledger rows; exhaustive renderer map asserted against APPROVAL_KINDS; stable terminal mount outside the refreshed subtree; child survives disconnect, idle timeout + process-group TERM/KILL; openpty + Popen(start_new_session); async-lock reservation, single worker, WS close codes; output sanitiser; look as text only; next_command = pending gate commands only; lazy detail, bounded summaries, server log outside projects.
Partially accepted (1): #1 trust model. Rejected "OS-backed user presence" as over-engineering for a single-user localhost tool; adopted a per-launch secret in the opened URL as the thing that makes the pty the user's, and stated the trust model explicitly in the plan and in the two docstrings. Codex's simpler alternative (read-only panel + copy command) is logged; embedding was Ben's D1 choice.

## Round 2 — Codex
Revision 2 addresses most prior findings: read-only state derivation, lazy packets, null digests, exhaustive kind coverage, canonical project resolution, stable terminal mounting, `openpty()`, sanitization, logs, and removal of guessed run commands.

Material issues remain:

1. **The run-lease race is not closed.** Checking that `.run-lease` is absent before spawning is TOCTOU; a runner can acquire it immediately afterward, while `receipt_lock` still does not protect checkpoints ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:121)).  
   Fix: Have the signer or a signer wrapper acquire and hold the actual run lease through display and commit, which requires expanding the stated scope.

2. **Displayed visuals are not reliably the evidence being signed.** Sheet/QC/generic renderers trust `preview_paths` and raw rows, whereas `construct()` signs authoritative checkpoint and receipt data; a request can therefore show different pixels or QC than the terminal validates ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:83)).  
   Fix: Derive every visual from the same authoritative refs used by its constructor and verify each file hash against its `asset_id`; do not use generic `preview_paths` for actionable evidence.

3. **The token-launch design does not work with an already-running detached server.** `backlot open` is a separate process and cannot place a newly generated token into the existing server’s memory without IPC or a registration endpoint ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:24)).  
   Fix: Specify server-owned token creation plus protected IPC/0600 token handoff for subsequent `open` calls.

4. **The query-string token leaks unnecessarily.** It can enter browser history, referrers, diagnostics, and copied URLs; CSP is still only an open question despite the signing capability ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:26), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:240)).  
   Fix: Deliver the token in the URL fragment, scrub it immediately, and require CSP plus `Referrer-Policy: no-referrer`.

5. **The secret still does not prove human presence.** It blocks drive-by browser access, but a same-user local process can obtain or drive it; calling this what makes the PTY “the user’s terminal” overstates the guarantee.  
   Fix: Describe it as CSRF/capability protection only, or add OS-backed user authentication if actual human presence is required.

6. **The single-port claim is false.** Two Backlot servers can use different `BACKLOT_PORT` values and each maintain its own in-memory PTY registry ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:144)).  
   Fix: Use an interprocess `flock` outside project directories keyed by project ID, with the in-memory registry only as a local optimization.

7. **Mid-commit termination remains possible.** Disconnect is improved, but server shutdown and detached-session timeout still TERM/KILL the signer; non-sheet request completion is not recoverable after receipt commit but before the move to `done/` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:139), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1343)).  
   Fix: Make request completion crash-recoverable for every approval kind before allowing forced termination.

8. **Nested symlink and ticket traversal remain underspecified.** Skipping symlink files does not reject symlinked state directories, and rendering `source_ticket_path` directly can escape the Wayfinder root ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:54), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:89)).  
   Fix: Reject symlinks at every directory component and resolve look tickets through the existing `confine_ticket_path()` contract.

9. **`gate_command()` quoting is still ambiguous.** The current value is `cd … && python …`, not one argv that can simply be passed to `shlex.join()` ([run_common.py](/Users/ben/Projects/OpenMontage/lib/run_common.py:67)).  
   Fix: Introduce a shared `{cwd, argv}` gate invocation helper and render it as quoted `cd` plus separately quoted argv.

10. **The central integration remains untested.** The plan explicitly uses only a fake signer, so it never proves that the real script passes both TTY checks, enters `handler_context`, prompts correctly, and completes the request/receipt transition ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:174)).  
    Fix: Add one hermetic real-signer PTY test using temporary project and gates directories.

11. **The escape filter needs streaming semantics.** CSI/OSC and multibyte UTF-8 sequences can span PTY reads, bypassing or corrupting a stateless per-chunk filter.  
    Fix: Require an incremental parser/decoder and tests that split hostile sequences at every byte boundary.

VERDICT: REVISE
### Claude's response (round 2)
Accepted 10: lease held by a new `scripts/gate_sign.py` wrapper (closes the TOCTOU without the server writing); visuals derived from checkpoint entries / QC rows with content-hash verification, `preview_paths` never loaded as evidence; server-owned token file (0600) so `open` works against a running server; fragment delivery + scrub + CSP + Referrer-Policy; trust wording downgraded to capability/CSRF protection; interprocess flock per project; symlink check on every component + `confine_ticket_path()`; `gate_invocation()` {cwd, argv} helper; hermetic real-signer pty test; incremental sanitiser with split-boundary tests.
Reshaped 1 (#7): rather than make every kind crash-recoverable (out of scope), the server never sends a signal: idle/shutdown writes `q` (gate_approve's own quit answer), waits, then detaches. Crash-safety is exactly today's Terminal behaviour.

## Round 3 — Codex
Revision 3 resolves most round-2 findings, but four material problems remain:

1. **`gate_invocation()` has contradictory contracts.** Lines 75–76 return `gate_approve.py` directly, while lines 137–144 require it to return `gate_sign.py`; following the former bypasses the run-lease fix ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:74)).  
   Fix: Make `gate_invocation()` return only the wrapper argv, with a separate private helper for the wrapper’s child command.

2. **Sending `q\n` is not harmless at every prompt.** At optional-note prompts, `q` becomes the note and approval proceeds; at the reject-all note prompt, it can complete the rejection ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1461), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1490)).  
   Fix: Never inject prompt input automatically; add an explicit out-of-band safe-cancel protocol or leave the signer reconnectable indefinitely.

3. **The claimed shutdown detachment cannot work with the server owning the PTY master and flock.** When the server exits, its master FD and lock close; the slave receives hangup/EOF and another server can start a second signer while the first wrapper may still exist ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:148), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:162)).  
   Fix: Make `gate_sign.py` a persistent broker that owns the PTY master and interprocess lock, with Backlot connecting over IPC, or abandon survival-on-server-shutdown and make completion crash-recoverable.

4. **The visual contract remains incomplete.** Headshot candidates are not explicitly hash-checked against their exact candidate paths, while `hero`, `location`, `poster`, and `storyboard_batch` deliberately show no images—contradicting the goal that every visual approval is judged visually ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:100), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-30-backlot-gates.md:107)).  
   Fix: Hash-check every headshot’s exact authoritative path and implement authoritative image extraction for every genuinely visual approval kind.

VERDICT: REVISE
### Claude's response (round 3)
All four accepted: gate_invocation() returns only the wrapper argv; no software ever types into the signer (q is unsafe at note prompts) — sessions stay reconnectable while the signer lives; gate_sign.py becomes the broker that owns the pty master, run lease and flock, listening on a 0600 unix socket, with the server as a re-attachable client (survives server shutdown by construction); headshot candidates hash-checked against their exact packet paths; hero/location/poster/storyboard_batch get per-kind authoritative extractors with fixture tests, text-only fallback limited to headshot_grandfather/artifact_review.

## Round 4 — Codex
The four round-3 findings are addressed in intent, but Revision 4 introduces material broker gaps:

- **Visual/signing TOCTOU:** the packet is displayed before the broker acquires the lease, so a checkpoint can change before signing and candidate numbers/assets can diverge. **Fix:** acquire the lease first, then refetch the packet and enable terminal input only after that refresh completes.
- **Foreground lease leak:** “execs `gate_approve.py`” bypasses `Lease.__exit__`, leaving `.run-lease` behind after exit. **Fix:** spawn and wait for `gate_approve.py` inside the lease/flock contexts rather than `exec`.
- **Undefined IPC contract:** a Unix stream cannot distinguish raw input from resize/control messages, and the slug-only socket provides no verified request identity. **Fix:** specify length-prefixed typed frames plus a broker `HELLO` containing project, request ID, session ID, and status; reject mismatched attaches.
- **Broken startup/stale-socket recovery:** “spawn if no socket exists, then connect” races broker bind and cannot recover a socket left by a crash. **Fix:** add bounded readiness retries and broker-side stale-socket removal under the project flock, with concurrent-start and stale-socket tests.
- **Contradictory invariants remain:** the plan still says the server owns the PTY, an idle timeout bounds orphans, and only `gate_approve` writes—each conflicts with the new persistent broker. **Fix:** update those contracts to state that `gate_sign.py` owns the PTY/lease, detached sessions have no timeout, and the wrapper writes only `.run-lease`.

VERDICT: REVISE
### Claude's response (round 4)
All five accepted: input enabled only after the broker reports lease_held and the server re-fetches the packet (packet_refreshed); foreground mode waits for gate_approve inside the lease/flock contexts (no exec); typed length-prefixed IPC frames with HELLO {project, request_id, session_id, pid, status} and mismatch refusal; stale socket unlinked under the flock before bind, client attach retries with backoff, concurrent-start test; contradictory sentences fixed (gate_sign.py owns the pty/lease/flock; no timeout; writers inside a project are gate_sign.py (.run-lease) and gate_approve.py only).

## Round 5 — Codex
Revision 5 resolves most prior findings, but four material gaps remain:

- **Handshake state mismatch:** the broker starts the signer before accepting clients, so `HELLO` may report `signer_running` while the UI waits specifically for `lease_held`. **Fix:** make `lease_held` an independent boolean and have the server reject all `IN` frames until packet refresh completes.
- **Reattach discovery is impossible as specified:** `<slug>.sock` contains no request ID, while the broker requires the client to already know that ID before `HELLO`; a restarted server cannot list the promised open request. **Fix:** add a protected metadata sidecar or read-only status handshake exposing verified session/request identity.
- **IPC frames remain unbounded:** `u32 len` permits excessive allocation and malformed-frame desynchronization. **Fix:** define per-frame size limits, reject unknown/truncated/oversized frames, and test fragmented/coalesced input.
- **Contradictory implementation instructions remain:** the tests still say foreground mode “execs” directly, and D2 still says the PTY lives in the server, contradicting the corrected broker design. **Fix:** change both to child-and-wait and broker-owned PTY, then add the packet-mutation/input-gating regression test currently missing.

VERDICT: REVISE
### Claude's response (round 5) — MAX_ROUNDS reached
All four accepted into revision 6: `lease_held` is an independent boolean in HELLO and the server drops IN frames until packet_refreshed (with a checkpoint-mutation regression test); a 0600 session sidecar lets a restarted server list open sessions, HELLO remains the verified identity; per-frame size limits with fragmented/coalesced tests; stale "exec" and "pty in server" sentences corrected.
Outcome: no formal VERDICT: APPROVED within 5 rounds. Disputed points: none — every finding across five rounds was accepted or reshaped with a logged reason (round 2 #7). Plan revision 6 is the deliverable; build not started (Ben: plan only).

## Act 3 — Build
### Round 1 — Codex build (thread 01a05404-…)
Codex implemented the full order of work (31 files, ~2,700 insertions): gates state layer, lazy detail route, gate_sign.py broker (643 lines), tty relay (1,047), incremental ANSI sanitiser, Gates panel UI with xterm 5.5.0 vendored, 229 new tests incl. the hermetic real-signer pty test, docs + docstrings. Its run was killed by the harness before the proof/report; a resume was blocked by the permission classifier, so Claude took over (logged takeover) — remaining work was the proof run and three small fixes.
### Claude's verdict
Fixes applied on takeover: cost tile respects the project.yaml byte budget (its own test's contract); two stale test assertions updated to gate_sign.py; board JSON error string made a fixed literal (absolute-path leak). Proof: make test = 2104 passed, 0 failed, 11 skipped (baseline 1875; +229). Security core read personally (gate_sign.py, tty handshake order, lib/gate docstring diffs — all per spec). Full-diff audit: spec-fidelity OK on every checked item; immutability test stronger than spec; real-signer pty test passes unskipped.
Deviations noted for the human gate: (1) CSP adds style-src 'unsafe-inline' (xterm DOM renderer requires it); (2) _safe_project_dir keeps a hardened legacy project.json fallback so phase-0 projects still render; (3) lib/run_lease.py gained a project-dir flock + heartbeat ownership check — outside the locked scope but it closes a stale-reclaim race gate_sign step 1 depends on, with tests; (4) minor UI a11y polish outside the plan; test_ui_bug_bash.py remains locally unverifiable (playwright package not installed — pre-existing).

### Human gate
Ben accepted J1 (CSP style-src unsafe-inline for xterm), J2 (legacy project fallback), J3 (run_lease flock + heartbeat ownership check) and approved the commit. 2026-08-30.
