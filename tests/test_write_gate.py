"""The model tool commits only `remove`, only while write_approval is off; the user's CLI applies the rest."""

import json

from conftest import CONFIG, MEMORY, read_entries

from memory_tidy import store
from memory_tidy.cli import main as cli_main
from memory_tidy.service import Settings, periodic_check, tool_apply

REMOVE_0 = [{"action": "remove", "target": "memory", "index": 0}]
ADD = [{"action": "add", "target": "memory", "text": "Prefers metric units."}]


def settings(**over):
    return Settings(overrides=over)


def set_config(home, extra: str) -> None:
    (home / "config.yaml").write_text(CONFIG + extra, encoding="utf-8")


def backups(home):
    root = home / "memory-tidy" / "backups"
    return sorted(root.iterdir()) if root.exists() else []


def test_tool_remove_still_applies_when_gate_off(home):
    res = tool_apply({"actions": REMOVE_0, "dry_run": False}, settings(), home)
    assert res["status"] == "applied", res
    assert read_entries(home) == MEMORY[1:]


def test_tool_never_commits_model_text(home):
    skill = "skills/notes/backup-routine/SKILL.md"
    skill_before = (home / skill).read_text(encoding="utf-8")
    for plan in (ADD,
                 [{"action": "replace", "target": "memory", "index": 2, "text": MEMORY[2] + " Extra."}],
                 [{"action": "append_skill", "path": skill, "text": "## Notes\nInjected?"}],
                 REMOVE_0 + ADD):
        res = tool_apply({"actions": plan, "dry_run": False}, settings(), home)
        assert res["status"] == "needs_user", res
        assert res["written"] is False and "model-authored text" in res["reason"]
        assert res["changes"]  # the dry-run diff is still returned for the user to review
        assert "hermes memory-tidy apply" in res["user_command"] and "--write" in res["user_command"]
        saved = json.loads(open(res["plan_file"], encoding="utf-8").read())
        assert [a["action"] for a in saved] == [a["action"] for a in plan]
        assert all(not k.startswith("_") for a in saved for k in a)
    assert read_entries(home) == MEMORY
    assert (home / skill).read_text(encoding="utf-8") == skill_before
    assert backups(home) == []


def test_memory_write_approval_blocks_even_remove(home):
    set_config(home, "  write_approval: true\n")
    assert store.write_approval_enabled(home, "memory") is True
    res = tool_apply({"actions": REMOVE_0, "dry_run": False}, settings(), home)
    assert res["status"] == "needs_user" and "memory.write_approval is on" in res["reason"]
    assert read_entries(home) == MEMORY and backups(home) == []


def test_write_approval_values_read_like_hermes(home):
    for raw, expected in (("on", True), ("'yes'", True), ("approve", True), ("false", False), ("'off'", False)):
        set_config(home, f"  write_approval: {raw}\n")
        assert store.write_approval_enabled(home, "memory") is expected, raw
    (home / "config.yaml").write_text("skills:\n  write_approval: true\n", encoding="utf-8")
    assert store.write_approval_enabled(home, "skills") is True
    assert store.write_approval_enabled(home, "memory") is False


def test_unreadable_config_fails_closed(home):
    (home / "config.yaml").write_text("memory: [unclosed\n", encoding="utf-8")
    assert store.write_approval_enabled(home, "memory") is True
    res = tool_apply({"actions": REMOVE_0, "dry_run": False}, settings(), home)
    assert res["status"] == "needs_user"
    assert read_entries(home) == MEMORY


def test_dry_run_and_refusals_unchanged(home):
    set_config(home, "  write_approval: true\n")
    dry = tool_apply({"actions": ADD}, settings(), home)
    assert dry["status"] == "dry_run" and "plan_file" not in dry
    bad = tool_apply({"actions": [{"action": "remove", "target": "soul", "index": 0}], "dry_run": False},
                     settings(), home)
    assert bad["status"] == "refused" and not (home / "memory-tidy" / "plans").exists()


def test_user_cli_applies_the_saved_plan(home, capsys):
    res = tool_apply({"actions": ADD, "dry_run": False}, settings(), home)
    assert res["status"] == "needs_user"
    assert cli_main(["--home", str(home), "apply", res["plan_file"]]) == 0  # still a dry run
    assert read_entries(home) == MEMORY
    assert cli_main(["--home", str(home), "apply", res["plan_file"], "--write"]) == 0
    assert read_entries(home) == MEMORY + ["Prefers metric units."]
    assert '"status": "applied"' in capsys.readouterr().out
    assert len(backups(home)) == 1


def test_auto_apply_exact_respects_write_approval(home):
    set_config(home, "  write_approval: true\n")
    note = periodic_check(settings(auto_apply_exact=True), home, now=5)
    assert note and "skipped because memory.write_approval is on" in note
    assert read_entries(home) == MEMORY

def test_unclear_dry_run_value_stays_dry(home):
    for value in ("maybe", None, "", 1):
        res = tool_apply({"actions": REMOVE_0, "dry_run": value}, settings(), home)
        assert res["status"] == "dry_run", value
    assert read_entries(home) == MEMORY
