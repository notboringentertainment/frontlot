"""D19.1 — deterministic pre-checks that need no model.

Only what is actually deterministic: regular file (no symlink), PNG that
decodes, size/aspect within the role's tolerance, no alpha, bytes hash to the
asset id. Panel counting, crop detection and OCR are NOT attempted here (a
plain-background sheet defeats naive heuristics; a wrong deterministic answer
is worse than a model's ``unsure``). Rules live in policy.LOCAL_RULES so they
are part of the policy bundle hash.
"""
from __future__ import annotations

import hashlib
import io
import os
import stat
from pathlib import Path
from typing import Any

from lib.sheet_qc.policy import LOCAL_RULES, local_rules_for


class LocalCheckError(ValueError):
    """The asset failed a deterministic rule; ``item`` names it."""

    def __init__(self, item: str, message: str) -> None:
        super().__init__(message)
        self.item = item


def read_asset_bytes(objects_dir: Path, asset_id: str) -> bytes:
    """Open ``<objects_dir>/<asset_id>.png`` with O_NOFOLLOW, require a regular
    file, read once, hash the buffer, require it to equal ``asset_id`` (Codex
    R1#7). The returned buffer is the ONLY thing a judge may be shown."""
    if not isinstance(asset_id, str) or len(asset_id) != 64 or any(c not in "0123456789abcdef" for c in asset_id):
        raise LocalCheckError("asset_id", f"asset_id must be a 64-hex sha256, got {asset_id!r}")
    path = Path(objects_dir) / f"{asset_id}.png"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise LocalCheckError("file", f"cannot open {path}: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise LocalCheckError("file", f"{path} is not a regular file")
        if st.st_size > int(LOCAL_RULES["max_bytes"]):
            raise LocalCheckError("file", f"{path} exceeds {LOCAL_RULES['max_bytes']} bytes")
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            data = fh.read()
    finally:
        if fd >= 0:
            os.close(fd)
    if hashlib.sha256(data).hexdigest() != asset_id:
        raise LocalCheckError("hash", f"{path} does not hash to its asset id")
    return data


def check_image(role: str, data: bytes) -> dict[str, Any]:
    """Decode and apply the role's size rules (from the role's own bundle:
    sheet roles read LOCAL_RULES, ``hero`` reads HERO_LOCAL_RULES). Returns
    ``{width, height}``."""
    from PIL import Image

    rules = local_rules_for(role)
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as exc:  # noqa: BLE001
        raise LocalCheckError("decode", f"not a decodable image: {exc}") from exc
    if (im.format or "").lower() != rules["format"]:
        raise LocalCheckError("format", f"expected {rules['format']}, got {im.format}")
    if not rules["alpha_allowed"] and (im.mode in ("RGBA", "LA") or "transparency" in im.info):
        raise LocalCheckError("alpha", "alpha channel not allowed on a sheet")
    w, h = im.size
    rule = rules["size"].get(role)
    if rule is None:
        raise LocalCheckError("role", f"no local size rule for role {role!r}")
    if "width" in rule:
        tol = float(rule["tolerance"])
        if abs(w - rule["width"]) > rule["width"] * tol or abs(h - rule["height"]) > rule["height"] * tol:
            raise LocalCheckError("size", f"{role} must be {rule['width']}x{rule['height']} ±{tol:.0%}, got {w}x{h}")
    if "aspect" in rule:
        tol = float(rule["tolerance"])
        if abs((w / h) - rule["aspect"]) > rule["aspect"] * tol:
            raise LocalCheckError("aspect", f"{role} aspect must be {rule['aspect']} ±{tol:.0%}, got {w / h:.3f}")
    if rule.get("orientation") == "square_or_portrait" and w > h:
        raise LocalCheckError("orientation", f"{role} must be square or portrait, got {w}x{h} (landscape)")
    if "min_long_edge" in rule and max(w, h) < int(rule["min_long_edge"]):
        raise LocalCheckError("size", f"{role} long edge must be ≥ {rule['min_long_edge']}, got {max(w, h)}")
    return {"width": w, "height": h}
