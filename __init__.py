"""Discord Quota Dashboard — posts & live-updates an AI usage/quota dashboard
message in Discord channels and threads.

Generic by design: which channels/threads get a dashboard is config-driven, not
hardcoded. Configure under plugins.entries.discord-quota-dashboard.settings in
config.yaml:

    channels: ["123456789012345678"]          # always-tracked channel/thread IDs
    auto_track_parents: ["123456789012345678"]  # parent channel(s); every active
                                                   # thread under them is auto-tracked
    guild_id: "987654321098765432"           # required when auto_track_parents is set
    providers: ["anthropic", "nous"]          # which quota providers to show
    refresh_minutes: 15                       # background refresh cadence (0 disables)

Then enable it:  hermes plugins enable discord-quota-dashboard

Manual refresh:  /quota_dashboard   (in any chat)
                 hermes quota-dashboard             (CLI)

The background cadence runs as a detached OS-level loop (not an in-process
asyncio task — see core.ensure_loop_running's docstring for why), started from
register() and self-healing: it relaunches automatically on every gateway or
container restart, no manual step required.
"""
from __future__ import annotations

import logging

from . import core

logger = logging.getLogger(__name__)


def register(ctx):
    # Generic send/edit tools: useful on their own, not just for this dashboard.
    ctx.register_tool(
        name="discord_send_message", toolset="discord-quota-dashboard",
        schema=core.SEND_SCHEMA, handler=core.handle_send,
        requires_env=["DISCORD_BOT_TOKEN"],
        description="Send a message to a Discord channel or thread.",
    )
    ctx.register_tool(
        name="discord_edit_message", toolset="discord-quota-dashboard",
        schema=core.EDIT_SCHEMA, handler=core.handle_edit,
        requires_env=["DISCORD_BOT_TOKEN"],
        description="Edit an existing Discord message.",
    )

    def _run_refresh(_raw_args: str = "") -> str:
        try:
            return core.refresh_all(ctx)
        except Exception as e:
            logger.exception("discord-quota-dashboard: refresh failed")
            return f"Dashboard refresh failed: {e}"

    ctx.register_command(
        "quota_dashboard", _run_refresh,
        description="Refresh the AI usage/quota dashboard in every tracked channel/thread now.",
    )

    def _cli_setup(_parser):
        pass

    def _cli_handler(_args):
        print(_run_refresh())

    ctx.register_cli_command(
        "quota-dashboard", "Refresh the AI usage/quota dashboard now.",
        _cli_setup, _cli_handler,
    )

    try:
        minutes = int(ctx.get_config("refresh_minutes", 15) or 0)
    except (TypeError, ValueError):
        minutes = 15

    if minutes > 0:
        try:
            status = core.ensure_loop_running(minutes * 60)
            logger.info("discord-quota-dashboard: external refresh loop %s", status)
        except Exception:
            logger.exception("discord-quota-dashboard: failed to start external refresh loop")
