# backlot/claude_ops.py
"""The only pipeline operations Front Lot's embedded Claude can request.

Claude names an operation and gives parameters; each operation's adapter builds
the exact argv itself, inserting the film in that script's own form, reading
every file Claude names exactly once (no symlinks, inside the work area),
snapshotting those bytes, and listing what a paid run depends on so it can be
frozen (spec §4.2). Nothing here imports the tool registry on the request path.
"""
from __future__ import annotations

import errno, hashlib, json, math, os, re, stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from lib.run_common import ENTITY_ID_RE
from scripts.headshot_run import GENERATION_PRICE_USD as HEADSHOT_PRICE_USD, MAX_CANDIDATES
from scripts.sheet_run import GENERATION_PRICE_USD as SHEET_PRICE_USD
from tools.qa.sheet_judge import DEFAULT_RESERVE_USD
from lib.run_common import ABSENT

PY = ".venv/bin/python"
SHEET_ROLES = ("turnaround", "expressions", "wardrobe")      # lib.sheet_qc.policy.SHEET_ROLES
SHOT_TOOLS = ("seedream_image", "seedance_video", "kling_reference_video")  # supervised_production.SUPPORTED_TOOLS
ORIGIN_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
MANDATORY_SHEET_ROLES = ("turnaround", "expressions")     # lib.sheet_qc.verify.MANDATORY_ROLES


def _up(usd: float) -> float:
    """Round an upper bound up to the cent (never down)."""
    return math.ceil(round(usd * 100, 6)) / 100


class OpError(ValueError):
    pass


@dataclass(frozen=True)
class Prepared:
    op: str
    paid: bool
    argv: list[str]
    inputs: dict[Path, str]
    snapshot: dict[str, Path] = field(default_factory=dict)
    summary: str = ""
    entity: str | None = None
    estimate_usd: float | None = None


@dataclass(frozen=True)
class Ctx:
    op: str
    repo: Path
    slug: str
    film: Path
    work: Path
    snap: Path


@dataclass(frozen=True)
class Operation:
    paid: bool
    allowed: frozenset[str]
    build: Callable[[Ctx, dict], Prepared]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def input_digest(path: Path) -> str:
    p = Path(path)
    return sha256_bytes(p.read_bytes()) if p.is_file() else ABSENT


# -- reading files exactly once ---------------------------------------------------------------
def _read_beneath(root: Path, rel: str) -> bytes:
    """Read a regular file under root, opening every component with O_NOFOLLOW (no symlink, no escape)."""
    p = Path(rel) if isinstance(rel, str) and rel else None
    if p is None or p.is_absolute() or any(part in ("..", ".") for part in p.parts):
        raise OpError("name a file inside the film's Front Lot work area")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in p.parts[:-1]:
            nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = nxt
        ffd = os.open(p.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)  # NONBLOCK: a named pipe must not hang the open
    except OSError:
        raise OpError(f"no plain file named {rel} in the work area") from None
    finally:
        os.close(fd)
    with os.fdopen(ffd, "rb") as fh:  # fstat before any read: only a plain file is ever read
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OpError(f"{rel} is not a plain file")
        return fh.read()


def _snapshot(ctx: Ctx, key: str, rel) -> tuple[Path, bytes, str]:
    data = _read_beneath(ctx.work, rel)
    ctx.snap.mkdir(parents=True, exist_ok=True)
    dst = ctx.snap / f"{key}{Path(rel).suffix}"
    dst.write_bytes(data)
    return dst, data, sha256_bytes(data)


def _read_record(path: Path) -> tuple[bytes | None, str]:
    """A film record (Claude cannot write it): read once; the digest is of these bytes."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, ABSENT
    return data, sha256_bytes(data)


def _expect(path: Path, digest: str) -> str:
    return f"--expect-input-sha={path}={digest}"


# -- lookups the tests replace (they read signed canon, receipts and config) -------------------
def _config(film: Path):
    from lib.project_config import ProjectConfigError, load_verified_project_config
    try:
        return load_verified_project_config(film)
    except ProjectConfigError as exc:
        raise OpError(f"the film's settings aren't approved: {exc}") from None


def _config_digest(film: Path) -> str:
    return _config(film).digest   # sha256 of the project.yaml bytes the verification used


def _hero_budget(film: Path, entity: str, look_hash: str) -> tuple[int, int]:
    """(attempts left under the signed cap, started attempts not yet judged or voided)."""
    from lib import qc_receipts as qr
    from lib.project_config import ProjectConfigError
    try:
        cap = int(_config(film).require_hero_qc().max_hero_attempts)
    except ProjectConfigError as exc:
        raise OpError(str(exc)) from None
    from lib.receipts import ReceiptError
    try:
        started = qr.hero_attempts_started(film, entity, look_hash)
        closed = {r.get("attempt_id") for r in qr.rows_of_kind(film, "verdict_attached")}
        closed |= {r.get("attempt_id") for r in qr.rows_of_kind(film, "attempt_voided")}
    except (qr.QCReceiptError, ReceiptError) as exc:
        raise OpError(f"the record of headshot attempts can't be read, so the cost can't be worked out: {exc}") from None
    return max(cap - len(started), 0), sum(1 for r in started if r.get("attempt_id") not in closed)


def _sheet_cap(film: Path) -> int:
    from lib.project_config import ProjectConfigError
    try:
        return int(_config(film).require_qc().max_attempts_per_series)
    except ProjectConfigError as exc:
        raise OpError(str(exc)) from None


def _active_look_hash(film: Path, entity: str) -> str:
    from lib.look_ingest import LookIngestError, active_look_for
    try:
        look = active_look_for(film, "character", entity)
    except LookIngestError as exc:
        raise OpError(f"the look for {entity} can't be read: {exc}") from None
    if look is None:
        raise OpError(f"{entity} has no locked look yet; lock the look first")
    return look.look_hash


def _active_headshot(film: Path, entity: str) -> str:
    from lib.headshots import HeadshotError, active_headshots
    try:
        head = active_headshots(film).get(entity)
    except HeadshotError as exc:
        raise OpError(f"the headshots can't be read: {exc}") from None
    if head is None:
        raise OpError(f"{entity} has no approved headshot yet")
    return head.receipt_id


def _brief_revision(film: Path, shot: str) -> str:
    from lib.supervised_production import read_brief
    try:
        brief = read_brief(film, shot)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise OpError(f"shot {shot} can't be read: {exc}") from None
    if brief is None or brief.get("stopped"):
        raise OpError(f"shot {shot} has no active brief")
    rev = brief.get("revision_id") if isinstance(brief, dict) else None
    if not isinstance(rev, str) or not rev:
        raise OpError(f"shot {shot} has no readable brief revision")
    return rev


# -- parameter checks ---------------------------------------------------------------------------
def _entity(params: dict) -> str:
    e = params.get("entity")
    if not isinstance(e, str) or e.startswith("-") or not ENTITY_ID_RE.fullmatch(e):
        raise OpError("entity must be a lowercase id like hero-a")
    return e


def _note(params: dict) -> str:
    n = params.get("note")
    if not isinstance(n, str) or not n.strip():
        raise OpError("a note saying what Ben asked for is required")
    return n


def _shot_id(ctx: Ctx, params: dict) -> str:
    from lib.supervised_production import shot_dir
    shot = params.get("shot_id")
    try:
        shot_dir(ctx.film, shot)  # the script's own rule: letters, numbers, _ and -
    except (ValueError, TypeError):
        raise OpError("shot_id must contain only letters, numbers, underscores and hyphens") from None
    return shot


def _writing_file(ctx: Ctx, p: Path) -> bool:
    """True for a file in the film's writing folder (project.yaml wayfinder_root: canon note, workflow notes,
    look tickets), named by its absolute path and reached without links. Claude cannot write there, and prepare
    snapshots each source by hash, so nothing can be swapped in after this check. False if the film names no
    writing folder or the path is outside it; a bad path inside it is an error."""
    from lib.look_ingest import LookIngestError, wayfinder_root_for
    from lib.pathsafe import PathSafetyError, resolve_input
    try:
        root = wayfinder_root_for(ctx.film)
    except LookIngestError:
        return False
    if ".." in p.parts:
        raise OpError(f"the brief may not cite paths with '..': {p}")
    try:
        p.relative_to(root)
    except ValueError:
        return False
    try:
        resolved = resolve_input(p, root)
    except PathSafetyError:
        raise OpError(f"the brief may not cite this file in the writing folder (missing or through a link): {p}") from None
    if not resolved.is_file():
        raise OpError(f"no such file in the film's writing folder: {p}")
    return True


def _film_file(ctx: Ctx, raw, writing: bool = False) -> None:
    """A path the privileged shot script will read (production.prepare resolves even absolute paths).
    Only film files outside the work area, named relative to the film, reached without links. Claude cannot
    write the film folder outside the work area, so nothing can be swapped in after this check."""
    p = Path(raw) if isinstance(raw, str) and raw else None
    if writing and p is not None and p.is_absolute() and _writing_file(ctx, p):
        return
    if p is None or p.is_absolute() or ".." in p.parts or not p.parts:
        raise OpError("the brief may only cite the film's own files, by a path inside the film folder")
    if p.parts[0] == "frontlot-work":
        raise OpError("the brief may not cite the work area; cite the film's own files")
    cur = ctx.film
    for part in p.parts:
        cur = cur / part
        if cur.is_symlink():
            raise OpError(f"the brief may not cite files through links: {raw}")
        try:
            # identity, not spelling: on a case-insensitive disk FrontLot-Work is the work area too
            if ctx.work.exists() and cur.exists() and os.path.samefile(cur, ctx.work):
                raise OpError("the brief may not cite the work area; cite the film's own files")
        except OSError:
            pass
    if not cur.is_file():
        raise OpError(f"no such film file: {raw}")


# -- adapters -----------------------------------------------------------------------------------
def _script(name: str) -> list[str]:
    return [PY, f"scripts/{name}.py"]


def _look(ctx, p):
    e = _entity(p)
    kind = p.get("kind", "character")
    if kind not in ("character", "location"):
        raise OpError("kind must be character or location")
    argv = _script("look_run") + ["--project", ctx.slug, "--entity", e, "--kind", kind]
    source = p.get("source")
    if source is not None:
        if source not in ("auto", "writeros", "wayfinder"):
            raise OpError("source must be auto, writeros, or wayfinder")
        argv += ["--source", source]
    if p.get("supersede"):
        argv.append("--supersede")
    if p.get("dry_run"):
        argv.append("--dry-run")
    summary = f"Preview the look for {e} (nothing is locked)" if p.get("dry_run") else f"Lock the look for {e}"
    return Prepared(ctx.op, False, argv, {}, summary=summary, entity=e)


def _hero_frozen(ctx, e) -> tuple[list[str], dict[Path, str], str]:
    cp = ctx.film / "checkpoint_headshots.json"
    _, digest = _read_record(cp)
    look = _active_look_hash(ctx.film, e)
    flags = [_expect(cp, digest), "--expect-look-hash", look, "--expect-config-sha", _config_digest(ctx.film)]
    return flags, {cp: digest}, look


def _headshot_candidates(ctx, p):
    e = _entity(p)
    n = p.get("candidates", 3)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_CANDIDATES:
        raise OpError(f"candidates must be 1 to {MAX_CANDIDATES}")
    flags, inputs, look = _hero_frozen(ctx, e)
    argv = _script("headshot_run") + ["--project", ctx.slug, "--entity", e, "--candidates", str(n), *flags]
    if p.get("palette"):
        pal = p["palette"]
        if not isinstance(pal, list) or not all(isinstance(h, str) and h.strip() and "," not in h for h in pal):
            raise OpError("palette must be a list of colour words")
        argv += ["--palette", ",".join(pal)]
    left, unjudged = _hero_budget(ctx.film, e, look)
    most = left * (HEADSHOT_PRICE_USD + DEFAULT_RESERVE_USD) + unjudged * DEFAULT_RESERVE_USD
    return Prepared(ctx.op, True, argv, inputs, summary=f"Make {n} headshot candidates for {e}",
                    entity=e, estimate_usd=_up(most))


def _headshot_finish(ctx, p):
    e = _entity(p)
    flags, inputs, look = _hero_frozen(ctx, e)
    argv = _script("headshot_run") + ["--project", ctx.slug, "--entity", e, "--finish", *flags]
    left, unjudged = _hero_budget(ctx.film, e, look)
    most = left * (HEADSHOT_PRICE_USD + DEFAULT_RESERVE_USD) + (unjudged + 1) * DEFAULT_RESERVE_USD
    return Prepared(ctx.op, True, argv, inputs, summary=f"Finish the headshot round for {e}",
                    entity=e, estimate_usd=_up(most))


def _headshot_import(ctx, p):
    e = _entity(p)
    origin = p.get("origin_tool")
    if not isinstance(origin, str) or not ORIGIN_TOOL_RE.fullmatch(origin):
        raise OpError("origin_tool must name the tool that made the picture, e.g. midjourney")
    cp = ctx.film / "checkpoint_headshots.json"
    data, digest = _read_record(cp)
    try:
        state = ((json.loads(data or b"{}").get("metadata") or {}).get("run_state") or {}).get(e)
    except ValueError:
        raise OpError("the headshot record can't be read") from None
    if state:
        # headshot_run resumes run state before it looks at --import (headshot_run.py:272-274); a resume can judge
        # or regenerate, which is paid. A free import must never get there.
        raise OpError(f"a headshot round for {e} is in progress; finish or decline it before importing")
    snap, _, img_digest = _snapshot(ctx, "import", p.get("image"))
    argv = _script("headshot_run") + ["--project", ctx.slug, "--entity", e, "--import", str(snap),
                                      "--origin-tool", origin, _expect(cp, digest)]
    return Prepared(ctx.op, False, argv, {cp: digest, snap: img_digest}, {"image": snap},
                    summary=f"Import a headshot for {e}", entity=e)


def _sheet(ctx, p):
    e = _entity(p)
    cp = ctx.film / "checkpoint_visual_bible.json"
    _, digest = _read_record(cp)
    argv = _script("sheet_run") + ["--project", ctx.slug, "--entity", e, _expect(cp, digest),
                                   "--expect-look-hash", _active_look_hash(ctx.film, e),
                                   "--expect-config-sha", _config_digest(ctx.film),
                                   "--expect-headshot", _active_headshot(ctx.film, e)]
    roles = p.get("roles")
    if roles is not None:
        if not isinstance(roles, list) or not roles or not all(r in SHEET_ROLES for r in roles):
            raise OpError("roles must be some of turnaround, expressions, wardrobe")
        argv += ["--roles", ",".join(roles)]
    if p.get("resume"):
        argv.append("--resume")
    cap = _sheet_cap(ctx.film)
    priced = set(roles or []) | set(MANDATORY_SHEET_ROLES)   # a subset run can regenerate rejected mandatory roles
    most = sum(cap * (SHEET_PRICE_USD[r] + DEFAULT_RESERVE_USD) for r in priced)
    return Prepared(ctx.op, True, argv, {cp: digest}, summary=f"Make the character sheet for {e}", entity=e,
                    estimate_usd=_up(most))


def _simple(script: str, flag: str, summary: str):
    def build(ctx, p):
        e = _entity(p)
        return Prepared(ctx.op, False, _script(script) + ["--project", ctx.slug, "--entity", e, flag], {},
                        summary=summary.format(e=e), entity=e)
    return build


def _shot(sub: str, paid: bool = False):
    def build(ctx, p):
        argv = [PY, "-m", "scripts.supervised_shot", str(ctx.film), sub]
        inputs, snap, summary, estimate = {}, {}, f"Shot step: {sub}", None
        if sub == "prepare":
            s, data, digest = _snapshot(ctx, "brief", p.get("brief"))
            try:
                brief = json.loads(data)
            except ValueError:
                raise OpError("the brief must be JSON") from None
            if not isinstance(brief, dict):
                raise OpError("the brief must be a JSON object")
            sources, refs = brief.get("source_paths") or [], brief.get("reference_manifest") or []
            if not isinstance(sources, list) or not all(isinstance(x, str) for x in sources):
                raise OpError("source_paths must be a list of film file paths")
            if not isinstance(refs, list) or not all(isinstance(x, dict) for x in refs):
                raise OpError("reference_manifest must be a list of entries that each name a path")
            for raw in sources:
                _film_file(ctx, raw, writing=True)   # sources only: references are stored film-relative
            for ref in refs:
                _film_file(ctx, ref.get("path"))
            argv += [str(s), "--note", _note(p)]; inputs, snap = {s: digest}, {"brief": s}
            summary = "Prepare a shot from Claude's brief"
        else:
            shot = _shot_id(ctx, p)
            argv.append(shot)
            if sub in ("request", "generate"):
                s, _, digest = _snapshot(ctx, "settings", p.get("settings"))
                argv.append(str(s)); inputs, snap = {s: digest}, {"settings": s}
            if sub == "generate":
                tool = p.get("tool")
                if tool not in SHOT_TOOLS:
                    raise OpError("tool must be seedream_image, seedance_video, or kling_reference_video")
                argv += ["--tool", tool, "--expect-brief-revision", _brief_revision(ctx.film, shot)]
                summary = f"Generate shot {shot}"   # estimate stays None: "cost unknown", Go still required
            if sub == "select":
                take = p.get("take_id")
                if not isinstance(take, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", take):
                    raise OpError("take_id must contain only letters, numbers, underscores and hyphens")
                argv.append(take)
            if sub in ("stop", "select", "propose"):
                argv += ["--note", _note(p)]
        return Prepared(ctx.op, paid, argv, inputs, snap, summary=summary, estimate_usd=estimate)
    return build


OPERATIONS: dict[str, Operation] = {
    "look": Operation(False, frozenset({"entity", "kind", "source", "supersede", "dry_run"}), _look),
    "headshot_candidates": Operation(True, frozenset({"entity", "candidates", "palette"}), _headshot_candidates),
    "headshot_finish": Operation(True, frozenset({"entity"}), _headshot_finish),
    "headshot_import": Operation(False, frozenset({"entity", "image", "origin_tool"}), _headshot_import),
    "sheet": Operation(True, frozenset({"entity", "roles", "resume"}), _sheet),
    "sheet_finish": Operation(False, frozenset({"entity"}), _simple("sheet_run", "--finish", "Finish the sheet for {e}")),
    "sheet_abandon": Operation(False, frozenset({"entity"}), _simple("sheet_run", "--abandon", "Abandon the sheet for {e}")),
    "shot_prepare": Operation(False, frozenset({"brief", "note"}), _shot("prepare")),
    "shot_request": Operation(False, frozenset({"shot_id", "settings"}), _shot("request")),
    "shot_generate": Operation(True, frozenset({"shot_id", "settings", "tool"}), _shot("generate", paid=True)),
    "shot_inspect": Operation(False, frozenset({"shot_id"}), _shot("inspect")),
    "shot_stop": Operation(False, frozenset({"shot_id", "note"}), _shot("stop")),
    "shot_select": Operation(False, frozenset({"shot_id", "take_id", "note"}), _shot("select")),
    "shot_propose": Operation(False, frozenset({"shot_id", "note"}), _shot("propose")),
}


def prepare(op: str, params: dict, *, repo: Path, film_slug: str, film_root: Path, snapshot_dir: Path) -> Prepared:
    spec = OPERATIONS.get(op)
    if spec is None:
        raise OpError(f"unknown operation: {op}")
    if not isinstance(params, dict):
        raise OpError("params must be an object")
    extra = set(params) - spec.allowed
    if extra:
        raise OpError(f"not allowed for {op}: {', '.join(sorted(extra))}")
    ctx = Ctx(op, repo, film_slug, film_root, film_root / "frontlot-work", snapshot_dir)
    prepared = spec.build(ctx, params)
    if prepared.paid != spec.paid:  # one source of truth for paid/free
        raise OpError(f"{op} is misconfigured")
    return prepared
