"""The README example output must stay true: scan the synthetic demo home from examples/."""

from __future__ import annotations

import importlib.util

from conftest import REPO
from memory_tidy.scanner import scan


def _load_demo():
    spec = importlib.util.spec_from_file_location("make_demo_home", REPO / "examples" / "make_demo_home.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_home_matches_readme(tmp_path):
    home = tmp_path / "demo-home"
    _load_demo().main(str(home))
    report = scan(home)
    assert report["summary"] == {"duplicate_of_soul": 1, "duplicate_of_skill": 0, "overlaps_entry": 2,
                                 "procedure_like": 1, "ok": 2}
    actions = {e["index"]: e["suggested_action"] for e in report["entries"] if e["target"] == "memory"}
    assert actions[0] == "remove"
    assert actions[1] == "move_to_skill:skills/devops/docs-release/SKILL.md"
    assert actions[2] == "merge_with:3" and actions[3] == "merge_with:2"
    assert report["files"]["memory"]["chars"] == 473
