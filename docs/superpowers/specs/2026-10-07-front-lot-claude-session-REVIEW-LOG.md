# Plan Review Log: Front Lot Claude session
Started 2026-10-07. MAX_ROUNDS=5. Plan file: docs/superpowers/specs/2026-10-07-front-lot-claude-session-design.md

## Round 1 — Codex
The plan has material security and lifecycle gaps. No files were modified.

1. **Critical — the trust-boundary test proves too little (§4.2).** `require_tty()` checks `isatty()`, not human presence. Claude can create a PTY, run the signer through it, and supply answers. More fundamentally, [lib/gates.py](/Users/ben/Projects/OpenMontage-worktrees/front-lot-redesign/lib/gates.py:27) explicitly says a same-user process can read the signing key and mint approvals itself; script deny rules cannot prevent imports or alternate wrappers.  
   **Fix:** isolate Claude’s execution from signing keys, ledgers, signer IPC, and trusted code using an enforced execution boundary; test those accesses rather than ordinary stdin alone.

2. **Critical — Claude can inject answers into an already-running signer.** [gate_sign.py](/Users/ben/Projects/OpenMontage-worktrees/front-lot-redesign/scripts/gate_sign.py:504) accepts a local socket client whose HELLO supplies the public project/request and `token_ok: true`, then forwards `IN` bytes to the signer. Mode 0600 excludes other users, not embedded Claude running as Ben. Browser capability checks do not protect this direct socket path.  
   **Fix:** make signing sockets inaccessible to Claude’s execution environment and include direct broker attachment in the boundary tests.

3. **Critical — paid-command prefixes do not enforce “nothing paid without Go” (§4.1).** Alternate interpreter paths, wrapper scripts, Python imports, direct provider calls, and MCP tools can spend without matching those prefixes. The allowance-caller inventory also misses independently paid operations such as QC judging. Anthropic documents the limitations of [Bash permission matching](https://code.claude.com/docs/en/permissions).  
   **Fix:** enforce a one-use, human-issued spend authorization at the paid execution boundary, with Claude unable to bypass it using credentials or another entry point.

4. **High — Q1’s external wait can time out and fall through.** A promise resolved later by the inbox counts against a function hook’s execution budget. Current [Mods documentation](https://code.claude.com/docs/en/plugins/mods/reference) specifies ten seconds and skips timed-out hooks. Story Drive only reports `classic.PermissionRequest` and calls `next(e)`; it does not demonstrate external approval handling.  
   **Fix:** prove the exact return shape, long waits, inbox concurrency, cancellation, and timeout behavior on 2.1.293 with free probes before building; enforce spending independently of hook success.

5. **High — spend failure handling is unspecified (§3.2, §5).** No behavior is defined for Not now racing Go, duplicate decisions, Stop while waiting, plugin reload, disconnected browsers, or late decisions after a new conversation. Copying Story Drive’s `.catch(...next(e))` would fall through on approval-handler errors.  
   **Fix:** define a fail-closed request state machine bound to session, epoch, request, and exact execution inputs; cancel outstanding requests on abort or replacement and reject duplicate or stale decisions.

6. **High — restart survival covers the PTY but not the live channel (§3.3).** The detached broker survives, but the Front Lot server owns the HTTP endpoint, sequence state, and inbox. Story Drive sends `/hello` once per epoch; restarting the server does not cause another hello. A transcript cache alone cannot restore accepted sequence numbers or pending actions.  
   **Fix:** put the live endpoint and its state in the surviving broker, or specify a durable reattachment handshake that restores sequence and action state safely.

7. **High — the proposed one-shot-allow fallback is not one-shot (§7, Q1).** Adding a permission rule can authorize later calls and can remain active if cancellation or a crash interrupts cleanup. A command match also does not bind mutable input files or scripts.  
   **Fix:** remove this fallback until approval is implemented as an atomically consumed authorization for one immutable execution request.

8. **High — single-client semantics do not cover both control surfaces (§5).** The signing broker arbitrates one PTY client; that does not establish ownership across the new live WebSocket and terminal WebSocket. A second tab could still submit Go, Stop, End, or New unless the server rejects those actions.  
   **Fix:** use one server-enforced controller lease across both surfaces, with explicit transfer and read-only rejection for every mutating action.

9. **High — the launch environment repeats a documented reference failure (§3.1).** Story Drive’s [spike results](/Users/ben/Projects/story-drive/docs/superpowers/specs/2026-10-03-live-spike-results.md) report inherited `CLAUDECODE`/`CLAUDE_CODE_*` variables causing missing transcripts and broken resume. The signing broker’s environment pattern starts with `os.environ.copy()`.  
   **Fix:** sanitize nested-session variables and pin the resolved Claude executable before launch.

10. **High — End/New cleanup lacks child ownership guarantees (§3.1).** There is no termination policy, escalation deadline, or rule preventing a replacement session while the previous Claude process or paid child remains active. The signing wrapper deliberately waits for its child; that pattern cannot simply provide bounded session termination.  
    **Fix:** define process-group ownership, bounded shutdown, exact-child reaping, and preservation of outstanding paid-job records before permitting replacement.

11. **Medium — the spend log cannot establish what actually ran (§3.3).** It records a summary and decision but omits request/session identity, exact inputs, delivery acknowledgment, authorization consumption, and execution outcome. After a disconnect, “Go recorded” is indistinguishable from “run started.”  
    **Fix:** record correlated request, decision, consumed authorization, and run outcome, including an explicit uncertain state.

The smallest useful next step is a free-only prototype proving signing isolation, delayed permission handling, and restart recovery. The boundary fixes are required by Ben’s explicit constraints; additional UI work should wait for that evidence.

VERDICT: REVISE
### Claude's response (round 1)
Accepted all 11. Structural change: Claude works inside Claude Code's OS sandbox (no read of ~/.openmontage, .env, credentials; localhost-only network; no unsandboxed escape; strict MCP config) and Front Lot executes every pipeline script via a new add-on MCP tool `frontlot_run` with an allowlist; paid scripts need Ben's Go. This removes blocking permission hooks (#4), the one-shot-allow fallback (#7), and makes spend/sign enforcement OS-level (#1-3). Broker owns PTY + live endpoint + journal for restart survival (#6). Controller lease across both sockets (#8). Env allowlist + pinned executable (#9). Process-group shutdown, replacement refused until reaped, paid jobs detached (#10). Spend state machine + full transition log incl. uncertain (#5, #11). Probes P1-P3 gate the build.

## Round 2 — Codex
Revision 2 resolves the blocking-hook problem and improves server-restart handling. It still has material gaps in the new execution boundary. No files were modified.

1. **Critical — Claude can alter code that Front Lot executes with Ben’s privileges (§4.1–4.2).** The write denies protect project records but leave `scripts/`, `lib/`, `tools/`, the vendored add-on, and Python dependencies writable. Claude could modify an allowlisted free script or its imports, then request execution outside the sandbox. An argv hash would remain unchanged.  
   **Fix:** execute an immutable trusted checkout and interpreter, with Claude allowed to write only a separate work area.

2. **Critical — the Bash sandbox does not isolate the whole Claude process (§3.1, §4.1).** Hooks, plugin helpers, and MCP processes run outside it; `--strict-mcp-config` does not disable inherited hooks or installed plugins. `allowUnsandboxedCommands: false` alone also does not establish that excluded commands or unavailable sandbox support fail closed. These distinctions are explicit in [Anthropic’s sandbox documentation](https://code.claude.com/docs/en/sandboxing).  
   **Fix:** isolate the whole Claude process, or define and test a complete restricted configuration that excludes every unsandboxed execution path and refuses startup without enforcement.

3. **High — approvals bind filenames, not the inputs used (§4.3).** [supervised_shot.py](/Users/ben/Projects/OpenMontage-worktrees/front-lot-redesign/scripts/supervised_shot.py:57) loads settings JSON when execution begins. Claude can change that file after the spend card appears while preserving the exact argv hash, changing prompt, provider settings, or cost.  
   **Fix:** freeze execution inputs before presenting the card and execute those snapshots; reject changed authoritative dependencies.

4. **High — film binding is asserted but not enforced against arguments (§3.2, §4.2).** `frontlot_run` accepts arbitrary argument strings, while existing scripts accept `--project` or project paths. A request associated with film A could target film B; path arguments also let the privileged executor read files Claude cannot read directly.  
   **Fix:** have the executor supply the current project itself and validate every input/output path against operation-specific permitted roots.

5. **High — an atomic write does not guarantee one execution (§4.3).** Two wrappers can both read `approved`, both replace it with `running`, and both execute. Crash windows between claim, child spawn, and recording child identity are also undefined. A crash before the wrapper starts leaves `approved` stranded outside the specified `uncertain` case.  
   **Fix:** use an exclusive execution claim and durable launch states; reconcile incomplete launches without automatically retrying possible paid submissions.

6. **High — `frontlot_run` acknowledges requests before reliable acceptance (§3.2).** It “reports” a request and immediately returns `running` or `waiting-for-ben`. Story Drive’s [ReportQueue](/Users/ben/Projects/story-drive/mod/story-drive-live/hooks/queue.ts:34) silently drops events after failure or overflow. Claude could receive a request ID for work the broker never received.  
   **Fix:** return success only after a short, idempotent broker acknowledgment that the request is durably accepted; use the event queue for display updates.

7. **High — Stop and epoch changes leave actionable spend cards (§4.3, §5).** Stop only aborts the turn, while pending cards remain valid for thirty minutes. Plugin reload changes the reporting epoch, but no cancellation or rebinding rule is specified. A later Go can therefore launch work from an abandoned turn or obsolete epoch.  
   **Fix:** cancel unstarted requests on Stop and define explicit cancellation or safe rebinding on epoch changes, while preserving already-running jobs.

8. **High — broker death does not prove Claude has ended (§3.1, §5).** A killed broker cannot perform its shutdown sequence or unlink sidecars. Its separate Claude process group may survive. Conversely, deleting a stale sidecar could permit a replacement while that group remains alive. Revision 2 also omits an explicit durable session-UUID persistence rule.  
   **Fix:** persist session and process identity before publication, then reconcile orphaned groups and stale sidecars under the film lock before resume or replacement.

9. **Medium — bounded replay lacks a gap-recovery contract (§3.1).** After enough events are evicted, `resume_from` may precede the journal’s retained range. Replaying only surviving events cannot reconstruct pending cards or session state, despite “nothing lost.”  
   **Fix:** return an authoritative state snapshot and new cursor whenever the requested replay range is unavailable.

The simpler boundary is a read-only trusted runtime plus narrowly validated run requests and frozen inputs. Probes should include code replacement, settings mutation after card creation, inherited unsandboxed helpers, duplicate wrapper claims, and broker death—not just direct key reads.

VERDICT: REVISE
### Claude's response (round 2)
Accepted #1 (as write-deny on the trusted runtime rather than a separate immutable checkout: same protection, less machinery), #2 (setting sources exclude user/project so Ben's hooks/plugins don't load; fail-closed sandbox self-check; residual: the vendored add-on runs unsandboxed but is write-protected Front Lot code), #3 (frozen input hashes re-checked on Go), #4 (executor supplies --project; path roots), #5 (O_EXCL claim + launch states + no auto-retry), #6 (direct durable POST /run), #7 (Stop/epoch cancel unstarted), #8 (identity persisted before publication; reconcile under film lock), #9 (snapshot on journal gap). Added an explicit threat model: capable cooperative agent that errs or improvises, not a hostile same-user program with exploit capability, matching lib/gates.py's stated scope. Review should judge against that model.

