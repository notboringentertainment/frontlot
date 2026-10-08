import json
from pathlib import Path

from backlot import claude_settings as cs


def world(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    film = tmp_path / "elsewhere" / "projects" / "film"; film.mkdir(parents=True)  # films may live outside the repo
    return repo, film, tmp_path / "meta"


def test_native_tools_are_allow_listed_and_never_prompt(tmp_path):
    repo, film, meta = world(tmp_path)
    s = cs.build_settings(repo_root=repo, film_root=film, meta_root=meta, environ={})
    work, f, r = film.resolve() / "frontlot-work", film.resolve(), repo.resolve()
    p = s["permissions"]
    assert p["defaultMode"] == "dontAsk"
    assert p["allow"] == [cs.TOOL_NAME, f"Read(/{f}/**)", f"Read(/{r}/**)", f"Edit(/{work}/**)"]
    for name in (".claude/**", ".mcp.json", "CLAUDE.md", "CLAUDE.local.md", ".git/**"):
        assert f"Edit(/{work}/{name})" in p["deny"]
    assert "Read(~/.openmontage/**)" in p["deny"] and f"Read(/{meta.resolve()}/**)" in p["deny"]
    assert f"Read(/{r}/.env*)" in p["deny"]


def test_sandbox_is_enforced_and_fail_closed(tmp_path):
    repo, film, meta = world(tmp_path)
    sb = cs.build_settings(repo_root=repo, film_root=film, meta_root=meta, environ={})["sandbox"]
    assert sb["enabled"] is True and sb["failIfUnavailable"] is True
    assert sb["allowUnsandboxedCommands"] is False and sb["excludedCommands"] == []
    assert {"~/.openmontage", "~/.ssh", str(meta.resolve())} <= set(sb["filesystem"]["denyRead"])
    assert sb["network"] == {"allowedDomains": ["127.0.0.1:5177"], "strictAllowlist": True,
                             "allowUnixSockets": [], "allowLocalBinding": False}


def test_secrets_cover_every_env_file_and_named_credential_files(tmp_path):
    repo, film, meta = world(tmp_path)
    keys = tmp_path / "keys"; keys.mkdir()
    sa = keys / "service-account.json"; sa.write_text("{}")
    other = keys / "other.pem"; other.write_text("x")
    (repo / ".env").write_text("FAL_KEY=abc\n")
    (repo / ".env.production").write_text(f'export GOOGLE_APPLICATION_CREDENTIALS="{sa}"\n')
    s = cs.build_settings(repo_root=repo, film_root=film, meta_root=meta, environ={"SOME_KEY_FILE": str(other)})
    deny_read, deny = s["sandbox"]["filesystem"]["denyRead"], s["permissions"]["deny"]
    for path in (repo.resolve() / ".env", repo.resolve() / ".env.production", sa.resolve(), other.resolve()):
        assert str(path) in deny_read and f"Read(/{path})" in deny, path


def test_env_is_allowlisted():
    base = {"HOME": "/h", "USER": "u", "LANG": "en_US.UTF-8", "CLAUDECODE": "1",
            "CLAUDE_CODE_SESSION_ID": "x", "OPENAI_API_KEY": "k", "FAL_KEY": "k"}
    env = cs.allowed_env(base, login_path="/usr/bin", live_socket="/s", live_token="t")
    assert env == {"HOME": "/h", "USER": "u", "LANG": "en_US.UTF-8", "PATH": "/usr/bin",
                   "TERM": "xterm-256color", "FRONTLOT_LIVE_SOCKET": "/s", "FRONTLOT_LIVE_TOKEN": "t"}


def test_launch_argv_loads_no_setting_source_and_keeps_the_prompt(tmp_path):
    common = dict(claude="/c", mod_dir=Path("/m"), settings_file=Path("/s.json"),
                  brief_file=Path("/b.md"), session_id="u-1", prompt="hi")
    new = cs.launch_argv(resume=False, **common)
    assert new[:1] == ["/c"] and new[new.index("--session-id") + 1] == "u-1" and new[-1] == "hi"
    assert new[new.index("--setting-sources") + 1] == ""
    assert new[new.index("--mcp-config") + 2].startswith("--")  # the variadic value is closed by an option
    res = cs.launch_argv(resume=True, **common)
    assert "--resume" in res and "--session-id" not in res and res[-1] == "hi"


def test_brief_has_no_story_text_and_names_rules():
    b = cs.build_brief(film_title="Film", film_slug="film")
    assert "frontlot_run" in b and "never" in b.lower() and "sign" in b.lower()


def stream(*events):
    lines = []
    for i, (name, inp, result, err) in enumerate(events):
        lines.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": err, "content": result}]}}))
    return "\n".join(lines)


def test_tool_events_pair_uses_with_results():
    evs = cs.tool_events("not json\n" + stream(("Bash", {"command": "cat /k"}, "cat: /k: Operation not permitted", True)))
    assert evs == [cs.ToolEvent("Bash", {"command": "cat /k"}, "cat: /k: Operation not permitted", True)]
    assert cs.refused(evs[0])


def test_selfcheck_needs_tool_level_evidence(tmp_path):
    d, a, o = tmp_path / "deny", tmp_path / "allow", tmp_path / "outside.txt"
    args = dict(deny_file=d, allow_file=a, outside_file=o, deny_secret="DENY-1", allow_secret="ALLOW-1")
    good = [("Bash", {"command": f"cat {d}"}, f"cat: {d}: Operation not permitted", True),
            ("Bash", {"command": f"cat {a}"}, "ALLOW-1", False),
            ("Write", {"file_path": str(o), "content": "canary"}, "Permission to use Write has been denied", True)]
    assert cs.selfcheck_passed(stream(*good), **args)
    assert not cs.selfcheck_passed(stream(*good[1:]), **args)                      # deny step skipped: no evidence
    assert not cs.selfcheck_passed(stream(("Bash", {"command": f"cat {d}"}, "DENY-1", False), *good[1:]), **args)
    o.write_text("canary")
    assert not cs.selfcheck_passed(stream(*good), **args)                          # the write landed
    argv = cs.selfcheck_argv(claude="/c", settings_file=Path("/s.json"), prompt="p")
    assert argv[-1] == "p" and argv[argv.index("--mcp-config") + 2].startswith("--")
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
