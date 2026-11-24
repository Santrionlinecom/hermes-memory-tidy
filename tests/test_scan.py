import hashlib

from conftest import LIMITS, MEMORY

from memory_tidy.scanner import procedure_score, scan, stale_hints
from memory_tidy.textsim import specials


def by_index(report, target, idx):
    return next(e for e in report["entries"] if e["target"] == target and e["index"] == idx)


def test_exact_duplicate_of_soul(home):
    r = scan(home, limits=LIMITS)
    e = by_index(r, "memory", 0)
    assert e["verdict"] == "duplicate_of_soul"
    assert e["suggested_action"] == "remove"
    assert e["scores"]["soul_exact"] >= 0.92
    assert any(ev["where"].startswith("SOUL.md:") for ev in e["evidence"])
    assert {"target": "memory", "index": 0, "id": e["id"]} in r["exact_soul_duplicates"]


def test_procedure_detected_and_skill_suggested(home):
    e = by_index(scan(home, limits=LIMITS), "memory", 1)
    assert e["verdict"] == "procedure_like"
    assert e["suggested_action"] == "move_to_skill:skills/devops/demo-deploy/SKILL.md"
    feats = e["evidence"][0]["features"]
    assert any("command" in f for f in feats) and any("path" in f for f in feats)


def test_overlap_merge_suggestion(home):
    r = scan(home, limits=LIMITS)
    a, b = by_index(r, "memory", 2), by_index(r, "memory", 3)
    assert a["verdict"] == b["verdict"] == "overlaps_entry"
    assert a["suggested_action"] == "merge_with:3"
    assert b["suggested_action"] == "merge_with:2"


def test_duplicate_of_skill(home):
    e = by_index(scan(home, limits=LIMITS), "memory", 5)
    assert e["verdict"] == "duplicate_of_skill"
    assert e["evidence"][0]["where"].startswith("skills/notes/backup-routine/SKILL.md:")


def test_unique_entries_ok(home):
    r = scan(home, limits=LIMITS)
    assert by_index(r, "memory", 4)["verdict"] == "ok"
    assert by_index(r, "user", 0)["verdict"] == "ok"
    assert by_index(r, "user", 1)["verdict"] == "ok"
    assert by_index(r, "memory", 4)["suggested_action"] == "keep"


def test_usage_and_summary(home):
    r = scan(home, limits=LIMITS)
    chars = len("\n§\n".join(MEMORY))
    assert r["files"]["memory"]["chars"] == chars
    assert r["files"]["memory"]["usage_pct"] == round(100 * chars / 2000, 1)
    assert r["files"]["user"]["limit"] == 1500
    assert sum(r["summary"].values()) == len(r["entries"]) == 8
    assert r["skills_indexed"] == 2


def test_limits_read_from_config(home):
    r = scan(home)
    assert r["files"]["memory"]["limit"] == 2000


def _snapshot(root):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def test_scan_is_read_only(home):
    before = _snapshot(home)
    scan(home, limits=LIMITS)
    assert _snapshot(home) == before
    assert not (home / "memory-tidy").exists()


def test_paraphrase_is_not_exact(home):
    (home / "memories" / "USER.md").write_text(
        "Owner likes brief answers formatted as bullets, and wants approval before anything goes public.",
        encoding="utf-8")
    e = by_index(scan(home, limits=LIMITS), "user", 0)
    assert e["scores"]["soul_exact"] < 0.92
    assert e not in [d for d in scan(home, limits=LIMITS)["exact_soul_duplicates"]]


def test_missing_literal_blocks_duplicate(home):
    # Same sentence as SOUL.md but with a new number: not a duplicate.
    (home / "memories" / "USER.md").write_text(
        "Production deploys happen only on Fridays after a peer review, ticket 884213.", encoding="utf-8")
    e = by_index(scan(home, limits=LIMITS), "user", 0)
    assert e["verdict"] != "duplicate_of_soul"


def test_specials_and_procedure_heuristics():
    sp = specials("see https://example.org/a, ~/x/y.toml, v1.2.3, id 1234567 and `make all`")
    assert {"https://example.org/a", "~/x/y.toml", "v1.2.3", "1234567", "make all"} <= sp
    assert procedure_score("Likes green tea in the afternoon.")[0] == 0
    assert procedure_score("Run `git pull` then `pip install -e .`; edit ./conf/app.yaml")[0] >= 4


def test_stale_hint():
    import datetime as dt

    hints = stale_hints("Moved offices on 2020-01-05.", today=dt.date(2026, 1, 1))
    assert hints and "days old" in hints[0]
