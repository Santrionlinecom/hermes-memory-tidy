"""`hermes memory-tidy ...` host commands (also runnable as `python -m memory_tidy.cli`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .service import Settings, run_exact, summarize, user_apply
from .scanner import scan


def build_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", help="HERMES_HOME to use (default: the active profile)")
    sub = parser.add_subparsers(dest="tidy_cmd")
    p = sub.add_parser("scan", help="read-only scan")
    p.add_argument("--json", action="store_true", help="print the full JSON report")
    p.add_argument("--out", help="write the JSON report to this file")
    p.add_argument("--no-skills", action="store_true", help="compare with SOUL.md only")
    p = sub.add_parser("apply", help="apply a plan file (dry run unless --write)")
    p.add_argument("plan", help="JSON file with a list of actions")
    p.add_argument("--write", action="store_true", help="really write (default: dry run)")
    p.add_argument("--force-unique-loss", action="store_true")
    p = sub.add_parser("apply-exact", help="remove near-exact SOUL.md duplicates")
    p.add_argument("--write", action="store_true", help="really write (default: dry run)")


def run_cli(args: argparse.Namespace, settings: Settings | None = None) -> int:
    settings = settings or Settings()
    home = Path(args.home).expanduser() if getattr(args, "home", None) else None
    cmd = getattr(args, "tidy_cmd", None) or "scan"
    if cmd == "scan":
        report = scan(home, exact_threshold=settings.exact_threshold,
                      include_skills=not getattr(args, "no_skills", False))
        if getattr(args, "out", None):
            Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False) if getattr(args, "json", False) else summarize(report))
        return 0
    if cmd == "apply":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        res = user_apply(plan, home, write=args.write, force_unique_loss=args.force_unique_loss)
    else:
        res = run_exact(settings, home, dry_run=not args.write)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("status") in ("applied", "dry_run", "nothing_to_do") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memory-tidy")
    build_parser(parser)
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
