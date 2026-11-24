# Changelog

## 0.1.0

- `memory_tidy_scan`: read-only report with verdicts, evidence, suggested actions, usage %, hints
  for partial duplicates and possibly stale facts.
- `memory_tidy_apply`: remove / replace / add / append_skill plans with dry run (default), path
  validation, unique-fact protection, budget enforcement, backups, memory file lock, atomic writes.
- `/memory-tidy`, `/memory-tidy apply-exact`, `/memory-tidy preview-exact`.
- Rate-limited session-start check (`pre_llm_call`, first turn only) with optional
  `auto_apply_exact`.
- `hermes memory-tidy` CLI.
