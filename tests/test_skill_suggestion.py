"""move_to_skill target selection on a large skills tree with decoys (issue #1). Synthetic data only."""

from pathlib import Path

from memory_tidy import scanner, store
from memory_tidy.scanner import context_words, rank_skills, scan, skill_head, suggest_skill

GPU_NOTE = (
    "To run GPU jobs on Modal: 1) export MODAL_PROFILE=team 2) run `python3 -m modal run job.py --gpu L4` from "
    "~/projects/gpu (T4 is fine too) and set scaledown 60 3) cap containers at 2 4) check the Modal container list "
    "is empty after every run."
)
MAIL_NOTE = (
    "To set the mail app password: 1) create it on the account security page 2) save it to ~/secrets/mail.token "
    "and run `chmod 600 ~/secrets/mail.token` 3) use it for SMTP login, never the real password."
)

GPU_SKILL = """---
name: gpu-cloud-jobs
description: Use when running GPU jobs on the Modal cloud (profiles, GPU choice, scaledown, containers).
---

# GPU cloud jobs

Pick the GPU per job, keep scaledown short, cap containers, and list containers after a run.
"""


def _decoy(i: int) -> str:
    # Unrelated topics whose BODIES happen to contain the note's rare words (numbers, tool names, verbs).
    topics = ["ecg-simulator", "kitab-rag-pipeline", "invoice-printing", "quiz-bank", "map-video"]
    name = f"{topics[i % len(topics)]}-{i}"
    return f"""---
name: {name}
description: Use when working on the {topics[i % len(topics)].replace('-', ' ')} project number {i}.
---

# {name}

Simulation step 60 renders on an L4 or T4 box; run job.py, check the list, then scaledown the containers.
SMTP login for alerts uses an app password stored in a token file (chmod 600); never print the password.
Filler sentence {i} about patients, rhythms, chapters, receipts and frames.
"""


def _big_home(tmp_path: Path, monkeypatch, with_gpu_skill: bool = True, entries=(GPU_NOTE, MAIL_NOTE)) -> Path:
    h = tmp_path / "big-home"
    (h / "memories").mkdir(parents=True)
    (h / "SOUL.md").write_text("You are a careful assistant.\n", encoding="utf-8")
    (h / "memories" / "MEMORY.md").write_text("\n§\n".join(entries), encoding="utf-8")
    (h / "memories" / "USER.md").write_text("Likes short answers.", encoding="utf-8")
    skills = {f"projects/decoy-{i}/SKILL.md": _decoy(i) for i in range(60)}
    if with_gpu_skill:
        skills["devops/gpu-cloud-jobs/SKILL.md"] = GPU_SKILL
    for rel, text in skills.items():
        p = h / "skills" / rel
        p.parent.mkdir(parents=True)
        p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(h))
    scanner._SOURCES_CACHE.clear()
    return h


def _entry(report, idx):
    return next(e for e in report["entries"] if e["target"] == "memory" and e["index"] == idx)


def test_topic_skill_wins_over_decoys(tmp_path, monkeypatch):
    e = _entry(scan(_big_home(tmp_path, monkeypatch)), 0)
    assert e["verdict"] == "procedure_like"
    assert e["suggested_action"] == "move_to_skill:skills/devops/gpu-cloud-jobs/SKILL.md"
    cands = e["evidence"][0]["skill_candidates"]
    assert cands[0]["skill"] == "skills/devops/gpu-cloud-jobs/SKILL.md"
    assert {"gpu", "modal"} <= set(cands[0]["anchors"])
    assert len(cands) <= 3


def test_no_fitting_skill_means_new_skill_not_a_decoy(tmp_path, monkeypatch):
    r = scan(_big_home(tmp_path, monkeypatch, with_gpu_skill=False))
    for idx in (0, 1):
        e = _entry(r, idx)
        assert e["verdict"] == "procedure_like", e["scores"]
        assert e["suggested_action"] == "move_to_skill:<new skill>", e["evidence"][0]["skill_candidates"]


def test_close_runner_up_is_ambiguous(tmp_path, monkeypatch):
    h = _big_home(tmp_path, monkeypatch)
    twin = h / "skills" / "devops" / "gpu-cloud-jobs-copy" / "SKILL.md"
    twin.parent.mkdir(parents=True)
    twin.write_text(GPU_SKILL.replace("gpu-cloud-jobs", "gpu-cloud-jobs-copy"), encoding="utf-8")
    scanner._SOURCES_CACHE.clear()
    e = _entry(scan(h), 0)
    assert e["suggested_action"] == "move_to_skill:<new skill>"
    top = [c["skill"] for c in e["evidence"][0]["skill_candidates"][:2]]
    assert sorted(top) == ["skills/devops/gpu-cloud-jobs-copy/SKILL.md", "skills/devops/gpu-cloud-jobs/SKILL.md"]


def test_one_shared_head_word_is_not_enough(tmp_path, monkeypatch):
    h = _big_home(tmp_path, monkeypatch, with_gpu_skill=False)
    p = h / "skills" / "comms" / "telegram-channels" / "SKILL.md"
    p.parent.mkdir(parents=True)
    # Head shares only "telegram" with the note; the body shares a lot (long skills mention everything).
    p.write_text("---\nname: telegram-channels\ndescription: Use when diagnosing Telegram channels.\n---\n"
                 "Run notify.sh after a deploy, check the bot replied within 60 seconds, alerts go to the group.\n",
                 encoding="utf-8")
    scanner._SOURCES_CACHE.clear()
    src = scanner.build_sources(store.Layout(h))
    note = "Telegram alerts: run `notify.sh` after every deploy and check the bot replied within 60 seconds."
    cand = next(c for c in rank_skills(note, src) if c["skill"].endswith("telegram-channels/SKILL.md"))
    assert cand["anchors"] == ["telegram"] and not cand["eligible"]
    assert suggest_skill(note, src) == (None, 0.0)


def test_skill_head_reads_folder_name_description_and_plurals():
    head = skill_head("skills/devops/key-vault/SKILL.md",
                      "---\nname: key-vault\ndescription: >\n  Use when receiving API keys\n  or rotating tokens.\n---\nBody\n")
    assert {"key", "vault", "receiving", "api", "keys", "rotating", "token"} <= head
    assert "use" not in head


def test_context_words_are_ignored_as_topic():
    entries = [f"Owner Rani prefers note {i} about topic{i} on day {i}." for i in range(10)]
    ctx = context_words(entries)
    assert {"owner", "rani", "prefer"} <= ctx
    assert context_words(entries[:3]) == frozenset()  # too few entries to judge
