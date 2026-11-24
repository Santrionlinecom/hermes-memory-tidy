"""Glue between Hermes and the engine: settings, tool handlers, the slash command and the check hook."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from . import store
from .applier import apply_plan, exact_plan
from .scanner import DEFAULT_EXACT_THRESHOLD, VERDICTS, scan

COMMAND = "memory-tidy"
CHAT_LIMIT = 3000

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "usage_threshold": 85,
    "exact_threshold": DEFAULT_EXACT_THRESHOLD,
    "check_interval_hours": 24,
    "auto_apply_exact": False,
}


class Settings:
    """Typed view over ``plugins.entries.memory-tidy.settings`` with safe fallbacks."""

    def __init__(self, getter: Callable[[str, Any], Any] | None = None, overrides: dict | None = None):
        self._get = getter
        self._over = dict(overrides or {})

    def raw(self, key: str) -> Any:
        if key in self._over:
            return self._over[key]
        default = DEFAULTS[key]
        if self._get is None:
            return default
        try:
            value = self._get(key, default)
        except Exception:
            return default
        return default if value is None else value

    @staticmethod
    def _bool(v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    @property
    def enabled(self) -> bool:
        return self._bool(self.raw("enabled"))

    @property
    def auto_apply_exact(self) -> bool:
        return self._bool(self.raw("auto_apply_exact"))

    def _num(self, key: str, lo: float, hi: float) -> float:
        try:
            v = float(self.raw(key))
        except (TypeError, ValueError):
            v = float(DEFAULTS[key])
        return min(hi, max(lo, v))

    @property
    def usage_threshold(self) -> float:
        return self._num("usage_threshold", 1, 100)

    @property
    def exact_threshold(self) -> float:
        # Never below 0.8: this number gates the only automatic deletion.
        return self._num("exact_threshold", 0.8, 1.0)

    @property
    def check_interval_hours(self) -> float:
        return self._num("check_interval_hours", 0, 24 * 365)


# ── tools ─────────────────────────────────────────────────────────────────────

SCAN_SCHEMA = {
    "name": "memory_tidy_scan",
    "description": (
        "Read-only audit of the persistent memory files (memories/MEMORY.md and memories/USER.md). "
        "Compares every entry with SOUL.md, the skills and the other entries using deterministic text "
        "similarity and returns JSON: usage % per file and, per entry, a verdict (duplicate_of_soul, "
        "duplicate_of_skill, overlaps_entry, procedure_like, ok), evidence with matched snippets and "
        "locations, and a suggested_action (remove, merge_with:<idx>, move_to_skill:<path>, keep). "
        "Use it before tidying memory; then build a plan for memory_tidy_apply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "include_skills": {"type": "boolean", "description": "Also compare with skills/ (default true)."},
            "verdicts": {"type": "array", "items": {"type": "string", "enum": list(VERDICTS)},
                         "description": "Only return entries with these verdicts (summary still covers all)."},
        },
        "required": [],
    },
}

APPLY_SCHEMA = {
    "name": "memory_tidy_apply",
    "description": (
        "Apply a reviewed tidy plan to memories/MEMORY.md, memories/USER.md and skill files. DRY RUN BY "
        "DEFAULT: returns the diff and writes nothing until dry_run=false. Actions: "
        "{action:'remove', target:'memory'|'user', index|id}, {action:'replace', target, index|id, text}, "
        "{action:'add', target, text}, {action:'append_skill', path:'skills/<...>.md', text}. Never touches "
        "SOUL.md or paths outside skills/. Refuses a remove/replace that would lose facts found nowhere "
        "else (move them to a skill first) unless force_unique_loss=true, and refuses to exceed the memory "
        "char budget. Real runs back up every touched file under memory-tidy/backups/ and write atomically "
        "under the memory file lock. Move unique details into the skill in the same plan as the removal."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "actions": {"type": "array", "items": {"type": "object"}, "description": "The plan."},
            "dry_run": {"type": "boolean", "description": "Default true. Set false to write."},
            "force_unique_loss": {"type": "boolean",
                                  "description": "Allow dropping facts not present elsewhere (default false)."},
        },
        "required": ["actions"],
    },
}


def tool_scan(args: dict, settings: Settings, home: Path | None = None) -> dict:
    report = scan(home, exact_threshold=settings.exact_threshold,
                  include_skills=args.get("include_skills", True) is not False)
    wanted = args.get("verdicts")
    if isinstance(wanted, list) and wanted:
        report["entries"] = [e for e in report["entries"] if e["verdict"] in wanted]
    return report


def tool_apply(args: dict, settings: Settings, home: Path | None = None) -> dict:
    dry = args.get("dry_run", True)
    dry = not (dry is False or str(dry).strip().lower() in ("false", "0", "no"))
    force = args.get("force_unique_loss") is True or str(args.get("force_unique_loss")).lower() == "true"
    return apply_plan(args.get("actions"), home, dry_run=dry, force_unique_loss=force)


def json_handler(fn: Callable[[dict], dict]) -> Callable[..., str]:
    def handler(args: dict | None = None, **_: Any) -> str:
        try:
            return json.dumps(fn(args or {}), ensure_ascii=False, default=str)
        except Exception as exc:  # a tool answers, it does not raise
            return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"[:500]})
    return handler


# ── slash command ─────────────────────────────────────────────────────────────

USAGE = f"""\
/{COMMAND}: tidy the persistent memory files
  /{COMMAND}                 scan summary (read-only)
  /{COMMAND} apply-exact     remove entries that are near-exact copies of SOUL.md (with backup)
  /{COMMAND} preview-exact   show what apply-exact would remove, write nothing
Other changes go through the memory_tidy_apply tool (dry run first)."""


def summarize(report: dict, limit: int = 12) -> str:
    lines = ["memory-tidy scan (read-only)"]
    for t, f in report["files"].items():
        lines.append(f"  {f['file']}: {f['chars']}/{f['limit']} chars ({f['usage_pct']}%), {f['entries']} entries")
    lines.append(f"  compared with SOUL.md and {report['skills_indexed']} skill files")
    lines.append("Verdicts: " + ", ".join(f"{k}={v}" for k, v in report["summary"].items()))
    flagged = [e for e in report["entries"] if e["verdict"] != "ok"]
    for e in flagged[:limit]:
        s = e["scores"]
        score = {"duplicate_of_soul": s["soul_exact"] if s["soul_exact"] >= s["soul_coverage"] else s["soul_coverage"],
                 "duplicate_of_skill": s["skill_coverage"], "procedure_like": s["procedure"]}.get(e["verdict"])
        extra = f" ({score})" if score is not None else ""
        lines.append(f"  {e['target']}#{e['index']} [{e['id']}] {e['verdict']}{extra} -> {e['suggested_action']}")
    if len(flagged) > limit:
        lines.append(f"  ... {len(flagged) - limit} more (ask the agent to run memory_tidy_scan)")
    n_exact = len(report.get("exact_soul_duplicates", []))
    if n_exact:
        lines.append(f"{n_exact} near-exact SOUL.md duplicate(s): /{COMMAND} apply-exact removes them (backup kept).")
    if flagged:
        lines.append("Ask the agent to review the scan and prepare a memory_tidy_apply plan (dry run first).")
    else:
        lines.append("Nothing to tidy.")
    return _clip("\n".join(lines))


def run_exact(settings: Settings, home: Path | None = None, dry_run: bool = False) -> dict:
    report = scan(home, exact_threshold=settings.exact_threshold, include_skills=False)
    plan = exact_plan(report)
    if not plan:
        return {"status": "nothing_to_do", "removed": 0}
    result = apply_plan(plan, home, dry_run=dry_run, force_unique_loss=False)
    result["removed"] = 0 if dry_run or result["status"] != "applied" else len(plan)
    return result


def handle_slash(raw_args: str, settings: Settings, home: Path | None = None) -> str:
    args = (raw_args or "").split()
    sub = args[0].lower() if args else ""
    if sub in ("help", "-h", "--help") or len(args) > 1:
        return USAGE
    try:
        if not sub or sub == "scan":
            return summarize(scan(home, exact_threshold=settings.exact_threshold))
        if sub in ("apply-exact", "preview-exact"):
            dry = sub == "preview-exact"
            res = run_exact(settings, home, dry_run=dry)
            if res["status"] == "nothing_to_do":
                return f"No entry is a near-exact copy of SOUL.md (threshold {settings.exact_threshold})."
            if res["status"] == "refused":
                return "apply-exact refused: " + "; ".join(res.get("errors", []))
            gone = [c for c in res["changes"] if c["op"] == "remove"]
            head = "Would remove" if dry else "Removed"
            lines = [f"{head} {len(gone)} near-exact SOUL.md duplicate(s):"]
            lines += [f"  {c['target']}#{c['index']} [{c['id']}] {c['chars']} chars" for c in gone]
            for t, f in res["files"].items():
                lines.append(f"  {f['file']}: {f['chars_before']} -> {f['chars_after']} / {f['limit']} chars")
            if not dry:
                lines.append(f"Backup: $HERMES_HOME/{res['backup']}")
            return _clip("\n".join(lines))
    except Exception as exc:
        return f"memory-tidy failed: {type(exc).__name__}: {str(exc)[:200]}"
    return f"Unknown subcommand {sub!r}.\n\n{USAGE}"


def _clip(text: str) -> str:
    return text if len(text) <= CHAT_LIMIT else text[: CHAT_LIMIT - 40] + "\n... (truncated)"


# ── periodic check (pre_llm_call on the first turn) ───────────────────────────

def _read_state(layout: store.Layout) -> dict:
    try:
        return json.loads(layout.state_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(layout: store.Layout, state: dict) -> None:
    try:
        store.atomic_write(layout.state_file, json.dumps(state))
    except OSError:
        pass


def periodic_check(settings: Settings, home: Path | None = None, now: float | None = None) -> str | None:
    """Return a short note for the agent, or None. Rate-limited through a state file.

    Uses the cheap SOUL-only scan (no skills) so it stays far below the hook timeout.
    """
    if not settings.enabled:
        return None
    layout = store.Layout(Path(home) if home else store.hermes_home())
    now = time.time() if now is None else now
    state = _read_state(layout)
    interval = settings.check_interval_hours * 3600
    last = state.get("last_check")
    if isinstance(last, (int, float)) and 0 <= now - last < interval:
        return None
    state["last_check"] = now
    _write_state(layout, state)

    report = scan(layout.home, exact_threshold=settings.exact_threshold, include_skills=False)
    high = [f"{f['file']} {f['usage_pct']}%" for f in report["files"].values()
            if f["usage_pct"] >= settings.usage_threshold]
    exact = report.get("exact_soul_duplicates", [])
    if not high and not exact:
        return None
    parts = []
    if exact and settings.auto_apply_exact:
        res = run_exact(settings, layout.home, dry_run=False)
        if res.get("status") == "applied":
            parts.append(f"memory-tidy removed {res['removed']} entr{'y' if res['removed'] == 1 else 'ies'} that "
                         f"duplicated SOUL.md (backup in $HERMES_HOME/{res['backup']}).")
            exact = []
    if high:
        parts.append("Persistent memory is nearly full: " + ", ".join(high) + ".")
    if exact:
        parts.append(f"{len(exact)} memory entr{'y duplicates' if len(exact) == 1 else 'ies duplicate'} SOUL.md.")
    if high or exact:
        parts.append("Suggest that the user runs /memory-tidy (read-only scan); do not edit memory unasked.")
    return "[memory-tidy] " + " ".join(parts)
