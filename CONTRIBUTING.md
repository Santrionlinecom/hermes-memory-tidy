# Contributing to memory-tidy

Thanks for helping. memory-tidy is a small community plugin for
[Hermes Agent](https://github.com/NousResearch/hermes-agent); it is not part of the Hermes Agent
project, so report Hermes bugs upstream and memory-tidy bugs here.

## Ground rules

- **Never share real memory.** `MEMORY.md`, `USER.md` and `SOUL.md` are personal. In issues, tests and
  pull requests use a small synthetic example instead; `examples/make_demo_home.py` and
  `tests/conftest.py` show the format.
- **Stay local.** No network calls, no LLM calls, no third-party runtime dependencies. The plugin must
  keep working with nothing but the Python standard library.
- **Keep the safety model.** Dry run by default, backups, the memory tool's lock, atomic writes, and
  the unique-fact check. A change that weakens one of these needs a strong reason in the PR.
- Be kind; see the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

```bash
git clone https://github.com/Santrionlinecom/hermes-memory-tidy
cd hermes-memory-tidy
python -m pip install pytest      # the only development dependency
python -m pytest                  # all tests must pass
```

Try the scanner on the synthetic profile:

```bash
python examples/make_demo_home.py /tmp/demo-home
python -m memory_tidy.cli --home /tmp/demo-home scan
```

To test inside Hermes, copy (do not symlink) the repo to a **throw-away** profile's
`plugins/memory-tidy/` and restart that profile's gateway.

## Pull requests

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. One topic per PR. Add or update a test for every behaviour change.
3. If the scan output changes, update the README example and `tests/test_example.py` together.
4. Add a line under `## Unreleased` in [CHANGELOG.md](CHANGELOG.md).
5. Fill in the pull request template, including the raw `pytest` summary line.

Style: Python >= 3.10, type hints on public functions, small pure functions in `textsim.py` and
`scanner.py`, all writes through `store.py`.

## Reporting bugs and ideas

Use the issue templates. For security problems, do **not** open a public issue; follow
[SECURITY.md](SECURITY.md).
