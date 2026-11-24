"""Fixtures: a throw-away HERMES_HOME with fake SOUL/MEMORY/USER/skills (no real data)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DELIM = "\n§\n"

SOUL = """\
# Identity

You are Nova, a careful assistant for the Example Lab team.
Always answer in plain English.
Never publish anything without explicit approval from the owner.
The owner prefers short replies with bullet points and no filler.
Production deploys happen only on Fridays after a peer review.
"""

MEMORY = [
    # 0 near-exact copy of SOUL.md
    "Never publish anything without explicit approval from the owner. "
    "The owner prefers short replies with bullet points and no filler.",
    # 1 procedure that belongs in the deploy skill
    "To deploy the docs site: 1) run `npm run build` 2) run `wrangler pages deploy dist --project-name demo-docs` "
    "3) check https://demo-docs.example.org/health returns 200. Config lives in ~/projects/demo-docs/wrangler.toml.",
    # 2 and 3 overlap each other
    "The team calendar sync runs every morning at 06:00 and posts a digest to the planning channel.",
    "Calendar sync runs every morning at 06:00 and posts the digest to the team planning channel; it skips weekends.",
    # 4 unique, fine
    "Prefers metric units for all measurements and recipes.",
    # 5 copied from a skill
    "Nightly backups are written to the archive bucket at 02:30 and kept for 30 days.",
]

USER = [
    "The user is a hobby astronomer who enjoys telescope gear reviews.",
    "Lives in a small coastal town and usually works late in the evening.",
]

DEPLOY_SKILL = """\
---
name: demo-deploy
description: Use when deploying the demo docs site with wrangler pages.
---

# Demo docs deploy

Build with npm, then deploy the dist folder with wrangler pages to the demo-docs project.
Check the health endpoint after every deploy.
"""

BACKUP_SKILL = """\
---
name: backup-routine
description: Use when asked about nightly backups and retention.
---

# Backup routine

Nightly backups are written to the archive bucket at 02:30 and kept for 30 days.
Restores are tested once a month.
"""

CONFIG = "memory:\n  memory_char_limit: 2000\n  user_char_limit: 1500\n"
LIMITS = {"memory": 2000, "user": 1500}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "hermes-home"
    (h / "memories").mkdir(parents=True)
    (h / "SOUL.md").write_text(SOUL, encoding="utf-8")
    (h / "memories" / "MEMORY.md").write_text(DELIM.join(MEMORY), encoding="utf-8")
    (h / "memories" / "USER.md").write_text(DELIM.join(USER), encoding="utf-8")
    for rel, text in (("devops/demo-deploy/SKILL.md", DEPLOY_SKILL), ("notes/backup-routine/SKILL.md", BACKUP_SKILL)):
        p = h / "skills" / rel
        p.parent.mkdir(parents=True)
        p.write_text(text, encoding="utf-8")
    (h / "config.yaml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(h))
    return h


def read_entries(home: Path, name: str = "MEMORY.md") -> list[str]:
    raw = (home / "memories" / name).read_text(encoding="utf-8")
    return [e.strip() for e in raw.split(DELIM) if e.strip()]


@pytest.fixture
def plugin_module():
    """Load the repo root as a plugin package, the way Hermes' loader does."""
    name = "memory_tidy_plugin_under_test"
    spec = importlib.util.spec_from_file_location(name, REPO / "__init__.py", submodule_search_locations=[str(REPO)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        del sys.modules[key]
