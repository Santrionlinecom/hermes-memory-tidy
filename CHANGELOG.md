# Changelog

## 0.1.2

Review feedback on the plugin-catalog listing (NousResearch/hermes-agent#159817).

- The `memory_tidy_apply` tool honours `write_approval`. With `memory.write_approval: true` (or
  `skills.write_approval: true` for skill files) a real run writes nothing: the tool returns the
  dry-run diff, saves the plan under `$HERMES_HOME/memory-tidy/plans/` and answers
  `"status": "needs_user"` with the `hermes memory-tidy apply <plan> --write` command for the user.
  The setting is read like Hermes reads it (home `config.yaml`, then `tools.write_approval` for the
  active profile); an unparsable `config.yaml` counts as on.
- The tool no longer commits model-written text. `add`, `replace` and `append_skill` are dry-run only
  from the tool, whatever `write_approval` says; they are applied by the user's CLI command, which
  now calls the applier directly instead of the tool path. A real run from the tool commits
  `remove` actions only.
- The periodic check skips `auto_apply_exact` while `memory.write_approval` is on and says so.
- Tests: `tests/test_write_gate.py`.

## 0.1.1

- Fix #1: `move_to_skill` no longer points at unrelated skills. A target now needs at least two
  uncommon entry words in its folder name, `name` or `description`, three shared words and a 15%
  lead over the runner-up; otherwise the suggestion is `move_to_skill:<new skill>`. Words shared by
  most of the profile's own entries (owner name, year) are ignored as topic. A partly-covering skill
  must pass the same topic check. The evidence now carries `skill_candidates` (top three, with score,
  shared word count and matched head words).
- Fix: memory limits are read from the scanned home's own `config.yaml` first. Hermes' loader only
  knows the active profile, so scanning another profile (or a test home) reported the active
  profile's limits.
- Regression tests with a synthetic 60-skill tree full of decoys.

## Documentation update (after 0.1.0)

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
