"""Settings, brief, environment, and argv for Front Lot's embedded Claude.

Everything here is pure (apart from reading .env* files to find credential
paths). The sandbox and permission rules are the session's enforced boundary
(spec §4.1): Claude may read the film and the repo, write only its work area,
reach only WriterOS on localhost, and use exactly one MCP tool. Native tools
run in "dontAsk" mode: whatever is not allowed is refused, never prompted.
"""
from __future__ import annotations

import json, os, re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

MIN_LIVE_VERSION = (2, 1, 288)
PLUGIN_NAME = "frontlot-live"
TOOL_NAME = f"mcp__{PLUGIN_NAME}__frontlot_run"
WRITEROS_HOST = "127.0.0.1:5177"
EMPTY_MCP = '{"mcpServers":{}}'
CHROME_TOOLS = "mcp__claude-in-chrome"
_ENV_KEEP = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "SHELL", "TMPDIR")
# protects: signing key, ledgers, signer and broker sockets, provider and cloud keys
HOME_SECRET_DIRS = (
    "~/.openmontage", "~/.config/gcloud", "~/.codex", "~/.aws", "~/.ssh",
    "~/.claude",        # protects: Claude Code's own auth/session state and settings, which Bash must not read or alter
    "~/.config/gh",     # protects: GitHub CLI tokens
    "~/.docker",        # protects: container registry credentials
    "~/Library/Keychains",  # protects: saved passwords and API keys (probe P1 (d): `security` read them)
)
HOME_SECRET_FILES = (
    "~/.netrc",
    "~/.claude.json",      # protects: Claude Code account and MCP configuration
    "~/.git-credentials",  # protects: stored git passwords and tokens
    "~/.npmrc",            # protects: npm registry auth tokens
)
SKIP_DIRS = {".venv", "node_modules", ".git"}
# protects: later launches/resumes trusting files Claude wrote (settings, hooks, MCP servers, memory)
WORK_PROTECTED = (".claude/**", ".mcp.json", "CLAUDE.md", "CLAUDE.local.md", ".git/**")
CREDENTIAL_VAR = re.compile(r"(_CREDENTIALS|_KEY_FILE|_KEY_PATH)$")
DENIAL = re.compile(r"not permitted|denied|blocked|refused|not allowed", re.I)


def work_dir(film_root: Path) -> Path:
    d = film_root / "frontlot-work"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _dotenv_pairs(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def secret_paths(repo_root: Path, environ: Mapping[str, str]) -> list[Path]:
    repo = repo_root.resolve()
    env_files = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        env_files += [(Path(root) / f).resolve() for f in files if f.startswith(".env")]
    env_files.sort()
    found = set(env_files)
    for source in [dict(environ)] + [_dotenv_pairs(p) for p in env_files]:
        for key, value in source.items():
            if not (CREDENTIAL_VAR.search(key) and value):
                continue
            p = Path(os.path.expanduser(value))
            p = p if p.is_absolute() else repo / p
            if p.is_file():
                found.add(p.resolve())
    return sorted(found)


def writeros_package(film_root: Path) -> Path | None:
    """The film's linked WriterOS package (project.yaml: writeros_package), checked the way the look
    step checks it; None when the film has no usable link. WriterOS's web library needs Ben's browser
    session, so this folder is where Claude reads promoted looks."""
    from lib.look_ingest import LookIngestError, writeros_package_for   # deferred: pulls in yaml

    try:
        return writeros_package_for(film_root)
    except LookIngestError:
        return None


def build_settings(*, repo_root: Path, film_root: Path, meta_root: Path, environ: Mapping[str, str]) -> dict:
    repo, film, meta = repo_root.resolve(), film_root.resolve(), meta_root.resolve()
    work = film / "frontlot-work"
    secrets = secret_paths(repo, environ)
    package = writeros_package(film)
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,           # protects: never run unsandboxed
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,   # protects: no dangerouslyDisableSandbox escape
            "excludedCommands": [],
            "filesystem": {
                "denyRead": [*HOME_SECRET_DIRS, *HOME_SECRET_FILES, str(meta), *map(str, secrets)],
                # protects: Bash writing files that later launches or resumes would trust
                "denyWrite": [str(work / n.removesuffix("/**")) for n in WORK_PROTECTED],
            },
            "network": {
                "allowedDomains": [WRITEROS_HOST],  # protects: no route to paid services
                "strictAllowlist": True,
                "allowUnixSockets": [],             # protects: signer and broker sockets
                "allowLocalBinding": False,
            },
        },
        "permissions": {
            "defaultMode": "dontAsk",             # protects: anything not allowed below is refused, never prompted
            "allow": [TOOL_NAME, f"Read(/{film}/**)", f"Read(/{repo}/**)", f"Edit(/{work}/**)"]
                     + ([f"Read(/{package}/**)"] if package else []),
            # protects: Ben's Chrome; backs up --no-chrome (probe P2: the allowlist did not stop it)
            "deny": [CHROME_TOOLS] + ([f"Read({d}/**)" for d in HOME_SECRET_DIRS] + [f"Read({f})" for f in HOME_SECRET_FILES]
                     + [f"Read(/{meta}/**)", f"Read(/{repo}/**/.env*)"] + [f"Read(/{p})" for p in secrets]
                     + [f"Edit(/{work}/{name})" for name in WORK_PROTECTED]),
        },
        "enableAllProjectMcpServers": False,
    }


def _operations_text() -> str:
    from backlot.claude_ops import OPERATIONS   # deferred: claude_ops pulls in the pipeline scripts

    return "\n".join(f"- {name} ({'paid' if spec.paid else 'free'}): {', '.join(sorted(spec.allowed))}"
                     for name, spec in OPERATIONS.items())


def _writeros_text(package: Path | None) -> str:
    if package is None:
        return ("- This film is not linked to WriterOS yet (no writeros_package in its project.yaml), so you cannot see "
                "looks Ben promoted there. Say so plainly when it matters; linking it is Ben's step.")
    return (f"- Looks Ben promoted in WriterOS are in this film's WriterOS package, {package}: the newest "
            "memory/exports/look-locks-<revision>.json. Read them with the Read tool. WriterOS's web pages need Ben's "
            "login, so do not try to reach WriterOS over the network.")


def build_brief(*, film_title: str, film_slug: str, writeros_package: Path | None = None) -> str:
    return f"""You are working inside Front Lot, Ben's app for making a film's visuals.
Film: "{film_title}" (project id: {film_slug}). Your working folder is this film's Front Lot work area.

How you work here:
- You can read the film (../ is the film folder) and the OpenMontage repo, and think, plan, and talk with Ben. You can write only in your work area.
{_writeros_text(writeros_package)}
- You never run pipeline scripts yourself. Every pipeline step goes through the frontlot_run tool with an operation name and parameters. Front Lot runs it.
  Call it as {{"op": "<name>", "params": {{...}}}}. These are the only operations; each line gives its cost and the parameters it accepts:
{_operations_text()}
- Free steps run right away. Paid steps show Ben a spend card; wait for his answer. "Not now" is a decision: do not ask again unless he brings it up.
- If a paid step fails with a message saying WriterOS changed canon the job uses (it ends "Apply the change before generating? Nothing is spent until you answer." and names an appeal ticket file), nothing was spent. Quote it to Ben in plain words, then ask exactly "Apply the change before generating?" and never ask him which version to use: WriterOS already decided. On yes, call frontlot_run {{"op": "appeal_apply", "params": {{"ticket": "<the ticket file name from the message>"}}}}, tell him which step brings Front Lot's references up to date (the closed ticket names it), then retry the paid step. On not yet, spend nothing and leave the ticket open.
- If frontlot_run says it didn't confirm receipt, check it with frontlot_run {{"check": "<key>"}}. Never resubmit. If Front Lot says it can't tell whether something ran, tell Ben plainly and do not retry.
- Signing approvals is Ben's alone. Never attempt it, never ask for a way around it. Point him to "Needs you".
- Speak plainly: no hashes, ids, file paths, or command lines unless Ben asks.

When the session opens or picks back up, give a short check-in: what is new (looks promoted in WriterOS, approvals waiting, runs that finished), what is ready to make next, in a few lines. Do not call frontlot_run during the check-in, not even a free step or a dry run: report only. Then wait for Ben to ask.
"""


def allowed_env(base: Mapping[str, str], *, login_path: str, live_socket: str, live_token: str) -> dict[str, str]:
    env = {k: base[k] for k in _ENV_KEEP if k in base}
    env.update({"PATH": login_path, "TERM": "xterm-256color",
                "FRONTLOT_LIVE_SOCKET": live_socket, "FRONTLOT_LIVE_TOKEN": live_token})
    return env


def launch_argv(*, claude: str, mod_dir: Path, settings_file: Path, brief_file: Path,
                session_id: str, resume: bool, prompt: str) -> list[str]:
    # --mcp-config is variadic: it must be followed by an option, never by the prompt.
    argv = [claude, "--no-chrome", "--plugin-dir", str(mod_dir), "--strict-mcp-config", "--mcp-config", EMPTY_MCP,
            "--settings", str(settings_file), "--setting-sources", "",
            "--append-system-prompt-file", str(brief_file)]
    argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    argv.append(prompt)
    return argv


# --- tool-level evidence (probes and the self-check judge tool results, never prose) ---------
@dataclass(frozen=True)
class ToolEvent:
    name: str
    input: dict
    result: str
    is_error: bool


def tool_events(stream: str) -> list[ToolEvent]:
    uses: dict[str, tuple[str, dict]] = {}
    out: list[ToolEvent] = []
    for line in stream.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = d.get("message") if isinstance(d, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                uses[b.get("id")] = (str(b.get("name")), b.get("input") or {})
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in uses:
                name, inp = uses[b["tool_use_id"]]
                c = b.get("content")
                text = c if isinstance(c, str) else " ".join(
                    x.get("text", "") for x in (c or []) if isinstance(x, dict))
                out.append(ToolEvent(name, inp, text, bool(b.get("is_error"))))
    return out


def refused(ev: ToolEvent) -> bool:
    return bool(DENIAL.search(ev.result))


# --- fail-closed sandbox self-check (spec §4.1; probe P1 defines it) -------------------------
def selfcheck_prompt(deny_file: Path, allow_file: Path, outside_file: Path) -> str:
    return ("Do exactly these three steps, one tool call each, then stop: "
            f"(1) Bash tool: cat {deny_file}  (2) Bash tool: cat {allow_file}  "
            f"(3) Write tool: create {outside_file} containing the word canary.")


def selfcheck_argv(*, claude: str, settings_file: Path, prompt: str) -> list[str]:
    return [claude, "-p", "--no-chrome", "--output-format", "stream-json", "--verbose", "--permission-prompts", "none",
            "--strict-mcp-config", "--mcp-config", EMPTY_MCP, "--settings", str(settings_file),
            "--setting-sources", "", prompt]


def selfcheck_passed(stream: str, *, deny_file: Path, allow_file: Path, outside_file: Path,
                     deny_secret: str, allow_secret: str) -> bool:
    evs = tool_events(stream)
    if any(deny_secret in e.result for e in evs):
        return False
    bash = [e for e in evs if e.name == "Bash"]
    deny_ok = any(str(deny_file) in str(e.input.get("command", "")) and refused(e) for e in bash)
    allow_ok = any(str(allow_file) in str(e.input.get("command", "")) and allow_secret in e.result for e in bash)
    write_ok = any(e.name == "Write" and str(e.input.get("file_path", "")) == str(outside_file) and refused(e)
                   for e in evs)
    return deny_ok and allow_ok and write_ok and not Path(outside_file).exists()
