"""Validated, backed-up, lock-respecting application of a tidy plan.

A plan is a list of actions (or ``{"actions": [...]}``)::

    {"action": "remove",       "target": "memory", "index": 3}            # or "id": "<entry id>"
    {"action": "replace",      "target": "user",   "id": "ab12cd34ef", "text": "merged text"}
    {"action": "add",          "target": "memory", "text": "new entry"}
    {"action": "append_skill", "path": "skills/devops/deploy/SKILL.md", "text": "## Notes\\n..."}

Indexes and ids refer to the files as they are when the plan is applied (the scan report shows
both). ``expect`` (optional) is a prefix the entry must start with, as an extra guard.

Safety rules, all checked before anything is written:

* only ``memories/MEMORY.md``, ``memories/USER.md`` and ``*.md`` files inside ``skills/`` are
  writable; SOUL.md and every path outside ``skills/`` are refused (symlinks included);
* removing or replacing an entry must not lose facts: every literal (path, URL, ID, version...)
  and every sentence of the old text must still be present in SOUL.md, skills or the remaining
  entries after the plan, unless ``force_unique_loss`` is true;
* each memory file must end within its character budget (a plan may not grow a file that is
  already over budget);
* ``dry_run`` (the default) reports the diff and writes nothing;
* a real run copies every touched file to ``$HERMES_HOME/memory-tidy/backups/<timestamp>/``
  first, holds the memory tool's ``<file>.lock`` while reading and writing, and replaces files
  atomically.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from . import store
from .scanner import build_sources, entry_id
from .textsim import coverage, snippet

ENTRY_ACTIONS = {"remove", "replace", "add"}
ALL_ACTIONS = ENTRY_ACTIONS | {"append_skill"}
MAX_SKILL_APPEND = 50_000


class PlanError(ValueError):
    pass


def _norm_plan(plan: Any) -> list[dict]:
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError as exc:
            raise PlanError(f"plan is not valid JSON: {exc}") from None
    if isinstance(plan, dict):
        plan = plan.get("actions")
    if not isinstance(plan, list) or not plan:
        raise PlanError("plan must be a non-empty list of actions (or {'actions': [...]})")
    out = []
    for i, a in enumerate(plan, 1):
        if not isinstance(a, dict):
            raise PlanError(f"action {i}: must be an object")
        kind = str(a.get("action", "")).strip().lower()
        if kind not in ALL_ACTIONS:
            raise PlanError(f"action {i}: unknown action {a.get('action')!r} (use {sorted(ALL_ACTIONS)})")
        out.append({**a, "action": kind, "_n": i})
    return out


def resolve_skill_path(layout: store.Layout, raw: Any) -> Path:
    """Map a plan path to a real file inside skills/; raise PlanError for anything else."""
    if not isinstance(raw, str) or not raw.strip():
        raise PlanError("append_skill needs a 'path'")
    text = raw.strip().replace("\\", "/")
    root = layout.skills.resolve()
    p = Path(text).expanduser()
    if not p.is_absolute():
        parts = Path(text).parts
        p = layout.home / text if parts and parts[0] == store.SKILLS_DIR else layout.skills / text
    if p.name == store.SOUL_NAME:
        raise PlanError("SOUL.md is owner-authored and is never modified")
    if p.suffix.lower() != ".md":
        raise PlanError(f"{raw}: only .md files under skills/ can be appended to")
    if os.path.islink(p):
        raise PlanError(f"{raw}: refusing to write through a symlink")
    resolved = p.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PlanError(f"{raw}: path is outside skills/ and is refused") from None
    if resolved == layout.soul.resolve():
        raise PlanError("SOUL.md is never modified")
    parent = resolved.parent
    if not parent.is_dir() and not (parent.name == "references" and parent.parent.is_dir()
                                    and parent.parent != root):
        raise PlanError(f"{raw}: the skill folder does not exist (create the skill first)")
    return resolved


def _check_entry_text(n: int, text: Any, limit: int) -> str:
    if not isinstance(text, str) or not text.strip():
        raise PlanError(f"action {n}: needs a non-empty 'text'")
    text = text.strip()
    if any(line.strip() == "§" for line in text.splitlines()):
        raise PlanError(f"action {n}: text may not contain a line holding only '§' (the entry delimiter)")
    if len(text) > limit:
        raise PlanError(f"action {n}: entry is {len(text)} chars, larger than the whole file budget ({limit})")
    return text


def _resolve_index(a: dict, entries: list[str]) -> int:
    n = a["_n"]
    idx = a.get("index")
    eid = a.get("id") or a.get("entry_id")
    by_id = None
    if eid:
        hits = [i for i, e in enumerate(entries) if entry_id(e) == str(eid)]
        if not hits:
            raise PlanError(f"action {n}: no {a['target']} entry has id {eid} (file changed? re-scan)")
        by_id = hits[0]
    if idx is not None:
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            raise PlanError(f"action {n}: index must be an integer") from None
        if not 0 <= idx < len(entries):
            raise PlanError(f"action {n}: {a['target']} has no entry {idx} ({len(entries)} entries)")
        if by_id is not None and by_id != idx:
            raise PlanError(f"action {n}: id {eid} is entry {by_id}, not {idx} (file changed? re-scan)")
    elif by_id is None:
        raise PlanError(f"action {n}: needs 'index' or 'id'")
    idx = by_id if idx is None else idx
    expect = a.get("expect")
    if expect and not entries[idx].startswith(str(expect).strip()):
        raise PlanError(f"action {n}: entry {idx} does not start with the expected text (re-scan)")
    return idx


def _short_diff(old: str, new: str, limit: int = 1200) -> str:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "before", "after", lineterm="", n=1))
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit] + "\n…"


def apply_plan(plan: Any, home: Path | None = None, *, dry_run: bool = True,
               force_unique_loss: bool = False, limits: dict[str, int] | None = None) -> dict:
    """Validate and (unless ``dry_run``) apply ``plan``. Returns a JSON-able result; never raises PlanError."""
    layout = store.Layout(Path(home) if home else store.hermes_home())
    limits = limits or store.read_limits(layout.home)
    try:
        actions = _norm_plan(plan)
        for a in actions:
            if a["action"] in ENTRY_ACTIONS:
                tgt = str(a.get("target", "")).strip().lower()
                if tgt in ("soul", "soul.md"):
                    raise PlanError(f"action {a['_n']}: SOUL.md is owner-authored and is never modified")
                if tgt not in store.TARGETS:
                    raise PlanError(f"action {a['_n']}: target must be 'memory' or 'user'")
                a["target"] = tgt
            else:
                a["_path"] = resolve_skill_path(layout, a.get("path"))
                txt = a.get("text")
                if not isinstance(txt, str) or not txt.strip():
                    raise PlanError(f"action {a['_n']}: append_skill needs a non-empty 'text'")
                if len(txt) > MAX_SKILL_APPEND:
                    raise PlanError(f"action {a['_n']}: skill text over {MAX_SKILL_APPEND} chars")
    except PlanError as exc:
        return {"status": "refused", "dry_run": dry_run, "errors": [str(exc)]}

    paths = {t: layout.memory_file(t) for t in store.TARGETS}
    with store.file_lock(paths["memory"]), store.file_lock(paths["user"]):
        try:
            return _apply_locked(layout, actions, paths, limits, dry_run, force_unique_loss, plan)
        except PlanError as exc:
            return {"status": "refused", "dry_run": dry_run, "errors": [str(exc)]}


def _apply_locked(layout, actions, paths, limits, dry_run, force_unique_loss, raw_plan) -> dict:
    before = {t: store.read_entries(paths[t]) for t in store.TARGETS}
    removed: dict[str, set[int]] = {t: set() for t in store.TARGETS}
    replaced: dict[str, dict[int, str]] = {t: {} for t in store.TARGETS}
    added: dict[str, list[str]] = {t: [] for t in store.TARGETS}
    skill_new: dict[Path, str] = {}
    skill_old: dict[Path, str | None] = {}

    for a in actions:
        n, kind = a["_n"], a["action"]
        if kind == "append_skill":
            p = a["_path"]
            if p not in skill_new:
                old = p.read_text(encoding="utf-8") if p.exists() else None
                skill_old[p] = old
                skill_new[p] = old or ""
            base = skill_new[p].rstrip("\n")
            skill_new[p] = (base + "\n\n" if base else "") + a["text"].strip() + "\n"
            continue
        t = a["target"]
        if kind == "add":
            added[t].append(_check_entry_text(n, a.get("text"), limits[t]))
            continue
        idx = _resolve_index(a, before[t])
        if idx in removed[t] or idx in replaced[t]:
            raise PlanError(f"action {n}: {t} entry {idx} is already changed by an earlier action")
        if kind == "remove":
            removed[t].add(idx)
        else:
            replaced[t][idx] = _check_entry_text(n, a.get("text"), limits[t])

    after: dict[str, list[str]] = {}
    for t in store.TARGETS:
        out = []
        for i, e in enumerate(before[t]):
            if i in removed[t]:
                continue
            out.append(replaced[t].get(i, e))
        out.extend(added[t])
        after[t] = out

    # ── unique-fact protection ──
    losses = []
    touched = [(t, i) for t in store.TARGETS for i in sorted(removed[t] | set(replaced[t]))]
    if touched:
        overrides = {skill_label_for(layout, p): txt for p, txt in skill_new.items()}
        src = build_sources(layout, include_skills=True, skill_overrides=overrides)
        entry_docs = {src.corpus.add(f"memories/{store.FILE_NAMES[t]}#{k}", "entry", e)
                      for t in store.TARGETS for k, e in enumerate(after[t])}
        everywhere = src.soul_docs | src.skill_docs | entry_docs
        for t, i in touched:
            cov = coverage(before[t][i], src.corpus, everywhere)
            if cov.missing_specials or cov.uncovered:
                losses.append({
                    "target": t, "index": i, "id": entry_id(before[t][i]),
                    "missing_literals": cov.missing_specials[:10],
                    "uncovered_sentences": [snippet(s, 140) for s in cov.uncovered[:5]],
                })
    errors = []
    if losses and not force_unique_loss:
        errors.append(
            f"{len(losses)} removed/replaced entr{'y' if len(losses) == 1 else 'ies'} hold facts found nowhere "
            "else; move them into a skill or another entry first, or pass force_unique_loss=true")

    # ── budget ──
    files = {}
    warnings = []
    for t in store.TARGETS:
        b, a_ = store.char_count(before[t]), store.char_count(after[t])
        files[t] = {"file": f"memories/{store.FILE_NAMES[t]}", "chars_before": b, "chars_after": a_,
                    "limit": limits[t], "usage_pct_after": round(100.0 * a_ / limits[t], 1),
                    "entries_before": len(before[t]), "entries_after": len(after[t])}
        if a_ > limits[t]:
            if a_ > b:
                errors.append(f"{store.FILE_NAMES[t]} would be {a_} chars, over its {limits[t]} budget")
            else:
                warnings.append(f"{store.FILE_NAMES[t]} stays over budget ({a_}/{limits[t]}) but shrinks")

    changes = []
    for t in store.TARGETS:
        for i in sorted(removed[t]):
            changes.append({"op": "remove", "target": t, "index": i, "id": entry_id(before[t][i]),
                            "chars": len(before[t][i]), "preview": snippet(before[t][i], 90)})
        for i, new in sorted(replaced[t].items()):
            changes.append({"op": "replace", "target": t, "index": i, "id": entry_id(before[t][i]),
                            "chars_before": len(before[t][i]), "chars_after": len(new),
                            "diff": _short_diff(before[t][i], new)})
        for new in added[t]:
            changes.append({"op": "add", "target": t, "chars": len(new), "preview": snippet(new, 90)})
    for p, txt in skill_new.items():
        old = skill_old[p]
        changes.append({"op": "append_skill", "path": skill_label_for(layout, p), "created": old is None,
                        "chars_added": len(txt) - len(old or "")})

    result = {"status": "refused" if errors else ("dry_run" if dry_run else "applied"),
              "dry_run": dry_run, "files": files, "changes": changes}
    if losses:
        result["unique_fact_losses"] = losses
    if warnings:
        result["warnings"] = warnings
    if errors:
        result["errors"] = errors
        return result
    if dry_run:
        return result

    # ── backup, then write ──
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    bdir = layout.backups / stamp
    bdir.mkdir(parents=True, exist_ok=False)
    manifest = {"created_at": stamp, "files": [], "plan": _plain(raw_plan)}
    for t in store.TARGETS:
        if after[t] != before[t] and paths[t].exists():
            dest = bdir / "memories" / store.FILE_NAMES[t]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(paths[t], dest)
            manifest["files"].append(f"memories/{store.FILE_NAMES[t]}")
    for p, old in skill_old.items():
        label = skill_label_for(layout, p)
        if old is None:
            manifest.setdefault("created", []).append(label)
            continue
        dest = bdir / label
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
        manifest["files"].append(label)
    (bdir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    for t in store.TARGETS:
        if after[t] != before[t]:
            store.atomic_write(paths[t], store.join_entries(after[t]))
    for p, txt in skill_new.items():
        store.atomic_write(p, txt)
    result["backup"] = f"{store.DATA_DIR}/backups/{stamp}"
    return result


def skill_label_for(layout: store.Layout, p: Path) -> str:
    try:
        return f"{store.SKILLS_DIR}/{p.resolve().relative_to(layout.skills.resolve()).as_posix()}"
    except ValueError:
        return p.name


def _plain(plan: Any) -> Any:
    try:
        return json.loads(plan) if isinstance(plan, str) else json.loads(json.dumps(plan, default=str))
    except Exception:
        return str(plan)


def exact_plan(report: dict) -> list[dict]:
    """Removal actions for the near-exact SOUL.md duplicates of a scan report."""
    return [{"action": "remove", "target": d["target"], "index": d["index"], "id": d["id"]}
            for d in report.get("exact_soul_duplicates", [])]
