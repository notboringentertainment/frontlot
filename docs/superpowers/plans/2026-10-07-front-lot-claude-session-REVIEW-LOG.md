# Plan Review Log: Front Lot Claude session build plan (rev 2)
Started 2026-10-08. MAX_ROUNDS=5. Codex model gpt-6.1-sol (config default), read-only.

## Round 1 — Codex
Rev 2 still has material problems. No files were modified.

1. **Critical — Tasks 1, 8: Claude can create settings that subsequent launches trust.** `--setting-sources project` loads settings from the writable work area. Claude can create `frontlot-work/.claude/settings.json`; resume or New conversation can then load hooks outside the Bash sandbox. The cached self-check does not include these settings.  
   **Fix:** exclude all implicit setting sources and probe resume after planting a work-area hook.

2. **High — Task 1: native file writes are not confined to the work area.** Enumerated Edit denies leave paths such as film-level `canon-note.md`, unlisted directories, and home configuration files outside the enforced prohibition. A permission prompt is not the promised write boundary.  
   **Fix:** enforce a complete write boundary for native tools as well as Bash, with outside-work-area regression probes.

3. **High — Task 1: credential read denies are incomplete.** Bash denies only `.env` and `.env.local`, although the spec requires `.env*`; `.env.production` remains uncovered. The actual `GOOGLE_APPLICATION_CREDENTIALS` file and other discovered credential files are never incorporated.  
   **Fix:** build both sandbox and native-tool denies from all matching environment files and configured credential locations.

4. **Critical — Task 3: `shot_prepare` exposes a privileged arbitrary-file reader.** Validating the brief’s filename does not validate its contents. [`production.prepare()`](/Users/ben/Projects/OpenMontage-worktrees/front-lot-redesign/lib/supervised_production.py:98) accepts absolute `source_paths` and copies their bytes into the film. A brief can name the signing key, making it readable by Claude afterward.  
   **Fix:** validate every embedded path against permitted film/work roots before invoking the privileged script.

5. **Critical — Task 3: the free `headshot_import` operation can spend money.** [`run_headshot()`](/Users/ben/Projects/OpenMontage-worktrees/front-lot-redesign/scripts/headshot_run.py:273) resumes existing state before checking `import_file`. An already-attested import reaches `_judge_existing`; a declined selection or override state can reach `_generate_and_present`. Neither requires this operation’s Go. P11’s tagging fix is incomplete.  
   **Fix:** make import strictly staging-only and refuse existing run state, or classify this entire invocation as paid.

6. **High — Tasks 3, 5: snapshots and recorded input hashes can describe different bytes.** `_snapshot()` copies the source first; `RequestStore.create()` later hashes the original. An intervening edit yields snapshot A but recorded hash B, and Go can approve B while execution consumes A. `_work_path()` also checks a pathname before `copyfile()` reopens it, allowing a symlink replacement race.  
   **Fix:** securely read once, snapshot those bytes, and derive the recorded digest from that same immutable content.

7. **High — Tasks 3, 4: paid dependencies are only partially frozen.** A sheet request freezes `checkpoint_visual_bible.json` and the look hash, but execution independently reads the active headshot. Replacing that headshot before Go changes the generated sheet without invalidating the card. Signed config changes can similarly change judge/model and attempt limits.  
   **Fix:** freeze and verify the active headshot identity, signed config, and other execution-authority dependencies.

8. **High — Task 4: shot revision checking is not at the paid boundary.** `production.request()` checks the revision before registry discovery and tool execution, without a run lease. Another `shot_prepare` can revise the brief afterward; allowance checks compare references and limits, but never the expected revision.  
   **Fix:** carry the expected revision to the actual pre-submit check and serialize it with brief changes.

9. **High — Task 3: spend estimates omit permitted retry spending.** Headshot estimates count requested passing candidates; sheet estimates count one attempt per role. The real scripts can spend through their remaining signed attempt budgets. `headshot_finish` can also resume generation, despite showing only one judge reserve.  
   **Fix:** use the scripts’ remaining-attempt calculations and show the full possible run cost, or constrain execution to the displayed scope.

10. **High — Tasks 7, 8: delayed requests bypass epoch cancellation.** `/run` carries no originating epoch or turn generation. A request arriving after Stop or a new `/hello` is assigned the broker’s current epoch and becomes a fresh actionable card, although its originating work was cancelled.  
    **Fix:** bind submissions to their originating epoch and cancellation generation, rejecting stale arrivals.

11. **High — Tasks 5, 6: acknowledged requests and Go records are not durable.** State replacements, claim creation, and spend-log appends have no `fsync`; `_write()` publishes state before appending its audit entry. A machine crash can lose the accepted key, Go, or claim after paid execution starts.  
    **Fix:** durably commit the request, decision, audit entry, and exclusive claim before acknowledgment or spawn.

12. **High — Task 5: late outcomes never resolve uncertainty.** `reconcile()` checks outcome files only for `launching` or `running`. Once marked `uncertain`, a request stays uncertain even when its successful outcome arrives. An in-memory execution of the supplied code confirmed this.  
    **Fix:** reconcile authoritative outcomes for uncertain requests without relaunching them.

13. **High — Tasks 5, 6: a paid timeout is presented as ordinary failure.** The wrapper records only the script’s exit code. A provider can accept work, then polling fails; `supervised_shot` exits nonzero and the request becomes `failed`, although the provider job remains outstanding or indeterminate.  
    **Fix:** classify the result using the operation’s reservation/provider records and report unresolved submissions as uncertain.

14. **High — Tasks 5, 8: completion notifications can disappear after a crash.** The watcher first commits `done`/`failed`, then journals the event and queues Claude’s message. A crash between those steps leaves a terminal request excluded from future reconciliation, so neither notification is regenerated.  
    **Fix:** persist notification delivery state alongside completion and replay undelivered outcomes after restart.

15. **High — Tasks 8, 9: abrupt broker death leaves unstarted requests uncancelled.** Startup never cancels predecessor requests. The first `/hello` skips cancellation because the new broker initializes `addon_epoch = None`; resumed snapshots can therefore retain old waiting cards that only fail when clicked or expire later.  
    **Fix:** cancel predecessor unstarted requests during locked startup reconciliation and publish their terminal states.

16. **High — Tasks 8, 9: startup has an unrecoverable orphan window.** Claude is spawned before its identity file is written. Killing the broker in that interval leaves an unrecorded Claude group and an existing live socket. `reconcile_orphans()` returns `clean` when identity is absent, leaving the socket to obstruct replacement.  
    **Fix:** add recoverable startup identity/publication sequencing and clean abandoned sockets when no broker owns the lock.

17. **High — Task 9: orphan reconciliation does not confirm group termination.** It watches only the Claude leader PID, unlinks identity after bounded waits regardless of whether the group is gone, and discards records immediately when the leader is absent—even if descendants survive. `broker_alive()` also omits boot identity.  
    **Fix:** verify boot/process identity and refuse replacement until the owned group is confirmed gone.

18. **High — Task 9: a read-only tab can replace the conversation.** `new` is handled directly by `_Relay` and by the first-message handler; neither checks the broker’s controller lease before calling `spawn_broker(..., "new")`. It can terminate the controller’s session and cancel its pending cards.  
    **Fix:** authorize replacement through the existing broker’s controller check before invoking the spawner.

19. **High — Tasks 8, 9: control transfer is not defined across both sockets.** Controller flags are per client, while a page has separate live and tty connections. Demoting only “the holder” can leave its other connection authorized; disconnecting one connection can also start lease expiry while its sibling remains connected.  
    **Fix:** derive authorization from the current page lease and update every connection belonging to that page.

20. **High — Tasks 9, 11: Pick back up has no matching action handler.** The UI sends `{type:"resume"}` after Ended, but the established relay accepts neither resume nor start; these are only first-message actions. Initial page load also always sends `start`, which creates a fresh UUID when an ended conversation could resume.  
    **Fix:** reconnect with the correct first-message mode and choose attach/resume/new from persisted session state.

21. **Medium — Task 8: following the broker instructions verbatim fails at runtime.** The supplied imports omit `threading` and `termios`, although the numbered implementation requires both. `threading.Thread(...)` occurs after Claude and the client socket are published, so the error leaves a partially started session.  
    **Fix:** include the required imports and test cleanup on exceptions after child spawn.

22. **High — Tasks 1, 2: the sandbox probes do not establish their claimed evidence.** The paid Python probe prints only `.success`; with the allowlisted environment, `SeedreamImage.execute()` returns immediately for missing `FAL_KEY`, without attempting network access. The startup self-check accepts allowed-canary output plus absence of the denied secret, which also passes if Claude simply skips the denied command.  
    **Fix:** capture tool-level execution evidence and use controlled probes that demonstrably reach each enforced boundary without real paid submission.

The complete Python snippets do not reveal a Python 3.10 syntax incompatibility; the runtime omissions and boundary failures above remain implementation blockers.

VERDICT: REVISE
### Claude's response (round 1)

All 22 accepted after verification; none rejected. Plan bumped to rev 3 ("Changes in rev 3" in the plan). Experiments ran on claude 2.1.294 in a scratch dir under `~/Library/Caches` (non-temp, because the sandbox allows temp-dir writes by default). The Task 1, 3, 4, 5, 6 and 7 code and tests, applied verbatim to a throwaway copy of HEAD, pass (8/10/20/12/4/4 new Python tests; 110 + 13 pipeline tests; 33 mod tests; `claude plugin validate --strict`).

1. Accepted. Verified: a SessionStart hook planted in `work/.claude/settings.json` ran under `--setting-sources project` and did not run under `--setting-sources ""`. Launch, self-check and probes now use `""`; writes to the work area's `.claude/**`, `.mcp.json`, `CLAUDE*.md` and `.git/**` are denied; probe P3 plants a hook and checks it does not run on a new launch or a resume (with a `project` control that shows the plant does fire).
2. Accepted. Verified: under `permissions.defaultMode: "dontAsk"` with an allow-list, a Write outside the allowed dir is refused without a prompt ("denied because Claude Code is running in don't ask mode"), a denied subdirectory is refused, and sandboxed Bash still runs. Edit is allowed only in the work area and Read only in the film, repo and work area; the enumerated record denies are removed. P1 tries Write outside the work area (film record, an unlisted film file, a sibling film, a repo script, home) and Bash writes too.
3. Accepted. Verified that rev 2 denied only `.env` and `.env.local`. Now every `.env*` file in the repo is denied, plus every existing file named by a `*_CREDENTIALS`, `*_KEY_FILE` or `*_KEY_PATH` variable in the environment or in `.env*` (e.g. `GOOGLE_APPLICATION_CREDENTIALS`). This applies to both the sandbox `denyRead` and native Read; `~/.ssh` and `~/.netrc` are added; there is a test.
4. Accepted. Verified that `lib/supervised_production.py:96-99` resolves absolute `source_paths`. `shot_prepare` parses the brief from its snapshotted bytes. Every `source_paths` and `reference_manifest` path must then be relative, inside the film, outside the work area, and free of symlinks. There is a test.
5. Accepted. Verified that `scripts/headshot_run.py:272-274` resumes run state before the `--import` branch at :300. `headshot_import` now refuses when the entity has run state and freezes `checkpoint_headshots.json` with `--expect-input-sha`, so the script refuses if run state appears before its lease.
6. Accepted. Files are read once (each path component opened with `O_NOFOLLOW` under the work-area dir fd), and the snapshot is written from those bytes. `Prepared.inputs` is now `{path: digest-of-those-bytes}`, and `RequestStore.create` records it verbatim. There are tests for symlinks and for edits made after prepare.
7. Accepted. Verified that sheet_run reads the active headshot at :267 and the config at :232. Paid runs now freeze the signed config (`--expect-config-sha`, checked against `VerifiedProjectConfig.digest`, the bytes actually verified) and, for sheets, the headshot receipt (`--expect-headshot`). Checks run at lease entry or right after resolution, before any paid call; there are tests.
8. Accepted. Verified that `reserve_paid_call` re-runs `ShotGuard.check` under `reservation_lock` immediately before reserving (`tools/cost_tracker.py:736-741`), and `_append` takes the same lock. `request()` now puts `brief_revision_id` into the inputs and `ShotGuard.check` compares it. A test revises the brief after Go and shows the paid boundary refusing with no reservation written. The tools build payloads from named keys, so the extra key never reaches a provider.
9. Accepted. Verified the retry loops: headshot `_budget` / `need`, sheet `cap - used` per role, and the `_declined` → `_generate_and_present` path in finish. Cards now show "up to $X" from the signed budgets (`max_hero_attempts` minus attempts started; `max_attempts_per_series` per role; finish adds one judge reserve).
10. Accepted. `/run` carries the add-on epoch and `mainTurnId`. The broker records stopped turns and refuses old-epoch or stopped-turn requests without storing them; there is a test.
11. Accepted, scoped to money as directed. `create`, `decide` and the claim append and fsync the spend-log line first, then fsync the state file and the directory; the claim file is fsync'd. There is a test that counts the fsyncs. Other transitions are plain writes.
12. Accepted. Confirmed from the rev-2 code (only `launching`/`running` consulted the outcome). `reconcile` now also settles `uncertain` from a late outcome and never relaunches; there is a test.
13. Accepted. The wrapper lists the request film's paid-call reservations (`cost-reservations.jsonl`) that appeared during the run and are still `submitting`/`pending_billing`. A non-zero exit with any of them becomes `uncertain` with the reservation ids; there are tests (store and wrapper).
14. Accepted. Each record carries `notified`, and the watcher re-sends anything not yet notified (`pending_notices`), so delivery is at least once across restarts. Bookkeeping writes skip the spend log; there is a test.
15. Accepted. Broker start cancels and journals every unstarted request left by the previous broker; a test kills a broker with -9 and checks the new one cancels its card.
16. Accepted, with the simplest recoverable sequence. Claude is spawned behind a `/bin/sh` gate that waits on a pipe and then `exec`s claude, keeping the same pid, group and start time. The identity file is written (fsync) before the gate opens and before the socket is published, so there is never an unrecorded running Claude. Any failure after the spawn runs `shutdown("start-failed")`. Reconcile removes sockets that no identity owns; there are tests (failure before and after the gate opens; sockets nobody owns).
17. Accepted. Reconcile checks boot time, group liveness (`killpg(pgid, 0)`) and leader start time. It escalates on the group and returns `stuck` (keeping the record and refusing replacement) until the group is confirmed gone, or when the leader is gone but the group lives (spec §3.1 allows signalling only a verified leader). `broker_alive` now checks boot time. Broker shutdown unlinks identity only after the group is confirmed gone. There are tests.
18. Accepted. `new` is now a broker `ACTION` that is refused for non-controllers. The broker ends with `BYE {reason: "new", page}` and only that page's relay spawns the replacement; `spawn_broker` never ends a live broker. There are tests.
19. Accepted. The lease is keyed by page id: every connection of the lease page is authorized, transfer sends `STATUS` to every connection, and expiry starts only when the page's last connection closes. The page's tty socket uses the same page id; there is a test.
20. Accepted. The relay handles `start`/`resume`/`new`/`attach` at any time. The page picks its first message from GET state (running/ended/unavailable → `attach`, none → `start`), and Pick back up sends `resume` on the open socket. There is a test.
21. Accepted. Added `threading`, `termios` and `struct` to the imports. `warm_estimates` starts before anything is spawned or published, and every step after the spawn is inside `try … shutdown("start-failed")`. A test forces a failure at two points and checks that nothing is left behind.
22. Accepted. Probes and the self-check parse `--output-format stream-json --verbose` (verified format: `tool_use`/`tool_result` blocks; e.g. Bash `cat` of a denied file → `is_error: true`, "Operation not permitted"). A row passes only if the matching tool call exists and its result shows the refusal and the effect is absent. Controls prove that allowed calls are seen as allowed. The "paid call from python" row is replaced by a raw TCP connect to a provider host, which sends no request, plus an env check for provider keys. The self-check now also needs a refused outside-work Write, and it fails when the denied step was skipped.

Also found while verifying: `--mcp-config` is variadic, so a prompt placed right after its value is swallowed ("MCP config file not found: …/Reply with the word ok."). Rev 2's `selfcheck_argv` had that order; fixed, and a test was added.

## Round 2 — Codex
Rev 3 still has material problems. The round-1 fixes are present, but several remain incomplete.

1. **Tasks 5/8 — Outcomes can still disappear across restart.** The watcher calls `mark_notified` immediately after queuing a message in the in-memory inbox. Kill the broker before `/inbox-ack: submitted`, and the queue disappears while `pending_notices()` excludes the outcome forever. This does not establish the claimed “at least once” delivery.
   **Fix:** Tie notification completion to a durable inbox action acknowledged as `submitted`, and replay unacknowledged actions after restart.

2. **Task 8 — An old inbox poll can consume a new epoch’s message.** `/inbox` checks the epoch before its 25-second wait, then stamps the current epoch after dequeueing. If `/hello` changes epochs during that wait, the old poll receives the message; the vendored add-on rejects it as “epoch ended,” and `/inbox-ack` deletes it.
   **Fix:** Recheck the poll’s epoch after waiting and retain or requeue the action when its epoch has ended.

3. **Tasks 7/8 — Stop depends on an asynchronously delivered display event.** The real add-on’s `turn.start` hook merely enqueues its report. If Stop arrives before that report, `broker.turn_id` is empty or stale, so the issuing turn is never added to `stopped_turns`; its delayed `/run` is accepted after the cancellation sweep. The new test avoids this race by posting the turn-start report first.
   **Fix:** Synchronize turn registration and Stop cancellation with `/run` authorization, and test Stop before delivery of the turn-start report.

4. **Task 3 — Headshot candidates can exceed the displayed upper bound.** `_hero_remaining()` subtracts all started attempts. When the final attempt generated an asset but crashed before judging it, the card shows “up to $0.00”; the real [`headshot_run.py`](/Users/ben/Projects/OpenMontage-worktrees/front-lot-redesign/scripts/headshot_run.py:533) resumes that open attempt and calls the paid judge.
   **Fix:** Include the judge reserve for every open, unjudged attempt in the candidates estimate.

5. **Task 3 — A sheet subset can generate roles omitted from its estimate.** `_sheet()` prices only the requested roles. The real [`sheet_run.py`](/Users/ben/Projects/OpenMontage-worktrees/front-lot-redesign/scripts/sheet_run.py:292) appends omitted mandatory roles when their previous assets or receipts were rejected. A wardrobe-only card can therefore pay for wardrobe, turnaround, and expressions.
   **Fix:** Estimate the script’s effective role set from the frozen checkpoint, including mandatory roles it will regenerate.

6. **Task 3 — Shot estimates use different inputs from execution.** `tool_estimate()` receives raw settings, while `production.request()` replaces their references with the saved brief’s references. Seedream charges for additional reference images; ten saved references can cost $0.1755 while raw settings produce an “up to $0.14” card. Ordinary rounding can also round an upper bound downward.
   **Fix:** Estimate the actual frozen execution inputs and round upper bounds upward, or display “cost unknown.”

7. **Task 6 — Reservation inspection fails open.** `_open_reservations()` catches every exception and returns `{}`. The real ledger reader raises on malformed JSONL. If inspection fails after a potentially submitted paid call, a nonzero exit becomes `failed` rather than `uncertain`, concealing unresolved financial status.
   **Fix:** Record reservation-inspection failures in the outcome and classify the paid request as `uncertain` when its financial status cannot be established.

8. **Task 8, affecting Tasks 9/12 — The shared operation fake uses the obsolete input type.** `STUB_SITECUSTOMIZE` constructs `Prepared(..., [], ...)`, but rev 3’s `RequestStore.create()` calls `prep.inputs.items()`. Both stub operations raise `AttributeError`, preventing the broker and end-to-end spend tests from reaching their assertions.
   **Fix:** Change the fake’s `inputs` argument from `[]` to `{}`.

VERDICT: REVISE
### Claude's response (round 2)

All 8 accepted after verification; none rejected. Plan bumped to rev 4 ("Changes in rev 4"). The Task 1, 3, 5, 6 code and tests, applied verbatim to a throwaway copy of HEAD, pass (59 tests); the Task 7 mod changes pass `claude plugin test` (34) and `validate --strict`.

1. Accepted. Confirmed that rev 3 called `mark_notified` right after queueing in memory. Now `mark_notified` runs only from `/inbox-ack` (`submitted`, or a rejection other than `epoch ended`, which is also shown to Ben as a notice). The watcher queues each pending notice once per broker life, so a restart re-sends anything not yet acked. A test kills the broker after delivery and before the ack, and checks that the new broker re-sends the message.
2. Accepted. Confirmed that rev 3 checked the epoch only before the 25 s wait. `/inbox` now re-checks after the wait and puts the action back at the front (`appendleft`); an `epoch ended` rejection requeues instead of dropping (the vendored add-on rejects with that reason, `register.ts:207`). A test starts a poll for epoch e1, sends `/hello e2` while it waits, and checks that the message reaches the e2 poll.
3. Accepted. Verified that `turn.start` only enqueues its report (`register.ts:276-281`). Stop is now turn-agnostic: `stop {turnId: "*"}`; the add-on aborts its own `mainTurnId` and acks `aborted:<turn>`. The broker refuses every `/run` while a Stop is unanswered (15 s cap) and refuses the aborted turn afterwards. Tests: the broker test sends Stop before any turn report, and the mod test covers `*`.
4. Accepted. Verified at `headshot_run.py:533-541`: an open attempt with a generation is resumed and judged. The estimate now adds `DEFAULT_RESERVE_USD` for each started attempt with no `verdict_attached`/`attempt_voided` row (`lib/qc_receipts.py:151-157` rule).
5. Accepted. Verified at `sheet_run.py:287-297`: omitted mandatory roles that the writer rejected are regenerated. The estimate now prices the union of the requested roles and `MANDATORY_ROLES` (an upper bound, kept simple rather than replaying the checkpoint).
6. Accepted, using the controller's allowance. Verified that `production.request` swaps in the saved brief's references (`lib/supervised_production.py:124-137`). `shot_generate` now shows "cost unknown" and still needs Go; the registry warm-up is gone; headshot and sheet upper bounds round up (`_up`).
7. Accepted. Verified that `load_reservations` raises `ValueError` on malformed JSONL. The wrapper now records `["paid-call ledger unreadable"]` when it cannot read the ledger before or after a paid run, so a failed exit is classified `uncertain`. A test feeds a malformed ledger.
8. Accepted. Confirmed that `RequestStore.create` iterates `prep.inputs.items()`; the stub now passes `{}`.

## Round 3 — Codex
Rev 4 resolves the cost, ledger, and fixture findings, but four material problems remain:

1. **Task 8 — Stop expires without proving the turn stopped.** `/run` blocks only while a `stop_pending` deadline remains unexpired; the watcher deletes it after 15 seconds. If the add-on is delayed or silent, the same unstopped turn can then submit requests that escape Stop’s cancellation sweep.
   **Fix:** Keep authorization blocked until Stop is acknowledged or a confirmed epoch/session replacement retires the old turn; use the timeout only to show a notice.

2. **Task 8 — Stop does not wake an outstanding inbox poll.** The Stop branch calls `outbox.appendleft(...)` without setting `outbox_ready`. A poll already waiting on that event can remain asleep for 25 seconds—longer than the 15-second authorization block. The test polls *after* Stop, so it misses this.
   **Fix:** Set `outbox_ready` on every enqueue/requeue path and test Stop while an inbox poll is already waiting.

3. **Tasks 7/8 — Redelivering Stop loses the aborted turn identity.** The retained `act()` shortcut for `handled.has(action.id)` returns `{status: "submitted"}` without `reason: "aborted:<turn>"`. If the first acknowledgment never reaches the broker, redelivery clears `stop_pending` without adding the aborted turn to `stopped_turns`; a delayed request from that turn is accepted.
   **Fix:** Cache and replay the complete final acknowledgment, including its aborted turn ID, and test loss of the first acknowledgment.

4. **Task 8 — The cancellation watcher branch has no `aid`.** Step 7 marks a cancelled request notified without calling `tell_claude`, then instructs the implementer to record `notice_actions[aid]`. A cancellation caused by changed input at Go reaches this branch: `aid` is either undefined or left over from another request.
   **Fix:** End the cancelled branch with `continue`; create `notice_actions` entries only for branches that actually enqueue a message.

VERDICT: REVISE
### Claude's response (round 3)

All 4 accepted after verification; none rejected. Plan bumped to rev 5 ("Changes in rev 5"). The Task 7 add-on changes (including the two Stop tests), applied verbatim to a renamed copy of the Story-drive mod, pass `claude plugin test` (35) and `validate --strict`.

1. Accepted. Confirmed that rev 4's watcher dropped `stop_pending` after 15 s. Entries now leave only through their final ack, a new add-on epoch (`/hello` clears them, and the epoch check refuses the old turns), or shutdown. After `STOP_NOTICE_SECONDS` the broker journals one notice: "Claude hasn't confirmed it stopped — start a new conversation to be sure." The test checks that the notice arrives and that `/run` is still refused afterwards.
2. Accepted. Confirmed that rev 4's Stop branch used `outbox.appendleft` without setting the event. A single `enqueue()` helper now always sets `outbox_ready`, and every path uses it: Stop, requeue after an ended epoch, the `epoch ended` ack, and `tell_claude`. The test starts a poll first, then sends Stop, and expects the Stop action within 3 s.
3. Accepted. Verified the shortcut at `register.ts:186`: `if (handled.has(action.id)) return sendAck(... { status: 'submitted' })` loses the reason. Final acks are now cached in `finalAcks` (cleared with `handled`) and replayed on redelivery. The mod test delivers the same Stop twice and expects one abort and two identical `aborted:t7` acks. The broker test loses the first ack and gets a redelivery after `FRONTLOT_REDELIVER_SECONDS`; the replayed ack then blocks t1 and allows t2.
4. Accepted. The `cancelled` branch now calls `mark_notified` and `continue`s. `notice_actions` and `queued_notices` are recorded only by the two branches that call `tell_claude`.
