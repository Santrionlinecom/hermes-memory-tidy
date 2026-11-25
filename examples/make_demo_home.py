"""Build a throw-away HERMES_HOME with synthetic SOUL/MEMORY/USER/skills for a demo scan.

Usage:
    python examples/make_demo_home.py /tmp/demo-home
    python -m memory_tidy.cli --home /tmp/demo-home scan

Everything written here is invented sample data; no real profile is read.
"""

from __future__ import annotations

import sys
from pathlib import Path

DELIM = "\n§\n"

SOUL = """\
# Identity

You are Orbit, a calm assistant for the Acme Docs team.
Always reply in plain English with short bullet points.
Never publish or deploy anything without explicit approval from the owner.
"""

MEMORY = [
    "Never publish or deploy anything without explicit approval from the owner.",
    "To release the docs site: 1) run `npm run build` 2) run `wrangler pages deploy dist "
    "--project-name acme-docs` 3) check https://docs.acme.example/health returns 200.",
    "The weekly changelog digest is posted every Monday at 09:00 to the team channel.",
    "Weekly changelog digest goes out every Monday at 09:00 in the team channel; skipped on holidays.",
    "Prefers ISO dates (YYYY-MM-DD) in every report.",
]

USER = [
    "The owner maintains the Acme docs and reviews pull requests in the evening.",
]

SKILL = """\
---
name: docs-release
description: Use when releasing the Acme docs site with wrangler pages.
---

# Docs release

Build with npm, then deploy the dist folder with wrangler pages to the acme-docs project.
Check the health endpoint after every release.
"""


def main(target: str) -> None:
    home = Path(target)
    (home / "memories").mkdir(parents=True, exist_ok=True)
    (home / "SOUL.md").write_text(SOUL, encoding="utf-8")
    (home / "memories" / "MEMORY.md").write_text(DELIM.join(MEMORY), encoding="utf-8")
    (home / "memories" / "USER.md").write_text(DELIM.join(USER), encoding="utf-8")
    skill = home / "skills" / "devops" / "docs-release" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(SKILL, encoding="utf-8")
    (home / "config.yaml").write_text("memory:\n  memory_char_limit: 1000\n  user_char_limit: 800\n",
                                      encoding="utf-8")
    print(f"demo HERMES_HOME written to {home}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo-home")
