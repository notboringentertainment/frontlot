import hashlib

import pytest

from lib.run_common import ABSENT, Expectations, InputChanged, RunError, parse_expectations


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_verified_bytes_are_the_bytes_hashed(tmp_path):
    p = tmp_path / "cp.json"; body = b'{"a":1}'; p.write_bytes(body)
    digest = _sha(body)
    exp = Expectations(parse_expectations([f"{p}={digest}"]))
    assert exp.read(p) == body


def test_changed_input_refused(tmp_path):
    p = tmp_path / "cp.json"; p.write_bytes(b"old")
    digest = _sha(b"old")
    exp = Expectations(parse_expectations([f"{p}={digest}"]))
    p.write_bytes(b"new")
    with pytest.raises(InputChanged, match="changed after it was approved"):
        exp.read(p)


def test_absent_is_a_frozen_state(tmp_path):
    p = tmp_path / "cp.json"
    assert Expectations(parse_expectations([f"{p}={ABSENT}"])).read(p) is None
    p.write_bytes(b"appeared")
    with pytest.raises(InputChanged):
        Expectations(parse_expectations([f"{p}={ABSENT}"])).read(p)


def test_unlisted_path_reads_normally(tmp_path):
    p = tmp_path / "x"; p.write_bytes(b"x")
    assert Expectations().read(p) == b"x"


def test_bad_expectation_rejected():
    with pytest.raises(ValueError):
        parse_expectations(["no-equals-sign"])
    with pytest.raises(ValueError):
        parse_expectations(["/x=nothex"])


def test_input_changed_is_a_run_error_so_scripts_print_it_plainly():
    assert issubclass(InputChanged, RunError)


def test_value_checks():
    exp = Expectations(values={"look": "a" * 64, "config": "c" * 64})
    exp.check("look", "a" * 64); exp.check("headshot", "anything")  # headshot not frozen
    with pytest.raises(InputChanged, match="look changed"):
        exp.check("look", "b" * 64)
    with pytest.raises(InputChanged, match="signed project settings changed"):
        exp.check("config", "d" * 64)
