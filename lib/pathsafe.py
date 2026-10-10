"""Path safety and content-addressed storage for project assets (PLAN §4, §7).

- Inputs are resolved strictly, must live inside the project root, and no
  component of the path (below the root) may be a symlink.
- Outputs go through a unique staging file under ``<project_root>/.staging/``,
  are deterministically re-encoded (images) and hashed, then atomically moved
  to ``<objects_dir>/<sha256><ext>``.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from lib.state_io import atomic_move, atomic_write_bytes

STAGING_DIRNAME = ".staging"
IMAGE_EXTS = {".png"}
_PNG_COMPRESS_LEVEL = 6


class PathSafetyError(Exception):
    """Raised when a path escapes the project root or crosses a symlink."""


def _resolved_root(project_root: Path | str) -> Path:
    root = Path(project_root)
    try:
        return root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PathSafetyError(f"project root does not exist: {root}") from exc


def _lexical_inside(candidate: Path, root: Path) -> Path:
    """Return the ``..``-collapsed absolute candidate; raise if it lexically escapes root."""
    normalized = Path(os.path.normpath(str(candidate)))
    try:
        normalized.relative_to(root)
    except ValueError:
        raise PathSafetyError(f"path escapes project root: {candidate}") from None
    return normalized


def _reject_symlink_components(normalized: Path, root: Path) -> None:
    rel = normalized.relative_to(root)
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise PathSafetyError(f"symlink in path is not allowed: {current}")


def _candidate(path: Path | str, root: Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    return p


def resolve_input(path: Path | str, project_root: Path | str) -> Path:
    """Strictly resolve an input path; it must exist inside ``project_root`` with no symlinks."""
    root = _resolved_root(project_root)
    normalized = _lexical_inside(_candidate(path, root), root)
    _reject_symlink_components(normalized, root)
    try:
        resolved = normalized.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PathSafetyError(f"input does not exist: {normalized}") from exc
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PathSafetyError(f"resolved path escapes project root: {resolved}") from None
    return resolved


def validate_output_parent(path: Path | str, project_root: Path | str) -> Path:
    """Validate that an output path's parent exists inside the root and is not a symlink.

    Returns the normalized absolute output path.
    """
    root = _resolved_root(project_root)
    normalized = _lexical_inside(_candidate(path, root), root)
    parent = normalized.parent
    _reject_symlink_components(parent, root)
    if not parent.is_dir():
        raise PathSafetyError(f"output parent does not exist: {parent}")
    if normalized.is_symlink():
        raise PathSafetyError(f"output path is a symlink: {normalized}")
    return normalized


def staging_file(project_root: Path | str, suffix: str) -> Path:
    """Create a unique, empty staging file under ``<project_root>/.staging/`` and return its path."""
    root = _resolved_root(project_root)
    staging = root / STAGING_DIRNAME
    staging.mkdir(parents=True, exist_ok=True)
    if staging.is_symlink():
        raise PathSafetyError(f"staging directory is a symlink: {staging}")
    fd, name = tempfile.mkstemp(prefix="stage-", suffix=suffix, dir=str(staging))
    os.close(fd)
    return Path(name)


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reencode_png_bytes(source: Path | str) -> bytes:
    """Decode any PIL-readable image and re-encode deterministically as PNG.

    Pixels are copied into a fresh image (dropping ICC/DPI/text metadata),
    mode is normalized to RGB or RGBA, and the encoder uses a fixed
    compression level with no pnginfo.
    """
    import io

    from PIL import Image

    with Image.open(source) as im:
        im.load()
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (
            im.mode == "P" and "transparency" in im.info
        )
        target_mode = "RGBA" if has_alpha else "RGB"
        converted = im.convert(target_mode)
        clean = Image.frombytes(target_mode, converted.size, converted.tobytes())
    buf = io.BytesIO()
    clean.save(buf, format="PNG", compress_level=_PNG_COMPRESS_LEVEL, optimize=False)
    return buf.getvalue()


def store_content_addressed(
    staging_path: Path | str, objects_dir: Path | str, ext: str
) -> tuple[str, Path]:
    """Move a staged file into ``objects_dir`` under its content hash.

    Images (``ext == '.png'``) are decoded and re-encoded deterministically
    before hashing so JPEG/WebP/PNG inputs with identical pixels share an id.
    If the destination already exists it must be byte-identical; the staging
    file is then discarded. Returns ``(asset_id, final_path)``.
    """
    staging_path = Path(staging_path)
    objects_dir = Path(objects_dir)
    if not ext.startswith("."):
        ext = "." + ext
    objects_dir.mkdir(parents=True, exist_ok=True)

    if ext.lower() in IMAGE_EXTS:
        data = reencode_png_bytes(staging_path)
        normalized = staging_path.with_name(staging_path.name + ".reencoded.png")
        atomic_write_bytes(normalized, data)
        source = normalized
        asset_id = hashlib.sha256(data).hexdigest()
    else:
        source = staging_path
        asset_id = sha256_file(staging_path)

    final_path = objects_dir / f"{asset_id}{ext}"
    try:
        if final_path.exists():
            if sha256_file(final_path) != asset_id:
                raise PathSafetyError(
                    f"existing object {final_path} does not match its content hash"
                )
        else:
            atomic_move(source, final_path)
    finally:
        for leftover in {source, staging_path}:
            try:
                Path(leftover).unlink()
            except FileNotFoundError:
                pass
    return asset_id, final_path
