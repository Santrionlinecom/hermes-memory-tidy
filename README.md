# memory-tidy

A [Hermes Agent](https://hermes-agent.nousresearch.com/docs) plugin that keeps the persistent memory
files lean.

Hermes puts three texts into every session: `SOUL.md` (written by the owner), and
`memories/MEMORY.md` and `memories/USER.md` (written by the agent, each with a hard character
budget). Over time the two memory files collect:

- facts that `SOUL.md` already states,
- procedures and technical details that belong in a skill (`skills/**/SKILL.md` or its `references/`),
- entries that overlap each other, and stale facts.

memory-tidy finds these with deterministic text similarity (no network, no LLM) and applies a tidy
plan you have reviewed, with a dry run, backups, the memory tool's file lock and atomic writes.

## Features

| Surface | What it does |
|---|---|
| `memory_tidy_scan` tool | Read-only JSON report: usage % per file and, per entry, a verdict (`duplicate_of_soul`, `duplicate_of_skill`, `overlaps_entry`, `procedure_like`, `ok`), evidence (matched snippets with `file:line`) and a suggested action (`remove`, `merge_with:<idx>`, `move_to_skill:<path>`, `keep`), plus hints for partial duplicates and possibly stale facts. |
| `memory_tidy_apply` tool | Applies a plan of `remove`, `replace`, `add` and `append_skill` actions. Dry run by default. |
| `/memory-tidy` | Scan summary in chat (read-only). |
| `/memory-tidy apply-exact` | Removes only entries that are near-exact copies of `SOUL.md` (backup kept). `preview-exact` shows them without writing. |
| Session-start check | On the first turn of a new top-level session, at most once per `check_interval_hours`, adds a one-line note suggesting `/memory-tidy` when a memory file is over `usage_threshold` % or exact `SOUL.md` duplicates exist. |
| `hermes memory-tidy` CLI | `scan [--json] [--out FILE] [--no-skills]`, `apply PLAN.json [--write]`, `apply-exact [--write]`. |

## Install

```bash
hermes plugins install Santrionlinecom/hermes-memory-tidy
hermes plugins enable memory-tidy
```

Or clone it yourself: `git clone https://github.com/Santrionlinecom/hermes-memory-tidy ~/.hermes/plugins/memory-tidy`.

A gateway that is already running picks the plugin up on its next restart (or when Hermes reloads
plugins). Requires Hermes >= 0.21.5 and Python >= 3.10; no third-party packages.

## Usage

1. Run `/memory-tidy` (or ask the agent to call `memory_tidy_scan`).
2. Review the flagged entries. For a procedure, first move its unique details into the suggested
   skill, then remove the entry - in one plan:

   ```json
   [
     {"action": "append_skill", "path": "skills/devops/demo-deploy/SKILL.md",
      "text": "## Deploy steps\n\n1. npm run build\n2. wrangler pages deploy dist"},
     {"action": "remove", "target": "memory", "id": "3f2a9c01be", "expect": "To deploy the docs"}
   ]
   ```

3. Call `memory_tidy_apply` with the plan (dry run), read the diff, then call it again with
   `"dry_run": false`.

Actions:

| Action | Fields |
|---|---|
| `remove` | `target` (`memory`/`user`), `index` or `id`, optional `expect` (prefix guard) |
| `replace` | `target`, `index` or `id`, `text` (use it to merge overlapping entries) |
| `add` | `target`, `text` |
| `append_skill` | `path` (a `.md` file inside `skills/`), `text` |

Indexes and ids come from the scan report and refer to the files as they are when the plan runs;
if the file changed since the scan, the plan is refused and you re-scan.

## Safety model

- **SOUL.md is never written.** Only `memories/MEMORY.md`, `memories/USER.md` and `.md` files inside
  `skills/` can change. Paths outside `skills/`, `..` tricks, absolute paths elsewhere and symlinks
  that escape are refused. `append_skill` only appends and only inside an existing skill folder
  (or its `references/`).
- **No silent fact loss.** A `remove` or `replace` is refused when the old text holds a literal
  (path, URL, ID, number, version, code span) or a sentence that is not present in `SOUL.md`, the
  skills or the remaining entries after the plan. Override with `force_unique_loss: true`.
- **Budget.** Each memory file must end within the budget Hermes enforces
  (`memory.memory_char_limit` / `memory.user_char_limit`, same counting rule as the memory tool).
  A file that is already over budget may shrink but never grow.
- **Dry run by default.** Nothing is written unless `dry_run` is `false`.
- **Backups.** A real run copies every file it changes to
  `$HERMES_HOME/memory-tidy/backups/<timestamp>/` with a `manifest.json` holding the plan.
- **Concurrency.** Reads and writes happen under the memory tool's own lock files
  (`MEMORY.md.lock`, `USER.md.lock`); files are replaced atomically (temp file + rename).
- **The only automatic deletion** is `apply-exact`: entries whose near-exact similarity to `SOUL.md`
  is at least `exact_threshold` (never below 0.8) and whose literals all appear in `SOUL.md`. The
  periodic check only does this when `auto_apply_exact: true`.
- The scan is read-only; the periodic check writes only its timestamp to
  `$HERMES_HOME/memory-tidy/state.json`. Chat output carries no host paths.

## Disclosure

No network calls, no LLM calls, no credentials, no shell commands and no background processes.
Reads `SOUL.md`, `memories/MEMORY.md`, `memories/USER.md` and the `SKILL.md` files under `skills/`.
Writes only when a tidy plan is applied with `dry_run: false` (memory files and skill `.md` files,
with backups under `$HERMES_HOME/memory-tidy/`), plus the periodic check's timestamp in
`$HERMES_HOME/memory-tidy/state.json` (and, only if you set `auto_apply_exact: true`, near-exact SOUL.md duplicates removed with a backup). One `pre_llm_call` hook adds a short suggestion to the
first turn of a new session when a memory file is near its budget.

## Configuration

`config.yaml` → `plugins.entries.memory-tidy.settings`:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Run the session-start check. |
| `usage_threshold` | `85` | Percent of a file's budget that triggers the suggestion. |
| `exact_threshold` | `0.92` | Near-exact similarity for `apply-exact` (clamped to 0.8-1.0). |
| `check_interval_hours` | `24` | Minimum hours between checks. |
| `auto_apply_exact` | `false` | Let the check remove near-exact `SOUL.md` duplicates itself. |

## How the similarity works

- Text is NFKC-normalised, lower-cased and split into word tokens without stopwords.
- Each entry sentence is matched against the best source sentence (plus its neighbour) through an
  inverted index; **coverage** is the token-weighted containment of the entry in the source.
- **Near-exact** is the share of the entry's 3-token shingles found in `SOUL.md`, capped by the share
  of its literals found there.
- An entry is a duplicate when coverage >= 0.8 and every literal is present (or near-exact >= the
  threshold). Overlaps use token containment / shingle overlap between entries.
- Procedure-likeness scores numbered steps, command lines, CLI flags, paths, URLs, IDs, versions,
  imperative sentences and env variables.
- The first scan of a large skills tree takes a few seconds; later scans in the same process reuse
  the index until a file changes.

## Development

```bash
python -m pytest            # from the repo root; needs pytest only
python -m memory_tidy.cli --home /path/to/hermes-home scan
```

Tests build a temporary `HERMES_HOME` with fake files; they never read a real profile.

## License

MIT - see [LICENSE](LICENSE).
