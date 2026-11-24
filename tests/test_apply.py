import fcntl
import json
import os
import threading
import time

import pytest

from conftest import LIMITS, MEMORY, SOUL, USER, read_entries

from memory_tidy.applier import apply_plan
from memory_tidy.scanner import entry_id


def mem_raw(home):
    return (home / "memories" / "MEMORY.md").read_text(encoding="utf-8")


def backups(home):
    root = home / "memory-tidy" / "backups"
    return sorted(root.iterdir()) if root.exists() else []


def test_dry_run_is_default_and_writes_nothing(home):
    before = mem_raw(home)
    res = apply_plan([{"action": "remove", "target": "memory", "index": 0}], home, limits=LIMITS)
    assert res["status"] == "dry_run" and res["dry_run"] is True
    assert res["changes"][0]["op"] == "remove"
    assert res["files"]["memory"]["entries_after"] == len(MEMORY) - 1
    assert mem_raw(home) == before
    assert backups(home) == []


def test_remove_with_backup_and_atomic_write(home):
    path = home / "memories" / "MEMORY.md"
    os.chmod(path, 0o640)
    before = mem_raw(home)
    res = apply_plan([{"action": "remove", "target": "memory", "id": entry_id(MEMORY[0])}], home,
                     dry_run=False, limits=LIMITS)
    assert res["status"] == "applied", res
    assert read_entries(home) == MEMORY[1:]
    assert (path.stat().st_mode & 0o777) == 0o640  # mode kept across the atomic replace
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".tidy_")]  # no temp leftovers
    (bdir,) = backups(home)
    assert (bdir / "memories" / "MEMORY.md").read_text(encoding="utf-8") == before
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"] == ["memories/MEMORY.md"]
    assert res["backup"].startswith("memory-tidy/backups/")
    assert not (bdir / "memories" / "USER.md").exists()  # untouched files are not copied


def test_soul_never_modified(home):
    soul = home / "SOUL.md"
    for plan in (
        [{"action": "remove", "target": "soul", "index": 0}],
        [{"action": "append_skill", "path": "SOUL.md", "text": "x"}],
        [{"action": "append_skill", "path": "skills/../SOUL.md", "text": "x"}],
        [{"action": "append_skill", "path": str(soul), "text": "x"}],
    ):
        res = apply_plan(plan, home, dry_run=False, limits=LIMITS)
        assert res["status"] == "refused", plan
    assert soul.read_text(encoding="utf-8") == SOUL


@pytest.mark.parametrize("path", [
    "skills/../../outside.md",
    "../outside.md",
    "/tmp/outside.md",
    "skills/devops/demo-deploy/run.sh",
    "skills/no-such-skill/deep/SKILL.md",
    "memories/MEMORY.md",
])
def test_path_traversal_and_foreign_paths_refused(home, path):
    res = apply_plan([{"action": "append_skill", "path": path, "text": "x"}], home, dry_run=False, limits=LIMITS)
    assert res["status"] == "refused"
    assert not (home.parent / "outside.md").exists()


def test_symlink_escape_refused(home, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (home / "skills" / "linked").symlink_to(outside, target_is_directory=True)
    res = apply_plan([{"action": "append_skill", "path": "skills/linked/SKILL.md", "text": "x"}], home,
                     dry_run=False, limits=LIMITS)
    assert res["status"] == "refused"
    assert not (outside / "SKILL.md").exists()


def test_unique_fact_loss_refused_then_forced(home):
    plan = [{"action": "remove", "target": "memory", "index": 4}]  # unique entry
    res = apply_plan(plan, home, dry_run=False, limits=LIMITS)
    assert res["status"] == "refused"
    assert res["unique_fact_losses"][0]["index"] == 4
    assert read_entries(home) == MEMORY
    forced = apply_plan(plan, home, dry_run=False, force_unique_loss=True, limits=LIMITS)
    assert forced["status"] == "applied"
    assert MEMORY[4] not in read_entries(home)


def test_literal_loss_detected(home):
    # Entry 1 holds a URL and paths that exist nowhere else.
    res = apply_plan([{"action": "remove", "target": "memory", "index": 1}], home, limits=LIMITS)
    assert res["status"] == "refused"
    assert "https://demo-docs.example.org/health" in res["unique_fact_losses"][0]["missing_literals"]


def test_move_procedure_to_skill_in_one_plan(home):
    skill = home / "skills" / "devops" / "demo-deploy" / "SKILL.md"
    old_skill = skill.read_text(encoding="utf-8")
    plan = [
        {"action": "append_skill", "path": "skills/devops/demo-deploy/SKILL.md",
         "text": "## Steps\n\n" + MEMORY[1]},
        {"action": "remove", "target": "memory", "index": 1, "expect": "To deploy the docs site"},
    ]
    res = apply_plan(plan, home, dry_run=False, limits=LIMITS)
    assert res["status"] == "applied", res
    assert skill.read_text(encoding="utf-8").startswith(old_skill.rstrip("\n"))
    assert MEMORY[1] in skill.read_text(encoding="utf-8")
    assert MEMORY[1] not in read_entries(home)
    (bdir,) = backups(home)
    assert (bdir / "skills/devops/demo-deploy/SKILL.md").read_text(encoding="utf-8") == old_skill


def test_merge_overlapping_entries(home):
    merged = ("The team calendar sync runs every morning at 06:00, posts a digest to the planning channel "
              "and skips weekends.")
    plan = [{"action": "replace", "target": "memory", "index": 2, "text": merged},
            {"action": "remove", "target": "memory", "index": 3}]
    res = apply_plan(plan, home, dry_run=False, limits=LIMITS)
    assert res["status"] == "applied", res
    entries = read_entries(home)
    assert merged in entries and MEMORY[3] not in entries and len(entries) == len(MEMORY) - 1


def test_budget_enforced(home):
    big = "Fact " + "x" * 1400
    res = apply_plan([{"action": "add", "target": "memory", "text": big}], home, dry_run=False, limits=LIMITS)
    assert res["status"] == "refused"
    assert any("budget" in e for e in res["errors"])
    assert read_entries(home) == MEMORY


def test_over_budget_file_may_still_shrink(home):
    res = apply_plan([{"action": "remove", "target": "memory", "index": 0}], home, dry_run=False,
                     limits={"memory": 300, "user": 1500})
    assert res["status"] == "applied"
    assert res["warnings"]


def test_entry_larger_than_budget_refused(home):
    res = apply_plan([{"action": "add", "target": "user", "text": "y" * 1600}], home, limits=LIMITS)
    assert res["status"] == "refused"


def test_guards_against_stale_plans(home):
    assert apply_plan([{"action": "remove", "target": "memory", "index": 1, "id": entry_id(MEMORY[0])}],
                      home, limits=LIMITS)["status"] == "refused"
    assert apply_plan([{"action": "remove", "target": "memory", "index": 99}], home,
                      limits=LIMITS)["status"] == "refused"
    assert apply_plan([{"action": "remove", "target": "memory", "index": 0, "expect": "Something else"}],
                      home, limits=LIMITS)["status"] == "refused"
    assert apply_plan([{"action": "remove", "target": "memory", "index": 0},
                       {"action": "remove", "target": "memory", "index": 0}], home,
                      limits=LIMITS)["status"] == "refused"


def test_delimiter_in_text_refused(home):
    res = apply_plan([{"action": "add", "target": "user", "text": "one\n§\ntwo"}], home, limits=LIMITS)
    assert res["status"] == "refused"


def test_invalid_plans_refused(home):
    for plan in (None, [], "not json", [{"action": "explode"}], [{"action": "add", "target": "user"}]):
        assert apply_plan(plan, home, limits=LIMITS)["status"] == "refused"


def test_add_entry_and_user_target(home):
    res = apply_plan([{"action": "add", "target": "user", "text": "Enjoys hiking on weekends."}], home,
                     dry_run=False, limits=LIMITS)
    assert res["status"] == "applied"
    assert read_entries(home, "USER.md") == USER + ["Enjoys hiking on weekends."]


def test_respects_memory_file_lock(home):
    lock_path = home / "memories" / "MEMORY.md.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    done = threading.Event()
    result = {}

    def worker():
        result["res"] = apply_plan([{"action": "remove", "target": "memory", "index": 0}], home,
                                   dry_run=False, limits=LIMITS)
        done.set()

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.4)
    assert not done.is_set(), "apply must wait for the memory tool's lock"
    assert read_entries(home) == MEMORY
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    t.join(5)
    assert result["res"]["status"] == "applied"
