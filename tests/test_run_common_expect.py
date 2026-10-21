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


def test_frozen_file_is_reverified_on_every_read_until_thawed(tmp_path):
    p = tmp_path / "cp.json"; p.write_bytes(b"one")
    exp = Expectations(parse_expectations([f"{p}={_sha(b'one')}"]))
    assert exp.read(p) == b"one"
    p.write_bytes(b"two")  # changed after the first read
    with pytest.raises(InputChanged):
        exp.read(p)
    p.write_bytes(b"one"); assert exp.read(p) == b"one"
    p.write_bytes(b"the run's own write"); exp.thaw(p)
    assert exp.read(p) == b"the run's own write"


def test_symlink_or_directory_at_a_frozen_path_is_a_change(tmp_path):
    target = tmp_path / "real"; target.write_bytes(b"x")
    link = tmp_path / "link.json"; link.symlink_to(target)
    with pytest.raises(InputChanged):
        Expectations({link: _sha(b"x")}).read(link)
    d = tmp_path / "dir.json"; d.mkdir()
    with pytest.raises(InputChanged):
        Expectations({d: ABSENT}).read(d)
    assert Expectations().read(link) == b"x"  # unfrozen paths read normally


def test_frozen_path_never_read_is_refused(tmp_path):
    p = tmp_path / "cp.json"; p.write_bytes(b"x")
    exp = Expectations({p: _sha(b"x")})
    with pytest.raises(InputChanged, match="never checked"):
        exp.require_all_verified()
    exp.read(p); exp.require_all_verified()


@pytest.mark.parametrize("bad", ["", "  "])
def test_empty_expectation_value_is_refused_not_ignored(bad):
    with pytest.raises(ValueError):
        Expectations(values={"look": bad})
    assert Expectations(values={"look": None}).values == {}
