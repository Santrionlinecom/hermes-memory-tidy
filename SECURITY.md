# Security policy

memory-tidy reads and rewrites files that hold an agent's long-term memory, so we treat any way to
lose, leak or corrupt that data as a security issue.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

Please report privately, not in a public issue:

- GitHub: **Security → Report a vulnerability** on this repository (private vulnerability
  reporting), or
- email websantrionline@gmail.com with the subject `memory-tidy security`.

Include the version, what you did, what happened, and a **synthetic** reproduction. Do not send real
`MEMORY.md`, `USER.md` or `SOUL.md` contents.

We aim to acknowledge a report within 7 days and to agree on a fix and disclosure date with you.
This is a volunteer project, so there is no bug bounty.

## In scope

- A write outside `memories/MEMORY.md`, `memories/USER.md` or `.md` files inside `skills/`
  (path traversal, symlink escape, writes to `SOUL.md`).
- A write when `dry_run` is true, or without a backup.
- A `remove` / `replace` that loses a unique fact without `force_unique_loss`.
- Memory contents or host paths leaking into chat output, logs or other files.
- Corruption under concurrent use with the Hermes memory tool (lock or atomic-replace bugs).
- Any network call, subprocess or credential access added to the plugin.

## Out of scope

- Bugs in Hermes Agent itself; report those to the
  [Hermes Agent project](https://github.com/NousResearch/hermes-agent/security).
- Wrong or unhelpful suggestions from the read-only scan (for example a poor `move_to_skill`
  target); please open a normal issue for those.

## Design notes

No network, no LLM, no credentials, no shell commands, no background processes; dry run by
default; backups under `$HERMES_HOME/memory-tidy/backups/`; the memory tool's lock files; atomic
replace. See the [safety model](README.md#safety-model).
