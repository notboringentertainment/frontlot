"""Repo-wide test guards.

Gate/receipt code (lib.gates, lib.receipts) writes an HMAC key, ledgers and a
WAL under ``$OPENMONTAGE_GATES_DIR`` (default ``~/.openmontage/gates``).
Generation receipts are signed with that key, so any test that records one —
including tool tests that never think about gates — must never touch the
real per-install key. Point every test at a private directory by default;
tests that need their own directory override it with monkeypatch.setenv.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_gates_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setenv(
        "OPENMONTAGE_GATES_DIR", str(tmp_path_factory.mktemp("gates"))
    )
