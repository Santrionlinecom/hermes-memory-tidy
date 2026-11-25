# Changelog

## Unreleased

Documentation and repository presentation only; no change to plugin behaviour.

- README rewritten: banner, badges, feature table, quick start, real example output from a
  synthetic demo profile, how it works, safety model, configuration, known limitations, FAQ.
- `assets/banner.svg` and `assets/banner.png` (original artwork, rendered from the SVG).
- `examples/make_demo_home.py`: builds a synthetic `HERMES_HOME` for trying the scanner.
- `tests/test_example.py`: keeps the README example output in sync with the scanner.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), issue
  templates and a pull request template.
- Known limitation documented: `move_to_skill` can suggest an unrelated skill (#1).

## 0.1.0

- `memory_tidy_scan`: read-only report with verdicts, evidence, suggested actions, usage %, hints
  for partial duplicates and possibly stale facts.
- `memory_tidy_apply`: remove / replace / add / append_skill plans with dry run (default), path
  validation, unique-fact protection, budget enforcement, backups, memory file lock, atomic writes.
- `/memory-tidy`, `/memory-tidy apply-exact`, `/memory-tidy preview-exact`.
- Rate-limited session-start check (`pre_llm_call`, first turn only) with optional
  `auto_apply_exact`.
- `hermes memory-tidy` CLI.
