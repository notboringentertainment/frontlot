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
