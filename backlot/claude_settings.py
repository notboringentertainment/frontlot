"""Settings, brief, environment, and argv for Front Lot's embedded Claude.

Everything here is pure (apart from reading .env* files to find credential
paths). The sandbox and permission rules are the session's enforced boundary
(spec §4.1). Claude gets the reach Ben's own Claude has: the open internet,
Ben's claude.ai connectors and Chrome, and Blender (which needs the GPU, so it
alone may run outside the sandbox). What stays closed, and why:
- signing keys, provider keys and saved passwords are unreadable;
- Claude writes only its work area, because Front Lot's checks on approved film
  files rely on Claude never writing the film folder;
- Higgsfield calls that spend credits ask Ben first.
Anything not allowed below asks Ben ("default" mode); what is denied is refused.
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
# Ben's claude.ai connectors, allowed whole. A connector missing here still works: Claude Code asks Ben.
CONNECTORS = ("Higgsfield", "Google_Drive", "Google_Calendar", "Gmail", "Notion", "Claude_Docs", "Airtable",
              "Granola", "Hugging_Face", "Supabase", "Docusign")
# protects: Ben's credits. Every Higgsfield call that makes or changes media asks him first.
HIGGSFIELD_SPEND = (
    "generate_image", "generate_video", "generate_audio", "generate_3d", "generate_image_batch",
    "generate_video_batch", "generate_audio_batch", "upscale_image", "upscale_video", "outpaint_image", "reframe",
    "remove_background", "motion_control", "execute_preset", "dubbing", "voice_change", "create_voice",
    "create_voice_from_confirmed_audio", "ai_influencer_generate", "build_ai_influencer", "ads_studio_generate",
    "shorts_studio_create", "scene_builder_3d_generate_angles", "scene_builder_3d_generate_video",
    "video_analysis_create", "virality_predictor", "apps_invoke",
)
# protects: nothing goes out or is deleted under Ben's name without his yes. Connector tools that send, share,
# sign, delete, or change outside systems ask him; reading stays free. (Web pages Claude reads can carry
# instructions, so the card is enforced, not left to the brief.)
CONNECTOR_SIDE_EFFECTS = (
    "Gmail__send_message", "Gmail__reply", "Gmail__forward", "Gmail__trash_message", "Gmail__trash_thread",
    "Gmail__delete_label",
    "Google_Calendar__create_event", "Google_Calendar__update_event", "Google_Calendar__delete_event",
    "Google_Calendar__respond_to_event",
    "Google_Drive__share_file", "Google_Drive__trash_file", "Google_Drive__update_file",
    "Docusign__createEnvelope", "Docusign__createEnvelopeWithDocuments", "Docusign__updateEnvelope",
    "Docusign__updateEnvelopeRecipients", "Docusign__updateEnvelopeTabs", "Docusign__sendReminder",
    "Docusign__triggerWorkflow", "Docusign__startClmWorkflow", "Docusign__stopClmWorkflow",
    "Docusign__cancelWorkflowInstance", "Docusign__pauseNewWorkflowInstances", "Docusign__resumeWorkflow",
    "Notion__notion-delete-pages", "Notion__notion-delete-databases", "Notion__notion-delete-comment",
    "Airtable__delete_records_for_table", "Airtable__delete_table", "Airtable__delete_automation",
    "Airtable__delete_interface", "Airtable__delete_page",
    "Supabase__execute_sql", "Supabase__apply_migration", "Supabase__deploy_edge_function",
    "Supabase__create_project", "Supabase__pause_project", "Supabase__delete_branch", "Supabase__merge_branch",
    "Supabase__reset_branch", "Supabase__create_edge_function_secret",
    "Claude_Docs__delete",
    "Higgsfield__publish_website", "Higgsfield__deploy_website", "Higgsfield__tiktok_prepare_publish",
    "Higgsfield__participate_in_contest", "Higgsfield__cancel_trial_auto_renewal",
)
# Blender needs the GPU, which the sandbox blocks, so it is the one command allowed to run outside it.
UNSANDBOXED_COMMANDS = ("blender",)
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


def story_drive_folder(film_root: Path) -> Path | None:
    """The film's Story-drive folder (project.yaml: wayfinder_root), checked the way the look step checks
    it; None when the film has none. Claude reads today's canon there; it cannot write there."""
    from lib.look_ingest import LookIngestError, wayfinder_root_for   # deferred: pulls in yaml

    try:
        return wayfinder_root_for(film_root)
    except LookIngestError:
        return None


def _film_write_denials(film: Path, work: Path) -> list[str]:
    """protects: approved film files. Front Lot's checks rely on Claude never writing the film folder outside
    its work area, so those writes are refused outright rather than put to Ben as a question."""
    # Never a bare "/film/*": it matches the work area itself and refuses everything inside it. "*.*" refuses any
    # top-level file with an extension, existing or new ("frontlot-work" has no dot); every existing file and
    # folder other than the work area is also refused by name.
    out = [f"Edit(/{film}/*.*)"]
    try:
        children = sorted(film.iterdir())
    except OSError:
        children = []
    for c in children:
        if c.resolve() == work.resolve():
            continue
        out.append(f"Edit(/{c}/**)" if c.is_dir() else f"Edit(/{c})")
    return out


def build_settings(*, repo_root: Path, film_root: Path, meta_root: Path, environ: Mapping[str, str]) -> dict:
    repo, film, meta = repo_root.resolve(), film_root.resolve(), meta_root.resolve()
    work = film / "frontlot-work"
    secrets = secret_paths(repo, environ)
    package = writeros_package(film)
    story = story_drive_folder(film)
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,           # protects: never run unsandboxed
            "autoAllowBashIfSandboxed": True,
            # Only Blender, and only when Claude asks for it (Bash's dangerouslyDisableSandbox): the allow rule
            # below covers blender alone, so any other unsandboxed command asks Ben.
            "allowUnsandboxedCommands": True,
            "excludedCommands": [],
            "filesystem": {
                "denyRead": [*HOME_SECRET_DIRS, *HOME_SECRET_FILES, str(meta), *map(str, secrets)],
                # protects: Bash writing files that later launches or resumes would trust
                "denyWrite": [str(work / n.removesuffix("/**")) for n in WORK_PROTECTED],
            },
            "network": {
                "allowedDomains": ["*", WRITEROS_HOST],
                "allowUnixSockets": [],             # protects: signer and broker sockets
                "allowLocalBinding": False,
            },
        },
        "permissions": {
            "defaultMode": "default",             # anything not allowed or denied below asks Ben
            "allow": [TOOL_NAME, CHROME_TOOLS, f"Read(/{film}/**)", f"Read(/{repo}/**)", f"Edit(/{work}/**)"]
                     + [f"Bash({c}:*)" for c in UNSANDBOXED_COMMANDS]
                     + [f"mcp__claude_ai_{c}" for c in CONNECTORS]
                     + ([f"Read(/{package}/**)"] if package else [])
                     + ([f"Read(/{story}/**)"] if story else []),
            "ask": [f"mcp__claude_ai_Higgsfield__{t}" for t in HIGGSFIELD_SPEND]
                   + [f"mcp__claude_ai_{t}" for t in CONNECTOR_SIDE_EFFECTS],
            "deny": [f"Read({d}/**)" for d in HOME_SECRET_DIRS] + [f"Read({f})" for f in HOME_SECRET_FILES]
                    + [f"Read(/{meta}/**)", f"Read(/{repo}/**/.env*)"] + [f"Read(/{p})" for p in secrets]
                    + [f"Edit(/{work}/{name})" for name in WORK_PROTECTED]
                    + _film_write_denials(film, work),
        },
        "enableAllProjectMcpServers": False,
    }


def _operations_text() -> str:
    from backlot.claude_ops import OPERATIONS   # deferred: claude_ops pulls in the pipeline scripts

    return "\n".join(f"- {name} ({'paid' if spec.paid else 'free'}): {', '.join(sorted(spec.allowed))}"
                     for name, spec in OPERATIONS.items())


def _writeros_text(package: Path | None) -> str:
    if package is None:
        return ("- Front Lot found no WriterOS project for this film, so you cannot see looks Ben promoted there. Front Lot "
                "finds it on its own once the WriterOS project is linked to this film's Story-drive folder in WriterOS. "
                "Say so plainly when it matters; linking it is Ben's step in WriterOS.")
    return (f"- Looks Ben promoted in WriterOS are in this film's WriterOS package, {package}: the newest "
            "memory/exports/look-locks-<revision>.json. Read them with the Read tool. WriterOS's web pages need Ben's "
            "login, so do not try to reach WriterOS over the network.")


def _canon_text(story_drive: Path | None) -> str:
    if story_drive is None:
        return ("- This film has no Story-drive folder (no wayfinder_root in its project.yaml), so there is no live canon "
                "to read. Say so plainly when it matters.")
    return (f"- Today's canon lives in this film's Story-drive folder, {story_drive}. The approved canon snapshot "
            "(checkpoint_canon_ingest.json) is only a receipt of what canon a paid asset came from; it is not current "
            "canon. Before planning or starting any job, call frontlot_run {\"op\": \"canon_check\", \"params\": {}} and read "
            "the Canon Note plus every decision it lists, with the Read tool. When today's canon and approved work "
            "disagree (for example the approved cast lists a place Story-drive scoped out), tell Ben plainly and give him the "
            "Next: line canon_check prints, in its own words. Never invent a step for him: if the Next: line says Front Lot "
            "can't do something yet, say exactly that and never tell him to do it himself in some other tool. Never fix it "
            "yourself and never edit canon. When canon_check lists a look as ready to lock, tell Ben it's ready and offer to "
            "bring it in (a look dry run first). For canon files it lists as edited, Front Lot keeps no old copy: read each "
            "one and tell Ben in a sentence what it now says that matters to the job, or that nothing in it does. Never "
            "read him the list of file names. If a paid step prints a \"Heads up\" line about something Story-drive "
            "scoped out, relay it to Ben.")


def build_brief(*, film_title: str, film_slug: str, writeros_package: Path | None = None,
                story_drive: Path | None = None) -> str:
    return f"""You are working inside Front Lot, Ben's app for making a film's visuals.
Film: "{film_title}" (project id: {film_slug}). Your working folder is this film's Front Lot work area.

How you work here:
- You can read the film (../ is the film folder) and the OpenMontage repo, and think, plan, and talk with Ben. You write files only in your work area (writes anywhere else in the film are refused: Front Lot's checks on approved files rely on that). You have the open internet, Ben's claude.ai connectors (Higgsfield, Google Drive, Gmail, Notion and the rest) and Chrome. A tool that isn't pre-approved asks Ben with a Yes/No prompt; that is fine to use when the job needs it.
- Gray-box previz (Ben's way to steer camera and blocking on a complex shot): write a Blender Python script in your work area that builds the shot from plain blocks (room, figures, camera move, timing) and renders it with the Workbench engine to PNG frames, then join them with ffmpeg. Run Blender as `blender -b -P <script> -- <args>` with the Bash tool's dangerouslyDisableSandbox set to true: Blender needs the GPU, which the sandbox blocks, and it is the only command allowed out. Render a version with the figures and one without (environment only), pull a few stills, and show Ben before anything is spent.
- Higgsfield: Ben has credits there and it accepts reference images with faces, which fal.ai refuses. Seedance 2.5 (model seedance_2_5, mode omni_reference) follows a gray-box clip given as a video_references entry, with reference images as image_references; a 10-second 480p draft (draft: true) costs about 30 credits, and a draft can be finalized at 1080p within seven days. Upload local files with media_upload (PUT the bytes with curl, sending the If-None-Match: * header the signed URL requires), then media_confirm. Every Higgsfield call that makes media asks Ben Yes/No first; tell him the cost before you call (generate_video with get_cost: true gives it, and also asks him). Decline preset suggestions unless Ben wants one. An "ip_detected" result means Higgsfield refused the job and charged nothing. Download a finished take into your work area, then file it on its shot with shot_attach (shot_id, take: its path in your work area, note: what Ben asked for plus the credits it cost, job_id: Higgsfield's job id) so Front Lot keeps the record. The shot must be prepared first (shot_prepare).
{_writeros_text(writeros_package)}
{_canon_text(story_drive)}
- You never run pipeline scripts yourself. Every pipeline step goes through the frontlot_run tool with an operation name and parameters. Front Lot runs it.
  Call it as {{"op": "<name>", "params": {{...}}}}. These are the only operations; each line gives its cost and the parameters it accepts:
{_operations_text()}
- Free steps run right away. Paid steps show Ben a spend card; wait for his answer. "Not now" is a decision: do not ask again unless he brings it up.
- If a paid step fails with a message saying WriterOS changed canon the job uses (it ends "Apply the change before generating? Nothing is spent until you answer." and names an appeal ticket file), nothing was spent. Quote it to Ben in plain words, then ask exactly "Apply the change before generating?" and never ask him which version to use: WriterOS already decided. On yes, call frontlot_run {{"op": "appeal_apply", "params": {{"ticket": "<the ticket file name from the message>"}}}}, relay to Ben in plain words the step line that appeal_apply prints, and do not retry the paid step: wait for Ben. Bringing Front Lot's references up to date (for example ratifying the updated look) is Ben's own signed step; never run it yourself. On not yet, spend nothing and leave the ticket open.
- Before you tell Ben what is waiting under Needs you, what he has signed, which looks are locked or finished, or who and what is in the trailer cast, call frontlot_run {{"op": "status", "params": {{}}}} in that same turn and answer from what it prints. Never answer those from earlier in the conversation, from what you filed before, or from your own reads of checkpoint or proposal files: they go stale and a proposal lists people it leaves out. If status says a look is signed but not finished, finish it with the look op right away (it is free and only records what Ben signed), then tell him it is done.
- Talk only about the approved trailer cast and what today's canon holds. The approved canon snapshot is a fixed record and still lists people and places Ben has since cut from the story; never mention anyone or anything that isn't in the trailer cast or today's canon unless Ben asks about them by name.
- Only when Ben asks to change the approved trailer cast (for example to take out a place Story-drive scoped out, or put a newly locked one in), call frontlot_run {{"op": "cast", "params": {{"remove": ["<id>"], "add": ["<id>"]}}}} with the ids he means; either list may be left out. It only files the request; nothing changes until Ben signs it. Then tell him in a sentence what you asked for and that he can sign it under Needs you. Never file one on your own initiative, not even to clear a canon mismatch.
- Location pictures: once a place's look is locked, it gets reference pictures from Ben's own images, never ones you choose or make. When Ben says which pictures he means, copy each file into your work area (cp in Bash; you can read his Story-drive folder) and call location_add_image with the copy's path in your work area, the place's id, origin_tool (the tool that made it, for example midjourney; ask Ben if you don't know) and, for any picture after the first, a short lowercase label such as aftermath. The first picture is the establishing picture; a place holds at most three more. Adding asks Ben for nothing. As soon as you have added the pictures he named, call location_seal in the same turn (unless he said he has more): Ben then signs once, for the whole set, under Needs you. To change the set before he signs, use location_remove_image or add another picture. When Ben says he signed (or status says signed but not finished), call location_finish (free) and tell him it's done. If he declined, location_finish records his note and the pictures stay added, so he can change the set and seal again. Never crop, edit or retouch his images.
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
    argv = [claude, "--plugin-dir", str(mod_dir),
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
