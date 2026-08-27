"""One-use human gate tokens, receipt signatures, and the consumed-token ledger (PLAN §5).

State lives in an orchestrator-owned directory outside any project tree:
``$OPENMONTAGE_GATES_DIR`` or ``~/.openmontage/gates``. Layout::

    key               per-install HMAC secret (0600, created on first use)
    pending/<hmac>.json   minted, unconsumed bindings
    consumed/<hmac>.json  bindings moved here atomically on consume
    ledger.jsonl      {token_hmac, receipt_id, record_sha256, project_id}
    generation-ledger.jsonl  {receipt_id, output_sha256, project_id, execution_id}
    chains/<sha256(project_id)>.<stream>.jsonl  signed per-project receipt chain
                      ({project_id, stream, receipt_id, kind, prev_receipt_id,
                      record_sha256, signature}); ``.tip.json`` beside it is the head
    chains/<name>.journal.json  in-flight chain advancement (row written, tip not yet)
    locks/<project>.<stream>.lock  flock files serializing receipt transactions
    wal/<hmac>.json   approval write-ahead entries (crash recovery, see lib.receipts)
    generation-wal/<execution_id>.json  paid-output write-ahead entries (lib.receipts.recover_generation_wal)

Only the HMAC of a token is ever stored; the raw token is returned once to
the gate handler. Receipts are HMAC-signed over their canonical JSON minus
``signature`` and cross-checked against the ledger by ``verify_receipt``,
which matches the full tuple ``(token_hmac, receipt_id, record_sha256,
project_id)``. Generation receipts (lib.receipts.record_generation) are
signed with the same key and ledgered in ``generation-ledger.jsonl``.

Trust boundary, stated honestly: the TTY-only gate handler
(scripts/gate_approve.py) and the orchestrator-owned key stop an agent from
approving through the pipeline API by discipline and process separation, not
by an OS privilege boundary. A malicious local process running with the
user's account can read the key under ``~/.openmontage/gates`` and mint,
sign, and ledger approvals itself. What this layer guarantees is that
*pipeline code following its contract* — directors, tools, checkpoint
writers — has no API path to self-approve, and that any approval which did
not pass through the human gate is detectable as unsigned/unledgered.

``mint_gate_token`` additionally refuses unless the calling process is inside
``handler_context()`` — a per-process nonce that scripts/gate_approve.py
enters right before minting and leaves right after. That is the same kind of
discipline boundary: it stops pipeline code from minting by accident or by
import, not a co-resident hostile process that can call ``handler_context``
itself.
"""

from __future__ import annotations

import fcntl
import hmac
import hashlib
import json
import os
import re
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from lib.canonical_json import canonical_bytes
from lib.state_io import append_jsonl, atomic_write_json, read_jsonl

DEFAULT_TTL_SECONDS = 3600


class GateError(Exception):
    """Base class for gate token failures."""


class GateTokenInvalid(GateError):
    """Unknown, already consumed, or mismatched token."""


class GateTokenExpired(GateError):
    pass


@dataclass(frozen=True)
class GateBinding:
    token_hmac: str
    project_id: str
    stage: str
    scope: str
    record_sha256: str
    expires_at: str
    minted_at: str
    user_response: Optional[Any] = None

    def to_dict(self) -> dict:
        return {
            "token_hmac": self.token_hmac,
            "project_id": self.project_id,
            "stage": self.stage,
            "scope": self.scope,
            "record_sha256": self.record_sha256,
            "expires_at": self.expires_at,
            "minted_at": self.minted_at,
            "user_response": self.user_response,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GateBinding":
        return cls(
            token_hmac=d["token_hmac"],
            project_id=d["project_id"],
            stage=d["stage"],
            scope=d["scope"],
            record_sha256=d["record_sha256"],
            expires_at=d["expires_at"],
            minted_at=d["minted_at"],
            user_response=d.get("user_response"),
        )


def gates_dir() -> Path:
    return Path(os.environ.get("OPENMONTAGE_GATES_DIR") or "~/.openmontage/gates").expanduser()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_dirs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / "pending").mkdir(exist_ok=True, mode=0o700)
    (root / "consumed").mkdir(exist_ok=True, mode=0o700)


def _load_key(root: Optional[Path] = None) -> bytes:
    root = root or gates_dir()
    _ensure_dirs(root)
    key_path = root / "key"
    if key_path.exists():
        return key_path.read_bytes()
    key = secrets.token_bytes(32)
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        key_path.unlink(missing_ok=True)
        raise
    os.close(fd)
    return key


def _token_hmac(token: str, key: bytes) -> str:
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def _valid_hmac_name(name: str) -> bool:
    return len(name) == 64 and all(c in "0123456789abcdef" for c in name)


def token_digest(token: str) -> str:
    """HMAC name of a raw token (the only form ever stored)."""
    return _token_hmac(token, _load_key())


def peek_binding(token: str) -> GateBinding:
    """Read a token's pending binding WITHOUT consuming it. Raises
    GateTokenInvalid if the token is unknown or already consumed."""
    root = gates_dir()
    digest = _token_hmac(token, _load_key(root))
    pending = root / "pending" / f"{digest}.json"
    try:
        return GateBinding.from_dict(json.loads(pending.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise GateTokenInvalid("gate token unknown or already consumed") from None


def consumed_binding(token_hmac: str) -> Optional[GateBinding]:
    """The consumed binding for ``token_hmac`` if the token was consumed."""
    if not _valid_hmac_name(token_hmac):
        return None
    path = gates_dir() / "consumed" / f"{token_hmac}.json"
    try:
        return GateBinding.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None


def is_pending(token_hmac: str) -> bool:
    return _valid_hmac_name(token_hmac) and (gates_dir() / "pending" / f"{token_hmac}.json").exists()


# ---- Gate handler process marker (inspection round 2, #4 narrowed) ----

HANDLER_ENV = "OPENMONTAGE_GATE_HANDLER"
_handler_nonce: Optional[str] = None


class GateHandlerRequired(GateError):
    """``mint_gate_token`` was called outside ``handler_context()``."""


@contextmanager
def handler_context() -> Iterator[None]:
    """Mark this process as the human gate handler for the duration of the block.

    A fresh random nonce is stored in this module AND exported as
    ``$OPENMONTAGE_GATE_HANDLER``; ``mint_gate_token`` requires both to agree.
    Only ``scripts/gate_approve.py`` (right before minting, cleared right
    after) and tests should enter it.

    This is a discipline boundary, not a privilege boundary: it makes minting
    from pipeline code an explicit, greppable act rather than something a
    stray import or a tool call can do, and it cannot be satisfied by setting
    the environment variable from outside the process (the nonce lives in
    process memory). It does NOT stop a hostile process running as the user —
    that process can enter ``handler_context`` itself, or read the key.
    """
    global _handler_nonce
    previous_nonce = _handler_nonce
    previous_env = os.environ.get(HANDLER_ENV)
    nonce = secrets.token_hex(16)
    _handler_nonce = nonce
    os.environ[HANDLER_ENV] = nonce
    try:
        yield
    finally:
        _handler_nonce = previous_nonce
        if previous_env is None:
            os.environ.pop(HANDLER_ENV, None)
        else:
            os.environ[HANDLER_ENV] = previous_env


def _require_handler() -> None:
    nonce = _handler_nonce
    marker = os.environ.get(HANDLER_ENV)
    if nonce is None or marker is None or not hmac.compare_digest(nonce, marker):
        raise GateHandlerRequired(
            "mint_gate_token is only callable from the gate handler process "
            "(scripts/gate_approve.py, inside gates.handler_context()) — pipeline "
            "code never mints its own approval"
        )


# ---- Tokens ----


def mint_gate_token(
    project_id: str,
    stage: str,
    scope: str,
    record_sha256: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    user_response: Optional[Any] = None,
) -> str:
    """Mint a one-use token bound to the given approval and return it (never stored raw).

    Refuses (``GateHandlerRequired``) unless the process is inside
    ``handler_context()`` — see that function for what this does and does
    not guarantee.
    """
    _require_handler()
    root = gates_dir()
    key = _load_key(root)
    token = secrets.token_urlsafe(32)
    digest = _token_hmac(token, key)
    now = _now()
    binding = GateBinding(
        token_hmac=digest,
        project_id=project_id,
        stage=stage,
        scope=scope,
        record_sha256=record_sha256,
        expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
        minted_at=now.isoformat(),
        user_response=user_response,
    )
    atomic_write_json(root / "pending" / f"{digest}.json", binding.to_dict())
    return token


def consume_gate_token(
    token: str, project_id: str, stage: str, scope: str, record_sha256: str
) -> GateBinding:
    """Verify and atomically consume a token. Raises GateError on any mismatch."""
    root = gates_dir()
    key = _load_key(root)
    digest = _token_hmac(token, key)
    if not _valid_hmac_name(digest):  # pragma: no cover - defensive
        raise GateTokenInvalid("malformed token digest")
    pending = root / "pending" / f"{digest}.json"
    try:
        data = json.loads(pending.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GateTokenInvalid("gate token unknown or already consumed") from None
    binding = GateBinding.from_dict(data)

    expected = (project_id, stage, scope, record_sha256)
    actual = (binding.project_id, binding.stage, binding.scope, binding.record_sha256)
    if not hmac.compare_digest(
        canonical_bytes(list(expected)), canonical_bytes(list(actual))
    ):
        raise GateTokenInvalid("gate token binding does not match this approval")
    if _now() >= datetime.fromisoformat(binding.expires_at):
        raise GateTokenExpired("gate token has expired")

    consumed = root / "consumed" / f"{digest}.json"
    try:
        os.rename(pending, consumed)  # atomic; fails if already consumed concurrently
    except FileNotFoundError:
        raise GateTokenInvalid("gate token already consumed") from None
    return binding


# ---- Receipt signatures ----


def _unsigned(receipt: dict) -> dict:
    return {k: v for k, v in receipt.items() if k != "signature"}


def sign_receipt(receipt: dict) -> str:
    key = _load_key()
    return hmac.new(key, canonical_bytes(_unsigned(receipt)), hashlib.sha256).hexdigest()


def verify_receipt_signature(receipt: dict) -> bool:
    signature = receipt.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    try:
        expected = sign_receipt(receipt)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, signature)


# ---- Ledger ----


def ledger_path() -> Path:
    return gates_dir() / "ledger.jsonl"


def ledger_append(consumed_binding: GateBinding, receipt_id: str) -> None:
    _ensure_dirs(gates_dir())
    append_jsonl(
        ledger_path(),
        {
            "token_hmac": consumed_binding.token_hmac,
            "receipt_id": receipt_id,
            "record_sha256": consumed_binding.record_sha256,
            "project_id": consumed_binding.project_id,
            "recorded_at": _now().isoformat(),
        },
    )


def ledger_has(
    receipt_id: str,
    record_sha256: str,
    project_id: Optional[str] = None,
    token_hmac: Optional[str] = None,
) -> bool:
    """True if a ledger row matches every given field. ``project_id`` and
    ``token_hmac`` are optional filters here; ``verify_receipt`` always
    matches the full tuple."""
    for row in read_jsonl(ledger_path()):
        if row.get("receipt_id") != receipt_id or row.get("record_sha256") != record_sha256:
            continue
        if project_id is not None and row.get("project_id") != project_id:
            continue
        if token_hmac is not None and row.get("token_hmac") != token_hmac:
            continue
        return True
    return False


def verify_receipt(receipt: dict) -> bool:
    """True only if the receipt carries a valid signature AND a ledger entry
    matching its full ``(token_hmac, receipt_id, record_sha256, project_id)``."""
    if not verify_receipt_signature(receipt):
        return False
    fields = tuple(receipt.get(k) for k in ("token_hmac", "receipt_id", "record_sha256", "project_id"))
    if not all(isinstance(f, str) and f for f in fields):
        return False
    token_hmac, receipt_id, record, project_id = fields
    return ledger_has(receipt_id, record, project_id=project_id, token_hmac=token_hmac)


# ---- Generation ledger (PLAN §6; inspection fix #8) ----
#
# Generation receipts live in the project-writable generation-receipts.jsonl.
# To stop a fabricated row from admitting an imported image as synthetic, every
# receipt is signed with the gates key and mirrored here, outside the project.


def generation_ledger_path() -> Path:
    return gates_dir() / "generation-ledger.jsonl"


def generation_ledger_append(receipt: dict) -> None:
    _ensure_dirs(gates_dir())
    append_jsonl(
        generation_ledger_path(),
        {
            "receipt_id": receipt["receipt_id"],
            "output_sha256": receipt["output_sha256"],
            "project_id": receipt["project_id"],
            "execution_id": receipt.get("execution_id"),
            "recorded_at": _now().isoformat(),
        },
    )


def generation_ledger_has(receipt_id: str, output_sha256: str, project_id: str) -> bool:
    for row in read_jsonl(generation_ledger_path()):
        if (
            row.get("receipt_id") == receipt_id
            and row.get("output_sha256") == output_sha256
            and row.get("project_id") == project_id
        ):
            return True
    return False


def verify_generation_receipt(receipt: dict) -> bool:
    """Valid signature AND a generation-ledger row for (receipt_id, output_sha256, project_id)."""
    if not isinstance(receipt, dict) or not verify_receipt_signature(receipt):
        return False
    fields = tuple(receipt.get(k) for k in ("receipt_id", "output_sha256", "project_id"))
    if not all(isinstance(f, str) and f for f in fields):
        return False
    return generation_ledger_has(*fields)


# ---- QC ledger (D19) ----
#
# QC rows (verdict / attempt_started / generation_attached / verdict_attached)
# live in the project-writable qc-receipts.jsonl; every row is signed with the
# gates key and mirrored here, outside the project, keyed by (receipt_id, kind,
# project_id, binding) where ``binding`` is tuple_sha256 for verdicts and
# attempt_id for attempt rows.


def qc_ledger_path() -> Path:
    return gates_dir() / "qc-ledger.jsonl"


def qc_binding(row: dict) -> Optional[str]:
    kind = row.get("kind")
    if kind == "verdict":
        return row.get("tuple_sha256")
    if kind in ("attempt_started", "generation_attached", "verdict_attached"):
        return row.get("attempt_id")
    return None


def qc_ledger_append(row: dict) -> None:
    _ensure_dirs(gates_dir())
    append_jsonl(
        qc_ledger_path(),
        {
            "receipt_id": row["receipt_id"],
            "kind": row["kind"],
            "binding": qc_binding(row),
            "project_id": row["project_id"],
            "recorded_at": _now().isoformat(),
        },
    )


def qc_ledger_has(receipt_id: str, kind: str, binding: str, project_id: str) -> bool:
    for r in read_jsonl(qc_ledger_path()):
        if (
            r.get("receipt_id") == receipt_id and r.get("kind") == kind
            and r.get("binding") == binding and r.get("project_id") == project_id
        ):
            return True
    return False


def verify_qc_row(row: dict) -> bool:
    """Valid signature AND a qc-ledger row for (receipt_id, kind, binding, project_id)."""
    if not isinstance(row, dict) or not verify_receipt_signature(row):
        return False
    binding = qc_binding(row)
    fields = (row.get("receipt_id"), row.get("kind"), binding, row.get("project_id"))
    if not all(isinstance(f, str) and f for f in fields):
        return False
    return qc_ledger_has(*fields)


def qc_wal_dir() -> Path:
    d = gates_dir() / "qc-wal"
    _ensure_dirs(gates_dir())
    d.mkdir(exist_ok=True, mode=0o700)
    return d


def qc_wal_write(tuple_sha256: str, entry: dict) -> Path:
    path = qc_wal_dir() / f"{_checked_execution_id(tuple_sha256)}.json"
    atomic_write_json(path, entry)
    return path


def qc_wal_read(tuple_sha256: str) -> Optional[dict]:
    path = qc_wal_dir() / f"{_checked_execution_id(tuple_sha256)}.json"
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) else None


def qc_wal_entries() -> list[dict]:
    out = []
    for path in sorted(qc_wal_dir().glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def qc_wal_delete(tuple_sha256: str) -> None:
    (qc_wal_dir() / f"{_checked_execution_id(tuple_sha256)}.json").unlink(missing_ok=True)


# ---- Per-project receipt chain (Slice A inspection #1) ----
#
# The project-local approvals.jsonl / generation-receipts.jsonl are writable
# by the agent. Signatures + ledger rows stop a FORGED row, but not a DELETED
# one: dropping a casting receipt clears taint, dropping a retire receipt
# restores an old look. So every append is also linked into a signed,
# orchestrator-owned chain per (project_id, stream): each row names its
# predecessor, and a per-chain ``tip`` file records the head. Readers require
# the local file to reproduce the chain from root to tip EXACTLY (same
# receipts, same digests, same order) and fail closed on the first
# divergence.

CHAIN_STREAMS = ("approval", "generation", "bootstrap", "qc")
# ``qc`` (D19): sheet-QC verdicts and attempt rows (lib.qc_receipts), projected
# from the project-local qc-receipts.jsonl like approval/generation.
# ``bootstrap`` holds at most one row per project: the signed ``chain_bootstrap``
# marker scripts/chain_bootstrap.py writes after adopting pre-chain receipts.
# It has no project-local projection, so it is never verified by
# ``verify_local_projection``.


class ReceiptChainError(GateError):
    """The orchestrator-owned receipt chain is internally inconsistent."""


def chain_dir() -> Path:
    d = gates_dir() / "chains"
    _ensure_dirs(gates_dir())
    d.mkdir(exist_ok=True, mode=0o700)
    return d


def _chain_name(project_id: str, stream: str) -> str:
    if stream not in CHAIN_STREAMS:
        raise ValueError(f"unknown receipt chain stream {stream!r}")
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("receipt chain needs a project_id")
    return f"{hashlib.sha256(project_id.encode('utf-8')).hexdigest()}.{stream}"


def chain_path(project_id: str, stream: str) -> Path:
    return chain_dir() / f"{_chain_name(project_id, stream)}.jsonl"


def chain_tip_path(project_id: str, stream: str) -> Path:
    return chain_dir() / f"{_chain_name(project_id, stream)}.tip.json"


def chain_journal_path(project_id: str, stream: str) -> Path:
    return chain_dir() / f"{_chain_name(project_id, stream)}.journal.json"


# ---- Per-project, per-stream receipt transaction lock (round 2, #2) ----

_SAFE_LOCK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_held_locks = threading.local()


def lock_dir() -> Path:
    d = gates_dir() / "locks"
    _ensure_dirs(gates_dir())
    d.mkdir(exist_ok=True, mode=0o700)
    return d


def lock_path(project_id: str, stream: str) -> Path:
    _chain_name(project_id, stream)  # validates both
    name = project_id if _SAFE_LOCK_NAME.match(project_id) else hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    return lock_dir() / f"{name}.{stream}.lock"


@contextmanager
def receipt_lock(project_id: str, stream: str) -> Iterator[None]:
    """Exclusive interprocess lock (``fcntl.flock``) for every receipt
    transaction on ``(project_id, stream)``: pre-commit validation, local
    append, ledger append, chain append and tip advance all happen under
    it, so two writers can never both pass a uniqueness check or append
    sibling chain rows. Re-entrant within a thread (a pre-commit check may
    read receipts, which may replay the WAL, which commits under the same
    lock); a second thread or process blocks until release."""
    path = lock_path(project_id, stream)
    held: dict = getattr(_held_locks, "held", None)
    if held is None:
        held = _held_locks.held = {}
    key = str(path)
    if key in held:
        held[key][1] += 1
        try:
            yield
        finally:
            held[key][1] -= 1
        return
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        held[key] = [fd, 1]
        try:
            yield
        finally:
            del held[key]
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def receipt_digest(receipt: dict) -> str:
    """sha256 of the canonical JSON of a receipt minus ``signature`` — what a
    chain row binds (the whole signed row: record, envelope, ids)."""
    return hashlib.sha256(canonical_bytes(_unsigned(receipt))).hexdigest()


def _sign_chain_row(row: dict) -> str:
    return hmac.new(_load_key(), canonical_bytes(_unsigned(row)), hashlib.sha256).hexdigest()


def chain_rows(project_id: str, stream: str) -> list[dict]:
    """The verified chain for ``(project_id, stream)`` in root→tip order.
    Raises ReceiptChainError if any row is unsigned, mislinked, or the tip
    file disagrees with the last row."""
    rows: list[dict] = []
    prev: Optional[str] = None
    for i, row in enumerate(read_jsonl(chain_path(project_id, stream))):
        if not isinstance(row, dict):
            raise ReceiptChainError(f"receipt chain {project_id}/{stream} row {i} is not an object")
        sig = row.get("signature")
        if not isinstance(sig, str) or not hmac.compare_digest(_sign_chain_row(row), sig):
            raise ReceiptChainError(f"receipt chain {project_id}/{stream} row {i} has a bad signature")
        if row.get("project_id") != project_id or row.get("stream") != stream:
            raise ReceiptChainError(f"receipt chain {project_id}/{stream} row {i} belongs to another chain")
        if row.get("prev_receipt_id") != prev:
            raise ReceiptChainError(
                f"receipt chain {project_id}/{stream} row {i} ({row.get('receipt_id')}) links to "
                f"{row.get('prev_receipt_id')!r}, expected {prev!r}"
            )
        prev = row.get("receipt_id")
        if not isinstance(prev, str) or not prev:
            raise ReceiptChainError(f"receipt chain {project_id}/{stream} row {i} has no receipt_id")
        rows.append(row)
    tip = chain_tip(project_id, stream)
    if tip != prev:
        if rows and rows[-1].get("prev_receipt_id") == tip:
            # Round 2 #3: a crash between the row append and the tip write left
            # a signed, correctly linked tail; advance the tip instead of
            # bricking the chain. The journal (written before the row) is the
            # trace of that in-flight advancement.
            _recover_dangling_tail(project_id, stream, rows[-1], len(rows))
            return rows
        raise ReceiptChainError(
            f"receipt chain {project_id}/{stream} tip file names {tip!r} but the chain ends at {prev!r}"
        )
    _discard_stale_journal(project_id, stream, prev)
    return rows


def _write_tip(project_id: str, stream: str, receipt_id: str, length: int) -> None:
    atomic_write_json(
        chain_tip_path(project_id, stream),
        {"project_id": project_id, "stream": stream, "tip_receipt_id": receipt_id, "length": length},
    )


def _recover_dangling_tail(project_id: str, stream: str, tail: dict, length: int) -> None:
    with receipt_lock(project_id, stream):
        # Re-read under the lock: another process may have finished the advance.
        current = chain_tip(project_id, stream)
        if current == tail["receipt_id"]:
            pass
        elif current == tail.get("prev_receipt_id"):
            _write_tip(project_id, stream, tail["receipt_id"], length)
        else:
            raise ReceiptChainError(
                f"receipt chain {project_id}/{stream} tip file names {current!r} while the chain ends at "
                f"{tail['receipt_id']!r} — cannot recover"
            )
        chain_journal_path(project_id, stream).unlink(missing_ok=True)


def _discard_stale_journal(project_id: str, stream: str, tip: Optional[str]) -> None:
    """A journal whose row never reached the chain (crash between journal
    and row append) records nothing committed; drop it once the chain and
    tip agree."""
    path = chain_journal_path(project_id, stream)
    if not path.exists():
        return
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entry = None
    row = entry.get("row") if isinstance(entry, dict) else None
    if isinstance(row, dict) and row.get("receipt_id") == tip:
        path.unlink(missing_ok=True)  # advance completed; journal just outlived it
    elif isinstance(row, dict) and row.get("prev_receipt_id") == tip:
        path.unlink(missing_ok=True)  # row never appended; nothing to recover


def chain_tip(project_id: str, stream: str) -> Optional[str]:
    path = chain_tip_path(project_id, stream)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or data.get("project_id") != project_id or data.get("stream") != stream:
        raise ReceiptChainError(f"receipt chain tip file for {project_id}/{stream} is malformed")
    tip = data.get("tip_receipt_id")
    return tip if isinstance(tip, str) and tip else None


def chain_has(project_id: str, stream: str, receipt_id: str) -> bool:
    return any(r.get("receipt_id") == receipt_id for r in chain_rows(project_id, stream))


def chain_append(project_id: str, stream: str, receipt: dict) -> dict:
    """Link ``receipt`` onto the chain (idempotent on receipt_id) and advance
    the tip. The chain is verified before the append so a corrupt chain is
    never extended. Runs under ``receipt_lock`` and journals the advance:
    journal entry → row append → tip write → journal removal, so a crash at
    any point is recoverable by ``chain_rows`` on the next open."""
    with receipt_lock(project_id, stream):
        rows = chain_rows(project_id, stream)
        rid = receipt["receipt_id"]
        for row in rows:
            if row.get("receipt_id") == rid:
                if row.get("record_sha256") != receipt_digest(receipt):
                    raise ReceiptChainError(
                        f"receipt {rid} is already chained with a different digest in {project_id}/{stream}"
                    )
                return row
        row = {
            "project_id": project_id,
            "stream": stream,
            "receipt_id": rid,
            "kind": receipt.get("kind") or ("generation" if stream == "generation" else None),
            "prev_receipt_id": rows[-1]["receipt_id"] if rows else None,
            "record_sha256": receipt_digest(receipt),
            "recorded_at": _now().isoformat(),
        }
        row["signature"] = _sign_chain_row(row)
        atomic_write_json(chain_journal_path(project_id, stream), {"row": row, "length": len(rows) + 1})
        append_jsonl(chain_path(project_id, stream), row)
        _write_tip(project_id, stream, rid, len(rows) + 1)
        chain_journal_path(project_id, stream).unlink(missing_ok=True)
        return row


def verify_local_projection(project_id: str, stream: str, local_receipts: list[dict]) -> None:
    """Require ``local_receipts`` (every row of the project-local file, in
    file order) to reproduce the chain root→tip exactly. Raises
    ReceiptChainError naming the first divergence."""
    chain = chain_rows(project_id, stream)
    label = f"{project_id}/{stream}"
    for i in range(max(len(chain), len(local_receipts))):
        if i >= len(chain):
            row = local_receipts[i]
            rid = row.get("receipt_id") if isinstance(row, dict) else None
            hint = (
                f" — pre-chain receipts? run scripts/chain_bootstrap.py --project {project_id} "
                f"from a terminal to adopt them"
                if not chain
                else " — extra or forged row"
            )
            raise ReceiptChainError(
                f"{label}: local receipt file row {i} ({rid!r}) is not in the signed chain "
                f"(chain has {len(chain)} receipts){hint}"
            )
        expected = chain[i]
        if i >= len(local_receipts):
            raise ReceiptChainError(
                f"{label}: chained receipt {expected['receipt_id']} ({expected.get('kind')}) at "
                f"position {i} is missing from the local receipt file — a receipt was deleted"
            )
        row = local_receipts[i]
        if not isinstance(row, dict):
            raise ReceiptChainError(f"{label}: local receipt file row {i} is not an object")
        if row.get("receipt_id") != expected["receipt_id"]:
            raise ReceiptChainError(
                f"{label}: local receipt file row {i} is {row.get('receipt_id')!r} but the chain "
                f"has {expected['receipt_id']} ({expected.get('kind')}) there — missing, extra or "
                f"reordered receipt"
            )
        if receipt_digest(row) != expected["record_sha256"]:
            raise ReceiptChainError(
                f"{label}: local receipt {expected['receipt_id']} at position {i} does not hash to "
                f"its chained digest — the row was altered"
            )


# ---- Approval write-ahead log (inspection fix #18) ----


def wal_dir() -> Path:
    d = gates_dir() / "wal"
    _ensure_dirs(gates_dir())
    d.mkdir(exist_ok=True, mode=0o700)
    return d


def wal_write(token_hmac: str, entry: dict) -> Path:
    path = wal_dir() / f"{token_hmac}.json"
    atomic_write_json(path, entry)
    return path


def wal_entries() -> list[dict]:
    out = []
    for path in sorted(wal_dir().glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def wal_delete(token_hmac: str) -> None:
    (wal_dir() / f"{token_hmac}.json").unlink(missing_ok=True)


# ---- Generation write-ahead log (crash-safe paid completion) ----
#
# A paid tool writes ``generation-wal/<execution_id>.json`` the moment a
# verified output lands in staging (sha256 + every field a receipt needs).
# Completion is receipt → ledger → terminal reservation → WAL delete, so a
# crash anywhere in between is replayed by lib.receipts.recover_generation_wal.

_EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _checked_execution_id(execution_id: str) -> str:
    if not isinstance(execution_id, str) or not _EXECUTION_ID_RE.match(execution_id):
        raise ValueError(f"unsafe execution_id for the generation WAL: {execution_id!r}")
    return execution_id


def generation_wal_dir() -> Path:
    d = gates_dir() / "generation-wal"
    _ensure_dirs(gates_dir())
    d.mkdir(exist_ok=True, mode=0o700)
    return d


def generation_wal_write(execution_id: str, entry: dict) -> Path:
    path = generation_wal_dir() / f"{_checked_execution_id(execution_id)}.json"
    atomic_write_json(path, entry)
    return path


def generation_wal_read(execution_id: str) -> Optional[dict]:
    path = generation_wal_dir() / f"{_checked_execution_id(execution_id)}.json"
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) else None


def generation_wal_entries() -> list[dict]:
    out = []
    for path in sorted(generation_wal_dir().glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def generation_wal_delete(execution_id: str) -> None:
    (generation_wal_dir() / f"{_checked_execution_id(execution_id)}.json").unlink(missing_ok=True)
