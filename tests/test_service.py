from conftest import MEMORY, SOUL, read_entries

from memory_tidy.service import Settings, handle_slash, periodic_check


def settings(**over):
    return Settings(overrides=over)


def test_slash_summary(home):
    out = handle_slash("", settings(), home)
    assert "duplicate_of_soul=1" in out and "procedure_like=1" in out and "overlaps_entry=2" in out
    assert "memory#0" in out and "apply-exact" in out
    assert len(out) <= 3000
    assert read_entries(home) == MEMORY  # read-only


def test_slash_help_and_unknown(home):
    assert "apply-exact" in handle_slash("help", settings(), home)
    assert "Unknown subcommand" in handle_slash("frobnicate", settings(), home)


def test_preview_exact_writes_nothing(home):
    out = handle_slash("preview-exact", settings(), home)
    assert "Would remove 1" in out
    assert read_entries(home) == MEMORY


def test_apply_exact_removes_only_exact_soul_duplicates(home):
    out = handle_slash("apply-exact", settings(), home)
    assert "Removed 1" in out and "Backup" in out
    assert read_entries(home) == MEMORY[1:]  # skill duplicate, overlaps, procedure all kept
    assert (home / "SOUL.md").read_text(encoding="utf-8") == SOUL
    assert "No entry" in handle_slash("apply-exact", settings(), home)


def test_exact_threshold_floor(home):
    assert settings(exact_threshold=0.1).exact_threshold == 0.8


def test_periodic_check_notes_and_rate_limits(home):
    s = settings(check_interval_hours=6)
    note = periodic_check(s, home, now=1_000_000)
    assert note and "/memory-tidy" in note and "SOUL.md" in note
    assert periodic_check(s, home, now=1_000_000 + 3600) is None  # within interval
    assert periodic_check(s, home, now=1_000_000 + 7 * 3600) is not None
    assert read_entries(home) == MEMORY  # never edits by default


def test_periodic_check_usage_threshold(home):
    (home / "memories" / "MEMORY.md").write_text("Prefers metric units.", encoding="utf-8")
    assert periodic_check(settings(), home, now=10) is None
    assert (home / "memory-tidy" / "state.json").exists()  # the check did run
    (home / "memories" / "MEMORY.md").write_text("Prefers metric units. " * 80, encoding="utf-8")
    assert periodic_check(settings(), home, now=10 + 3600) is None  # rate limited
    note = periodic_check(settings(), home, now=10 + 2 * 86400)
    assert note and "nearly full" in note


def test_periodic_check_auto_apply_exact(home):
    note = periodic_check(settings(auto_apply_exact=True), home, now=5)
    assert "removed 1" in note
    assert read_entries(home) == MEMORY[1:]


def test_periodic_check_disabled(home):
    assert periodic_check(settings(enabled=False), home, now=5) is None
    assert not (home / "memory-tidy").exists()


class FakeCtx:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.tools, self.hooks, self.commands, self.cli = {}, {}, {}, {}

    def get_config(self, key, default=None):
        return self.cfg.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema, handler)

    def register_hook(self, name, cb, **kw):
        self.hooks[name] = cb

    def register_command(self, name, handler, description="", args_hint="", **kw):
        self.commands[name] = handler

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli[name] = (setup_fn, handler_fn)


def test_register_wires_tools_hook_command(home, plugin_module):
    import json

    ctx = FakeCtx({"check_interval_hours": 0})
    plugin_module.register(ctx)
    assert set(ctx.tools) == {"memory_tidy_scan", "memory_tidy_apply"}
    assert set(ctx.hooks) == {"pre_llm_call"} and set(ctx.commands) == {"memory-tidy"}
    assert "memory-tidy" in ctx.cli

    report = json.loads(ctx.tools["memory_tidy_scan"][2]({"verdicts": ["duplicate_of_soul"]}))
    assert [e["index"] for e in report["entries"]] == [0]
    dry = json.loads(ctx.tools["memory_tidy_apply"][2]({"actions": [{"action": "remove", "target": "memory",
                                                                       "index": 0}]}))
    assert dry["status"] == "dry_run" and read_entries(home) == MEMORY
    bad = json.loads(ctx.tools["memory_tidy_apply"][2]({"actions": "{"}))
    assert bad["status"] == "refused"

    hook = ctx.hooks["pre_llm_call"]
    assert hook(session_id="s", is_first_turn=False) is None
    assert hook(session_id="s", is_first_turn=True, parent_session_id="parent") is None
    assert "/memory-tidy" in hook(session_id="s", is_first_turn=True)["context"]
    assert "Verdicts" in ctx.commands["memory-tidy"]("")
