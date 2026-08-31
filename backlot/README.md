# Backlot — the living storyboard

Backlot is a read-only local observer of a production: pipeline stages light
up, the script appears as a screenplay page, the scene plan fills in as a
filmstrip, and authored-film approval requests appear in the Gates panel.
Everything shown is derived from files the pipeline already writes under
`projects/<id>/`; the state server never writes to a project directory.

```bash
python -m backlot serve --port 4750       # foreground, one uvicorn worker
python -m backlot open                    # library view (all projects)
python -m backlot open moonlit-courier    # start if needed + open one project
```

## How it stays live

A `watchfiles` watcher on `projects/` publishes change notifications over SSE;
the browser then refetches board state. No agent action is needed.

| Board element | Disk source |
|---|---|
| identity / rail order | `project.json` + `pipeline_defs/<type>.yaml` |
| stage states and versions | `checkpoint_<stage>.json` + `history/` |
| authored-film gate summaries | raw `.gate-requests/{,done/,declined/,abandoned/}/*.json` |
| script card / modal | `artifacts/script.json` |
| filmstrip cards | `scene_plan × script × asset_manifest` join |
| generating shimmer, activity | `events.jsonl` (written by `BaseTool` instrumentation) |
| cost meter | `cost_log.json` ledger for authored-film; checkpoint `cost_snapshot` for legacy projects |
| renders | `renders/*.mp4` (+ root-level mp4 heuristic) |

Projects without checkpoints degrade gracefully to a "what the watcher
found" view — media, snapshots, and renders. Replay reconstructs a completed
run from checkpoint history and event timestamps.

## Authored-film gates

The Gates panel lists pending and archived requests. Details are loaded lazily
from raw JSON, JSONL, and PNG files. Backlot never calls approval constructors
or receipt readers that can replay the receipt WAL, and it never reconstructs
approval authority. QC rows and archived approval rows are therefore labelled
"unverified"; enforcement remains in the signer and receipt layer.

Each visual packet records `snapshot_at` and keeps `record_sha256` null. Only
the digest printed by the signer after the human supplies any selection or
reason is authoritative. Candidate and vault-object bytes are checked against
their recorded content hashes; missing, changing, or mismatched input is shown
as `packet_error` / `hash mismatch`, never silently accepted. The panel also
surfaces the current canon strip, cost source and total, and the exact next
signing command.

That command comes from `lib.run_common.gate_command()` and always invokes the
lease-holding wrapper, not `gate_approve.py` directly:

```bash
.venv/bin/python -c "from pathlib import Path; from lib.run_common import gate_command; print(gate_command(Path('projects/moonlit-courier'), 'config-moonlit-courier-1'))"
# Run the printed command in your Terminal, or use Start signing on the board.
```

The command and signer are the same in either place. `scripts/gate_sign.py`
holds the project run lease and a per-project process `flock` while the real
`scripts/gate_approve.py` runs interactively. Board mode gives that signer a
pty owned by the wrapper; the Backlot server only relays the browser WebSocket
as a client of the wrapper's 0600 Unix stream socket. A disconnected page or
stopped server does not signal, cancel, or type into the signer. The broker
remains alive and the page can reconnect, including after a server restart.

Only one signer may be open per project, and signing is refused while a live
run holds the project lease. Input is human-only: there is no automatic answer,
cancel byte, or signal because no input is safe at every prompt. The human ends
the session by answering the normal prompt (or by entering `q` where the prompt
offers it).

## Browser and process trust

At startup the server creates a random 32-byte capability token at
`~/.openmontage/backlot/<port>.token` with mode 0600. `backlot open` delivers it
in the URL fragment; the page moves it to `sessionStorage` and scrubs the URL.
The terminal WebSocket accepts only the exact localhost Origin and the first
frame must carry the token. A strict local-resource CSP and
`Referrer-Policy: no-referrer` further limit browser cross-origin access.

This protects the browser surface from a drive-by web page. It does not prove
human presence or defend against another process running as the same user: that
process could read the token just as it could already drive a pty. The trust
level remains the same process-discipline boundary as the user's Terminal;
Backlot does not add a fourth approval handler.

Backlot is localhost-only, POSIX, and macOS-first. It runs exactly one uvicorn
worker. Capability tokens, session sockets/sidecars/locks, and `server.log`
live under `~/.openmontage/backlot/`, outside projects. Backlot does not launch
pipeline runs and has no in-page Approve action; Start signing only attaches to
the real terminal workflow.

Try the legacy board without a real production:

```bash
python scripts/backlot_simulate_run.py
python -m backlot open backlot-demo-run
```

Design doc: `internal/design/LIVING_STORYBOARD.md`.
