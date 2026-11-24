"""memory-tidy: keep MEMORY.md / USER.md lean by finding SOUL.md duplicates, misplaced procedures
and overlapping entries, and by applying reviewed tidy plans safely (dry run, backups, locks)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("memory_tidy")

PLUGIN_NAME = "memory-tidy"
TOOLSET = "memory_tidy"


# Engine imports happen inside register(): test runners may import this file outside a package,
# and Hermes imports it as a package, where the relative imports resolve.


def register(ctx: Any) -> None:
    from .memory_tidy import service
    from .memory_tidy.service import APPLY_SCHEMA, COMMAND, SCAN_SCHEMA, Settings

    def _settings(ctx: Any) -> "Settings":
        return Settings(getattr(ctx, "get_config", None))

    ctx.register_tool(
        name="memory_tidy_scan", toolset=TOOLSET, schema=SCAN_SCHEMA,
        handler=service.json_handler(lambda args: service.tool_scan(args, _settings(ctx))),
        description=SCAN_SCHEMA["description"], emoji="🧹",
    )
    ctx.register_tool(
        name="memory_tidy_apply", toolset=TOOLSET, schema=APPLY_SCHEMA,
        handler=service.json_handler(lambda args: service.tool_apply(args, _settings(ctx))),
        description=APPLY_SCHEMA["description"], emoji="🧹",
    )

    def on_pre_llm_call(session_id: str = "", is_first_turn: bool = False, parent_session_id: str = "",
                        **_kwargs: Any) -> Any:
        # Once per new top-level session at most, and further rate-limited by check_interval_hours.
        # Only declared fields are read; the user message and history are ignored.
        if not is_first_turn or parent_session_id:
            return None
        try:
            note = service.periodic_check(_settings(ctx))
        except Exception as exc:  # fail open: never block a turn
            logger.debug("memory-tidy: periodic check skipped: %s", exc)
            return None
        return {"context": note} if note else None

    ctx.register_hook("pre_llm_call", on_pre_llm_call)

    ctx.register_command(
        COMMAND, handler=lambda raw_args="": service.handle_slash(raw_args, _settings(ctx)),
        description="Scan memory for SOUL.md duplicates, procedures and overlaps; apply-exact removes exact copies",
        args_hint="[apply-exact|preview-exact]",
    )

    register_cli = getattr(ctx, "register_cli_command", None)
    if callable(register_cli):
        from .memory_tidy.cli import build_parser, run_cli

        register_cli(
            name=PLUGIN_NAME,
            help="Scan and tidy MEMORY.md / USER.md (read-only scan, dry-run apply)",
            description="memory-tidy compares memory entries with SOUL.md and skills and applies reviewed plans.",
            setup_fn=build_parser,
            handler_fn=lambda args: run_cli(args, _settings(ctx)),
        )
