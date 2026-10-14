"""One-time adoption of pre-chain receipts into a project's signed receipt chain.

Projects whose approvals.jsonl / generation-receipts.jsonl were minted before
the per-project receipt chain existed (lib.gates ``chains/``) have signed,
ledgered receipts that no chain row names, so every reader fails closed and
scripts/gate_approve.py cannot even append a new approval. This handler lets
a human adopt those rows once:

    scripts/chain_bootstrap.py --project <slug>

Rules (same trust posture as gate_approve.py):

* TTY-only. Agents cannot run it.
* Every local row must carry a valid HMAC signature under the orchestrator
  key; approval rows must also have their consumed-token ledger entry
  (``lib.gates.verify_receipt``). One bad row refuses the WHOLE bootstrap —
  there is no partial adoption.
* The human sees every row and confirms. Rows are then linked into the chain
  in file order through the same ``chain_append`` that gate/receipt writers
  use, and a signed ``chain_bootstrap`` marker
  ``{project_id, adopted_receipt_ids, adopted_at}`` is written to the
  project's ``bootstrap`` chain and mirrored into the gates ledger.
* One-time: a project whose marker exists is refused. A partial chain with
  no marker (a crash mid-adoption) is offered as a resume — the existing
  chain must be an exact prefix of the local file first.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib import gates  # noqa: E402
from lib.receipts import (  # noqa: E402
    approvals_path,
    generation_receipts_path,
    project_id_for,
)
from lib.state_io import append_jsonl, read_jsonl  # noqa: E402
from scripts.gate_approve import GateHandlerError, project_root, require_tty  # noqa: E402

STREAMS = ("approval", "generation")
MARKER_KIND = "chain_bootstrap"


class BootstrapRefused(GateHandlerError):
    pass


class Declined(Exception):
    """The human answered anything but 'y'."""


@dataclass(frozen=True)
class LocalRow:
    stream: str
    index: int
    receipt: dict

    @property
    def receipt_id(self) -> str:
        return str(self.receipt.get("receipt_id"))

    def describe(self) -> str:
        r = self.receipt
        if self.stream == "approval":
            what = f"{r.get('stage')}/{r.get('scope')}"
            if r.get("entity_id"):
                what += f" entity={r['entity_id']}"
            elif r.get("artifact_type"):
                what += f" artifact={r['artifact_type']}@{r.get('artifact_version')}"
        else:
            what = f"output={r.get('output_sha256')} tool={r.get('tool')}"
        return f"{self.stream:10} {r.get('kind') or self.stream:20} {self.receipt_id}  {r.get('record_sha256') or gates.receipt_digest(r)}  {what}"


def _local_path(root: Path, stream: str) -> Path:
    return approvals_path(root) if stream == "approval" else generation_receipts_path(root)


def load_local_rows(root: Path, project_id: str) -> dict[str, list[LocalRow]]:
    out: dict[str, list[LocalRow]] = {}
    for stream in STREAMS:
        rows = []
        for i, raw in enumerate(read_jsonl(_local_path(root, stream))):
            if not isinstance(raw, dict):
                raise BootstrapRefused(f"{stream} row {i} is not an object")
            rows.append(LocalRow(stream, i, raw))
        out[stream] = rows
    return out


def verify_rows(rows: dict[str, list[LocalRow]], project_id: str) -> list[str]:
    """Problems, one line per bad row. Empty means every row is adoptable."""
    problems: list[str] = []
    for stream in STREAMS:
        for row in rows[stream]:
            r = row.receipt
            where = f"{stream} row {row.index} ({row.receipt_id!r})"
            if not row.receipt_id or row.receipt_id == "None":
                problems.append(f"{where}: no receipt_id")
                continue
            if r.get("project_id") != project_id:
                problems.append(f"{where}: carries project_id {r.get('project_id')!r}, not {project_id!r}")
                continue
            if not gates.verify_receipt_signature(r):
                problems.append(f"{where}: HMAC signature does not verify under the orchestrator key")
                continue
            if stream == "approval" and not gates.verify_receipt(r):
                problems.append(f"{where}: no consumed-token ledger entry for its (token, receipt, record, project)")
    return problems


def _chain_state(project_id: str, rows: dict[str, list[LocalRow]]) -> tuple[bool, dict[str, int]]:
    """``(resuming, already_chained_per_stream)``. Raises if a marker exists,
    if the chain is corrupt, or if a partial chain is not a prefix of the
    local file."""
    if gates.chain_rows(project_id, "bootstrap"):
        raise BootstrapRefused(
            f"project {project_id!r} is already bootstrapped (chain_bootstrap marker present); "
            "bootstrap is one-time"
        )
    chained: dict[str, int] = {}
    for stream in STREAMS:
        chain = gates.chain_rows(project_id, stream)
        local = rows[stream]
        if len(chain) > len(local):
            raise BootstrapRefused(
                f"{stream}: chain already holds {len(chain)} receipts but the local file has {len(local)} — "
                "chain and local file diverge; not a pre-chain project"
            )
        for i, expected in enumerate(chain):
            row = local[i]
            if row.receipt_id != expected["receipt_id"] or gates.receipt_digest(row.receipt) != expected["record_sha256"]:
                raise BootstrapRefused(
                    f"{stream}: chained receipt {expected['receipt_id']} at position {i} does not match local row "
                    f"{row.receipt_id!r} — chain and local file diverge; refusing to resume"
                )
        chained[stream] = len(chain)
    resuming = any(chained.values())
    return resuming, chained


def _write_marker(project_id: str, adopted_ids: list[str]) -> dict:
    marker = {
        "receipt_id": str(uuid.uuid4()),
        "kind": MARKER_KIND,
        "project_id": project_id,
        "adopted_receipt_ids": adopted_ids,
        "adopted_at": datetime.now(timezone.utc).isoformat(),
    }
    # record_sha256 binds the payload (ids + timestamp); the chain row then
    # binds the whole signed marker, and the ledger row is matched on both.
    marker["record_sha256"] = gates.receipt_digest(marker)
    marker["signature"] = gates.sign_receipt(marker)
    gates.chain_append(project_id, "bootstrap", marker)
    gates._ensure_dirs(gates.gates_dir())
    append_jsonl(gates.ledger_path(), marker)
    return marker


def bootstrap(root: Path, *, ask=input, out=None) -> dict:
    """Interactive adoption. Returns the marker on success; raises
    BootstrapRefused when anything fails verification and Declined when the
    human does not confirm. Nothing is written before the confirmation."""
    require_tty()
    out = out if out is not None else sys.stdout
    project_id = project_id_for(root)
    rows = load_local_rows(root, project_id)
    total = sum(len(v) for v in rows.values())
    if total == 0:
        raise BootstrapRefused(f"project {project_id!r} has no local receipts; nothing to adopt")

    problems = verify_rows(rows, project_id)
    if problems:
        raise BootstrapRefused(
            "refusing the whole bootstrap — no partial adoption. Rows that fail verification:\n  "
            + "\n  ".join(problems)
        )

    resuming, chained = _chain_state(project_id, rows)
    pending = [row for s in STREAMS for row in rows[s][chained[s]:]]
    if not pending:
        raise BootstrapRefused(f"project {project_id!r}: every local receipt is already chained; nothing to adopt")

    print(f"project {project_id}: {total} local receipt(s)", file=out)
    if resuming:
        print(
            f"resuming an interrupted bootstrap: {sum(chained.values())} already chained, "
            f"{len(pending)} left to adopt (chain is an exact prefix of the local files)",
            file=out,
        )
    print(f"{'stream':10} {'kind':20} {'receipt_id':36}  {'record_sha256':64}  entity/scope", file=out)
    for s in STREAMS:
        for row in rows[s]:
            flag = "chained " if row.index < chained[s] else "adopt   "
            print(f"{flag}{row.describe()}", file=out)

    answer = ask("Adopt these rows into the signed chain and write the chain_bootstrap marker? [y/N] ")
    if answer.strip().lower() != "y":
        raise Declined()

    adopted: list[str] = []
    for s in STREAMS:
        for row in rows[s]:
            gates.chain_append(project_id, s, row.receipt)  # idempotent on receipt_id
            adopted.append(row.receipt_id)
    marker = _write_marker(project_id, adopted)
    print(f"bootstrapped — {len(pending)} receipt(s) adopted, marker {marker['receipt_id']}", file=out)
    return marker


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, help="project slug under projects/")
    ap.add_argument("--projects-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    try:
        require_tty()
        root = project_root(args.project, args.projects_dir)
        bootstrap(root)
        return 0
    except Declined:
        print("declined — nothing written", file=sys.stderr)
        return 1
    except (GateHandlerError, gates.ReceiptChainError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
