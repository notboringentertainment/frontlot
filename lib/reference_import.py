"""Reference-image import (plan Slice A′ steps 1, 5, 6; D16/D17).

Two origin classes, one deterministic normalization, one gate:

- ``imported_synthetic`` — an image generated elsewhere. Imported through
  the ``reference_import`` gate with the fixed attestation
  ``SYNTHETIC_ATTESTATION``; after approval ``finalize_reference_import``
  stores the normalized PNG under ``canon/visual/objects/<sha>.png`` and
  records a generation receipt ``generator_kind: imported`` (a legitimate
  lineage root).
- ``casting_inspiration`` — a real person. Stored under
  ``canon/visual/casting-inspiration/<sha>.png``, shown to the human only.
  Its normalized pixel hash is in the project-wide TAINT SET, derived from
  the verified reference_import receipts on every check: never a lineage
  root, never in a reference manifest, never uploaded; an import of the same
  pixels under any other class is refused (R6#6, R7#4).

Normalization (R6#9), identical for JPEG / PNG / HEIC: decode (HEIC via
pillow-heif — missing library is an explicit error, never a silent skip),
reject multi-frame inputs, enforce byte and pixel limits, apply EXIF
orientation, convert to sRGB 8-bit (embedded ICC applied then dropped),
alpha flattened onto white for JPEG/HEIC sources and preserved for PNG,
re-encode PNG with fixed parameters and NO metadata chunks, hash the result.
The staged source file is deleted on every path (including failure) and the
original bytes are never copied into the project.
"""

from __future__ import annotations

import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

REFERENCE_IMPORT_KIND = "reference_import"
ORIGIN_IMPORTED_SYNTHETIC = "imported_synthetic"
ORIGIN_CASTING = "casting_inspiration"
ORIGIN_CLASSES = (ORIGIN_IMPORTED_SYNTHETIC, ORIGIN_CASTING)
SYNTHETIC_ATTESTATION = "machine-generated, depicts no real person"
CASTING_ATTESTATION = (
    "real-person casting inspiration; shown to the human only; never uploaded; never lineage"
)
ATTESTATIONS = {ORIGIN_IMPORTED_SYNTHETIC: SYNTHETIC_ATTESTATION, ORIGIN_CASTING: CASTING_ATTESTATION}
OBJECTS_DIR = Path("canon/visual/objects")
CASTING_DIR = Path("canon/visual/casting-inspiration")
STAGING_DIR = Path(".staging")

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 40_000_000
PNG_COMPRESS_LEVEL = 6
NORMALIZER_VERSION = "reference-normalize/1"

_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1", b"avif"}


class ReferenceImportError(RuntimeError):
    """The import cannot proceed (format, limits, missing decoder, taint)."""


class TaintError(ReferenceImportError):
    """A casting_inspiration hash appears where only synthetic lineage may."""


@dataclass(frozen=True)
class NormalizedImage:
    png_bytes: bytes
    sha256: str
    width: int
    height: int
    source_format: str
    has_alpha: bool


# ---- format sniffing ----


def sniff_format(data: bytes) -> str:
    """'png' | 'jpeg' | 'heif' | 'unknown' from magic bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "heif"
    return "unknown"


def _ensure_heif_decoder() -> None:
    try:
        import pillow_heif  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise ReferenceImportError(
            "HEIC/HEIF input needs the pillow-heif package (pip install pillow-heif); "
            "refusing to import without it"
        ) from exc
    pillow_heif.register_heif_opener()


# ---- normalization ----


def normalize_image_bytes(data: bytes) -> NormalizedImage:
    """The deterministic normalization described in the module docstring."""
    from PIL import Image, ImageOps

    if len(data) > MAX_SOURCE_BYTES:
        raise ReferenceImportError(f"source is {len(data)} bytes; limit is {MAX_SOURCE_BYTES}")
    fmt = sniff_format(data)
    if fmt == "unknown":
        raise ReferenceImportError("source is not a PNG, JPEG or HEIC/HEIF file")
    if fmt == "heif":
        _ensure_heif_decoder()

    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        try:
            im = Image.open(io.BytesIO(data))
        except Image.DecompressionBombError as exc:
            raise ReferenceImportError(f"source exceeds the pixel limit {MAX_PIXELS}: {exc}") from exc
        except Exception as exc:
            raise ReferenceImportError(f"source could not be decoded: {exc}") from exc
        with im:
            frames = getattr(im, "n_frames", 1)
            if frames and frames > 1:
                raise ReferenceImportError(f"multi-frame/animated input ({frames} frames) is rejected")
            if im.width * im.height > MAX_PIXELS:
                raise ReferenceImportError(
                    f"source is {im.width}x{im.height} ({im.width * im.height} px); limit is {MAX_PIXELS}"
                )
            try:
                im.load()
            except Image.DecompressionBombError as exc:
                raise ReferenceImportError(f"source exceeds the pixel limit {MAX_PIXELS}: {exc}") from exc
            except Exception as exc:
                raise ReferenceImportError(f"source could not be decoded: {exc}") from exc
            upright = ImageOps.exif_transpose(im) or im
            icc = upright.info.get("icc_profile")
            source_alpha = upright.mode in ("RGBA", "LA", "PA") or (
                upright.mode == "P" and "transparency" in upright.info
            )
            keep_alpha = source_alpha and fmt == "png"
            work = upright.convert("RGBA" if source_alpha else "RGB")
            if icc:
                work = _apply_icc_to_srgb(work, icc)
            if source_alpha and not keep_alpha:
                white = Image.new("RGBA", work.size, (255, 255, 255, 255))
                white.alpha_composite(work)
                work = white.convert("RGB")
            target_mode = "RGBA" if keep_alpha else "RGB"
            if work.mode != target_mode:
                work = work.convert(target_mode)
            clean = Image.frombytes(target_mode, work.size, work.tobytes())
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    buf = io.BytesIO()
    # Fresh image (no .info), no pnginfo, fixed compression: no EXIF/GPS/ICC/XMP/text chunks.
    clean.save(buf, format="PNG", compress_level=PNG_COMPRESS_LEVEL, optimize=False)
    png = buf.getvalue()
    import hashlib

    return NormalizedImage(
        png_bytes=png,
        sha256=hashlib.sha256(png).hexdigest(),
        width=clean.width,
        height=clean.height,
        source_format=fmt,
        has_alpha=keep_alpha,
    )


def _apply_icc_to_srgb(image: Any, icc: bytes) -> Any:
    from PIL import ImageCms

    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        out = ImageCms.profileToProfile(image, src, dst, outputMode=image.mode)
    except Exception as exc:
        # Slice A #13: an unreadable profile is NOT silently reinterpreted as
        # sRGB — the approved colors would drift. Refuse the import.
        raise ReferenceImportError(
            f"embedded ICC profile could not be read or applied ({exc}); the source's encoded "
            f"colors cannot be normalized to sRGB — re-export the image with a valid profile or "
            f"without one"
        ) from exc
    if out is None:
        raise ReferenceImportError("embedded ICC profile could not be applied (conversion returned nothing)")
    out.info.pop("icc_profile", None)
    return out


def normalize_staged_file(source_path: Path | str) -> NormalizedImage:
    """Normalize a staged source file and DELETE it on every path."""
    source_path = Path(source_path)
    try:
        data = source_path.read_bytes()
        return normalize_image_bytes(data)
    finally:
        try:
            source_path.unlink()
        except FileNotFoundError:
            pass


# ---- taint set (derived from the ledger every time) ----


def reference_import_receipts(project_dir: Path | str, *, project_id: Optional[str] = None) -> list[dict]:
    from lib.receipts import verified_approvals

    return verified_approvals(project_dir, REFERENCE_IMPORT_KIND, project_id=project_id)


def tainted_hashes(project_dir: Path | str, *, project_id: Optional[str] = None) -> set[str]:
    """Every casting_inspiration normalized pixel hash in the verified ledger."""
    return {
        str(r.get("normalized_pixel_hash"))
        for r in reference_import_receipts(project_dir, project_id=project_id)
        if r.get("origin_class") == ORIGIN_CASTING
    }


def assert_untainted(
    project_dir: Path | str, hashes: Iterable[str], *, project_id: Optional[str] = None, label: str = "reference"
) -> None:
    """Raise TaintError if any hash is casting_inspiration. Tools call this
    before any upload; enforcement calls it on every lineage node."""
    tainted = tainted_hashes(project_dir, project_id=project_id)
    hit = sorted(h for h in hashes if h in tainted)
    if hit:
        raise TaintError(
            f"{label} touches casting_inspiration pixels {hit} — a real-person image is shown to the "
            f"human only; it is never uploaded, never a lineage root, never a reference"
        )


def origin_class_of(project_dir: Path | str, pixel_hash: str, *, project_id: Optional[str] = None) -> Optional[str]:
    """The origin class already bound to ``pixel_hash`` by a verified import receipt, if any."""
    classes = {
        str(r.get("origin_class"))
        for r in reference_import_receipts(project_dir, project_id=project_id)
        if r.get("normalized_pixel_hash") == pixel_hash
    }
    if not classes:
        return None
    if len(classes) > 1:
        raise TaintError(f"pixel hash {pixel_hash} is bound to conflicting origin classes {sorted(classes)}")
    return classes.pop()


def refuse_conflicting_origin(
    project_dir: Path | str, pixel_hash: str, origin_class: str, *, project_id: Optional[str] = None
) -> None:
    existing = origin_class_of(project_dir, pixel_hash, project_id=project_id)
    if existing is not None and existing != origin_class:
        raise TaintError(
            f"pixel hash {pixel_hash} was already imported as {existing}; importing identical pixels "
            f"as {origin_class} is refused (origin is permanent)"
        )


def assert_origin_unique_for_signing(
    project_dir: Path | str, pixel_hash: str, origin_class: str, *, project_id: Optional[str] = None
) -> None:
    """Slice A #14: re-derive the origin bound to ``pixel_hash`` from the
    verified ledger and refuse a conflicting class. Run by the gate handler
    INSIDE the approval transaction (after the one-use token is consumed,
    before the receipt is signed) so two pending requests for the same
    pixels can never both land."""
    refuse_conflicting_origin(project_dir, pixel_hash, origin_class, project_id=project_id)


def synthetic_import_receipt(
    project_dir: Path | str, pixel_hash: str, *, project_id: Optional[str] = None
) -> Optional[dict]:
    """The verified imported_synthetic reference_import receipt for ``pixel_hash``."""
    match = None
    for r in reference_import_receipts(project_dir, project_id=project_id):
        if r.get("normalized_pixel_hash") == pixel_hash and r.get("origin_class") == ORIGIN_IMPORTED_SYNTHETIC:
            match = r
    return match


# ---- the import gate ----


IMPORT_RECORD_FIELDS = {
    ORIGIN_CASTING: ("origin_class", "normalized_pixel_hash", "attestation_text", "normalizer_version"),
    ORIGIN_IMPORTED_SYNTHETIC: (
        "origin_class", "normalized_pixel_hash", "attestation_text", "normalizer_version", "origin_tool",
    ),
}


def import_record(origin_class: str, pixel_hash: str, *, origin_tool: Optional[str] = None) -> dict[str, Any]:
    """The record hashed into a reference_import receipt: the normalized
    pixel hash, the FIXED attestation string for the class, the normalizer
    version and (imported_synthetic only) the origin tool. Nothing the caller
    typed — no filename, source name or note — is ever persisted (Slice A
    #12): a casting_inspiration receipt must not outlive the pixels with a
    real person's name attached."""
    if origin_class not in ORIGIN_CLASSES:
        raise ReferenceImportError(f"origin_class must be one of {ORIGIN_CLASSES}")
    if not isinstance(pixel_hash, str) or len(pixel_hash) != 64 or set(pixel_hash) - set("0123456789abcdef"):
        raise ReferenceImportError("normalized_pixel_hash must be a 64-hex sha256")
    record: dict[str, Any] = {
        "origin_class": origin_class,
        "normalized_pixel_hash": pixel_hash,
        "attestation_text": ATTESTATIONS[origin_class],
        "normalizer_version": NORMALIZER_VERSION,
    }
    if origin_class == ORIGIN_IMPORTED_SYNTHETIC:
        if not isinstance(origin_tool, str) or not origin_tool.strip():
            raise ReferenceImportError("imported_synthetic imports must name origin_tool")
        record["origin_tool"] = origin_tool
    elif origin_tool is not None:
        raise ReferenceImportError("casting_inspiration imports carry no origin_tool (no caller metadata at all)")
    return record


def validate_import_record(record: Any) -> dict[str, Any]:
    """Require ``record`` to be EXACTLY what ``import_record`` builds for its
    class: fixed attestation, known normalizer, no extra keys."""
    if not isinstance(record, dict):
        raise ReferenceImportError("reference_import record must be an object")
    origin_class = record.get("origin_class")
    if origin_class not in ORIGIN_CLASSES:
        raise ReferenceImportError(f"origin_class must be one of {ORIGIN_CLASSES}")
    expected = import_record(origin_class, str(record.get("normalized_pixel_hash")), origin_tool=record.get("origin_tool"))
    if record != expected:
        extra = sorted(set(record) - set(expected))
        raise ReferenceImportError(
            f"reference_import record is not the canonical {origin_class} record"
            + (f" (unexpected fields {extra})" if extra else " (attestation/normalizer mismatch)")
        )
    return expected


@dataclass(frozen=True)
class PreparedImport:
    origin_class: str
    normalized_pixel_hash: str
    staged_path: Path
    request_path: Path
    record: dict[str, Any]


def stage_reference_upload(project_dir: Path | str, original: Path | str) -> Path:
    """Copy the user's original into ``.staging/`` so ``prepare_reference_import``
    can consume (and delete) it. The original outside the project is untouched."""
    import shutil

    project_dir = Path(project_dir)
    staging = project_dir / STAGING_DIR
    staging.mkdir(parents=True, exist_ok=True)
    dest = staging / f"reference-source-{uuid.uuid4().hex}{Path(original).suffix.lower()}"
    shutil.copyfile(original, dest)
    return dest


def prepare_reference_import(
    project_dir: Path | str,
    project_id: str,
    source_path: Path | str,
    *,
    origin_class: str,
    origin_tool: Optional[str] = None,
    request_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    **ignored_caller_metadata: Any,
) -> PreparedImport:
    """Normalize a staged source (deleting it), refuse origin conflicts, stage
    the normalized PNG, and write the gate request. Mints nothing. Any extra
    keyword (``source_name`` and the like) is dropped on the floor: caller
    metadata never reaches the request or the record (#12)."""
    project_dir = Path(project_dir)
    if origin_class not in ORIGIN_CLASSES:
        raise ReferenceImportError(f"origin_class must be one of {ORIGIN_CLASSES}")
    normalized = normalize_staged_file(source_path)
    refuse_conflicting_origin(project_dir, normalized.sha256, origin_class)
    record = import_record(origin_class, normalized.sha256, origin_tool=origin_tool if origin_class == ORIGIN_IMPORTED_SYNTHETIC else None)

    from lib.state_io import atomic_write_bytes

    staging = project_dir / STAGING_DIR
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"reference-{normalized.sha256}.png"
    atomic_write_bytes(staged, normalized.png_bytes)

    request_id = request_id or f"reference-import-{normalized.sha256[:12]}"
    request = {
        "request_id": request_id,
        "project_id": project_id,
        "stage": "look_lock",
        "scope": f"reference:{normalized.sha256}",
        "kind": REFERENCE_IMPORT_KIND,
        "entity_id": entity_id or f"reference-{normalized.sha256[:12]}",
        "artifact": None,
        "approval_record": record,
        "envelope": {"origin_class": origin_class, "normalized_pixel_hash": normalized.sha256},
        "source_checkpoint_digest": None,
        "summary": (
            f"Import a {origin_class} reference ({normalized.width}x{normalized.height}, from "
            f"{normalized.source_format}; normalized pixel hash {normalized.sha256}). Attestation: "
            f"{ATTESTATIONS[origin_class]!r}."
            + (" This image will NEVER be sent to a model or director and can never enter lineage."
               if origin_class == ORIGIN_CASTING else
               f" Origin tool: {origin_tool}. Becomes a lineage root on approval.")
        ),
        "preview_paths": [str(staged.relative_to(project_dir))],
    }
    req_dir = project_dir / ".gate-requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    request_path = req_dir / f"{request_id}.json"
    request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
    return PreparedImport(origin_class, normalized.sha256, staged, request_path, record)


@dataclass(frozen=True)
class ImportedReference:
    origin_class: str
    normalized_pixel_hash: str
    path: Path
    attestation_receipt_id: str
    import_receipt_id: Optional[str]


def record_imported_generation(
    project_dir: Path | str,
    *,
    output_sha256: str,
    origin_tool: str,
    attestation_receipt_id: str,
    execution_id: Optional[str] = None,
    tool: str = "reference_import",
    project_id: Optional[str] = None,
) -> dict:
    """Append the signed ``generator_kind: imported`` generation receipt for
    an approved imported_synthetic reference. Verifies the attestation
    receipt (origin imported_synthetic, matching pixel hash) and the taint
    set first; this is the ONLY way an imported image becomes a lineage root."""
    from lib.receipts import record_generation

    assert_untainted(project_dir, [output_sha256], project_id=project_id, label="imported reference")
    receipt = synthetic_import_receipt(project_dir, output_sha256, project_id=project_id)
    if receipt is None or receipt.get("receipt_id") != attestation_receipt_id:
        raise ReferenceImportError(
            f"no verified imported_synthetic reference_import receipt {attestation_receipt_id!r} "
            f"binds pixel hash {output_sha256}"
        )
    now = datetime.now(timezone.utc).isoformat()
    return record_generation(
        project_dir,
        execution_id=execution_id or f"import-{output_sha256[:12]}-{uuid.uuid4().hex[:8]}",
        tool=tool,
        normalized_inputs_hash=output_sha256,
        output_sha256=output_sha256,
        cost_usd=0.0,
        started_at=now,
        finished_at=now,
        generator_kind="imported",
        origin_tool=origin_tool,
        attestation_receipt_id=attestation_receipt_id,
    )


def finalize_reference_import(
    project_dir: Path | str, receipt_id: str, *, project_id: Optional[str] = None
) -> ImportedReference:
    """After the human approved the request: move the staged PNG into its
    class directory and, for imported_synthetic, ledger the imported
    generation receipt. Verifies the staged bytes hash to the receipt."""
    from lib.pathsafe import sha256_file
    from lib.state_io import atomic_move

    project_dir = Path(project_dir)
    receipt = next(
        (r for r in reference_import_receipts(project_dir, project_id=project_id) if r.get("receipt_id") == receipt_id),
        None,
    )
    if receipt is None:
        raise ReferenceImportError(f"no verified reference_import receipt {receipt_id!r}")
    origin_class = str(receipt.get("origin_class"))
    pixel_hash = str(receipt.get("normalized_pixel_hash"))
    refuse_conflicting_origin(project_dir, pixel_hash, origin_class, project_id=project_id)
    target_dir = project_dir / (OBJECTS_DIR if origin_class == ORIGIN_IMPORTED_SYNTHETIC else CASTING_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / f"{pixel_hash}.png"
    staged = project_dir / STAGING_DIR / f"reference-{pixel_hash}.png"
    try:
        if final.exists():
            if sha256_file(final) != pixel_hash:
                raise ReferenceImportError(f"existing object {final} does not hash to {pixel_hash}")
        else:
            if not staged.is_file():
                raise ReferenceImportError(f"staged normalized image {staged} is missing")
            if sha256_file(staged) != pixel_hash:
                raise ReferenceImportError(f"staged image {staged} does not hash to the receipt's {pixel_hash}")
            atomic_move(staged, final)
    finally:
        try:
            staged.unlink()
        except FileNotFoundError:
            pass
    import_receipt_id = None
    if origin_class == ORIGIN_IMPORTED_SYNTHETIC:
        from lib.receipts import find_generation

        existing = find_generation(project_dir, pixel_hash, project_id=project_id)
        if existing is not None and existing.get("generator_kind") == "imported":
            import_receipt_id = existing["receipt_id"]
        else:
            row = record_imported_generation(
                project_dir,
                output_sha256=pixel_hash,
                origin_tool=str((receipt.get("record") or {}).get("origin_tool")),
                attestation_receipt_id=receipt_id,
                project_id=project_id,
            )
            import_receipt_id = row["receipt_id"]
    return ImportedReference(origin_class, pixel_hash, final, receipt_id, import_receipt_id)


def imported_image_ref(project_dir: Path | str, imported: ImportedReference, *, role: str = "hero") -> dict[str, Any]:
    """A schema-valid ImageRef (imported provenance branch) for an approved
    imported_synthetic reference."""
    if imported.origin_class != ORIGIN_IMPORTED_SYNTHETIC or not imported.import_receipt_id:
        raise ReferenceImportError("only an imported_synthetic reference can be an ImageRef")
    from lib.receipts import find_generation

    row = find_generation(project_dir, imported.normalized_pixel_hash)
    if row is None or row.get("receipt_id") != imported.import_receipt_id:
        raise ReferenceImportError("imported generation receipt not found")
    return {
        "asset_id": imported.normalized_pixel_hash,
        "path": str(imported.path.relative_to(Path(project_dir))),
        "role": role,
        "provenance": {
            "generator_kind": "imported",
            "origin_tool": row.get("origin_tool"),
            "attestation_receipt_id": imported.attestation_receipt_id,
            "import_receipt_id": imported.import_receipt_id,
            "normalized_pixel_hash": imported.normalized_pixel_hash,
            "generation_receipt_id": imported.import_receipt_id,
        },
    }


# ---- recursive lineage (D3 restated; R2#9 / R3#3) ----

MAX_LINEAGE_DEPTH = 16


def verify_lineage(
    project_dir: Path | str,
    asset_id: str,
    *,
    receipts_by_sha: Optional[dict[str, dict]] = None,
    project_id: Optional[str] = None,
    label: str = "asset",
) -> list[str]:
    """Prove every branch of ``asset_id``'s ancestry ends at an allowed root.

    Depth-first over ``input_asset_ids`` / ``references_applied`` with an
    explicit path stack (Slice A #5): a cycle is a violation (never "already
    verified"), every node needs a verified generation receipt, no node may
    be tainted, parents must be 64-hex strings (anything else is rejected,
    not skipped), and every ROOT must be a pipeline model/local receipt with
    no parents or an attested ``imported`` receipt whose reference_import
    receipt is imported_synthetic. Provenance is immutable per output hash
    (``lib.receipts``), so a later receipt cannot replace an earlier one.
    Returns the visited hashes in first-visit order; raises on violation.
    """
    if receipts_by_sha is None:
        from lib.receipts import provenance_of, verified_generation_receipts

        receipts_by_sha = {}
        for row in verified_generation_receipts(project_dir, project_id=project_id):
            sha = row["output_sha256"]
            prior = receipts_by_sha.get(sha)
            if prior is not None and provenance_of(prior) != provenance_of(row):
                raise ReferenceImportError(
                    f"{label}: output {sha} carries two generation receipts with different provenance "
                    f"({prior.get('receipt_id')} vs {row.get('receipt_id')}) — lineage is ambiguous, refused"
                )
            receipts_by_sha.setdefault(sha, row)
    if not isinstance(asset_id, str) or len(asset_id) != 64 or set(asset_id) - _HEX:
        raise ReferenceImportError(f"{label}: asset id {asset_id!r} is not a 64-hex sha256")
    tainted = tainted_hashes(project_dir, project_id=project_id)
    visited: list[str] = []
    done: set[str] = set()
    path: list[str] = []
    on_path: set[str] = set()

    def parents_of(receipt: dict, sha: str) -> list[str]:
        raw = list(receipt.get("input_asset_ids") or [])
        refs = receipt.get("references_applied") or []
        if not isinstance(refs, list):
            raise ReferenceImportError(f"{label}: node {sha} has a malformed references_applied")
        for r in refs:
            if not isinstance(r, dict):
                raise ReferenceImportError(f"{label}: node {sha} has a non-object reference {r!r}")
            raw.append(r.get("asset_id"))
        out: list[str] = []
        for parent in raw:
            if not isinstance(parent, str) or len(parent) != 64 or set(parent) - _HEX:
                raise ReferenceImportError(
                    f"{label}: node {sha} names parent {parent!r}, which is not a 64-hex sha256 — rejected"
                )
            out.append(parent)
        return out

    # explicit DFS: stack of (sha, iterator over parents) so the current path is known
    def enter(sha: str) -> list[str]:
        if sha in on_path:
            cycle = path[path.index(sha):] + [sha]
            raise ReferenceImportError(f"{label}: lineage cycle {' -> '.join(cycle)} — refused")
        if sha in tainted:
            raise TaintError(f"{label}: lineage node {sha} is casting_inspiration pixels — refused")
        if len(path) >= MAX_LINEAGE_DEPTH:
            raise ReferenceImportError(f"{label}: lineage deeper than {MAX_LINEAGE_DEPTH} at {sha}")
        receipt = receipts_by_sha.get(sha)
        if receipt is None:
            raise ReferenceImportError(
                f"{label}: lineage node {sha} has no verified generation receipt — every ancestor "
                f"must be a receipted pipeline output or an attested imported_synthetic import"
            )
        kind = receipt.get("generator_kind")
        parents = parents_of(receipt, sha)
        if kind == "imported":
            attestation = synthetic_import_receipt(project_dir, sha, project_id=project_id)
            if attestation is None or attestation.get("receipt_id") != receipt.get("attestation_receipt_id"):
                raise ReferenceImportError(
                    f"{label}: imported node {sha} has no verified imported_synthetic reference_import "
                    f"receipt {receipt.get('attestation_receipt_id')!r}"
                )
            if parents:
                raise ReferenceImportError(f"{label}: imported node {sha} cannot have inputs")
            return []
        if kind not in ("model", "local"):
            raise ReferenceImportError(f"{label}: node {sha} has generator_kind {kind!r}")
        return parents

    stack: list[tuple[str, list[str], int]] = []
    visited.append(asset_id)
    stack.append((asset_id, enter(asset_id), 0))
    path.append(asset_id)
    on_path.add(asset_id)
    while stack:
        sha, parents, i = stack[-1]
        if i < len(parents):
            stack[-1] = (sha, parents, i + 1)
            child = parents[i]
            if child in done:
                continue
            if child not in visited:
                visited.append(child)
            kids = enter(child)
            path.append(child)
            on_path.add(child)
            stack.append((child, kids, 0))
            continue
        stack.pop()
        path.pop()
        on_path.discard(sha)
        done.add(sha)
    return visited


_HEX = frozenset("0123456789abcdef")
