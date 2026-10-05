"""Core logic for discord-quota-dashboard: Discord REST helpers, quota text
rendering (reusing Hermes' own agent.account_usage), and dashboard refresh.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"
DASHBOARD_MARKER = "<!-- discord-quota-dashboard:v1 -->"
DEFAULT_PROVIDERS = ["anthropic", "nous", "openrouter", "openai-codex"]

SEND_SCHEMA = {
    "name": "discord_send_message",
    "description": "Send a message to a Discord channel or thread (bot token).",
    "parameters": {
        "type": "object",
        "properties": {
            "channel_id": {"type": "string", "description": "Discord channel or thread ID."},
            "content": {"type": "string", "description": "Message text (truncated to 2000 chars)."},
        },
        "required": ["channel_id", "content"],
    },
}

EDIT_SCHEMA = {
    "name": "discord_edit_message",
    "description": "Edit an existing Discord message (bot token).",
    "parameters": {
        "type": "object",
        "properties": {
            "channel_id": {"type": "string", "description": "Discord channel or thread ID."},
            "message_id": {"type": "string", "description": "ID of the message to edit."},
            "content": {"type": "string", "description": "New message text (truncated to 2000 chars)."},
        },
        "required": ["channel_id", "message_id", "content"],
    },
}


# ── Discord REST (bot token) ─────────────────────────────────────────────────
def _get_bot_token() -> Optional[str]:
    """Resolve DISCORD_BOT_TOKEN the same way Hermes' own discord tool does, with a bare
    os.environ fallback so this plugin also works outside a Hermes profile scope."""
    try:
        from agent.secret_scope import get_secret
        token = (get_secret("DISCORD_BOT_TOKEN", "") or "").strip()
        if token:
            return token
    except Exception:
        pass
    return (os.environ.get("DISCORD_BOT_TOKEN") or "").strip() or None


def _request(
    method: str, path: str, token: str, params: Optional[dict] = None,
    body: Optional[dict] = None, timeout: int = 15,
) -> Any:
    url = f"{DISCORD_API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, data=None if body is None else json.dumps(body).encode("utf-8"), method=method,
        headers={
            "Authorization": f"Bot {token}", "Content-Type": "application/json",
            "User-Agent": "discord-quota-dashboard-plugin (https://github.com/)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Discord API {e.code} on {method} {path}: {detail}") from e


def send_message(channel_id: str, content: str) -> dict:
    token = _get_bot_token()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not configured.")
    return _request("POST", f"/channels/{channel_id}/messages", token, body={"content": content[:2000]})


def edit_message(channel_id: str, message_id: str, content: str) -> dict:
    token = _get_bot_token()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not configured.")
    return _request(
        "PATCH", f"/channels/{channel_id}/messages/{message_id}", token, body={"content": content[:2000]})


def list_active_threads(guild_id: str) -> List[dict]:
    token = _get_bot_token()
    if not token:
        return []
    data = _request("GET", f"/guilds/{guild_id}/threads/active", token)
    return (data or {}).get("threads", [])


def handle_send(args: Dict[str, Any], **_kw: Any) -> str:
    try:
        msg = send_message(args["channel_id"], args["content"])
        return json.dumps({"success": True, "message_id": msg.get("id")})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def handle_edit(args: Dict[str, Any], **_kw: Any) -> str:
    try:
        msg = edit_message(args["channel_id"], args["message_id"], args["content"])
        return json.dumps({"success": True, "message_id": msg.get("id")})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ── quota text (reuses Hermes' own /usage machinery) ────────────────────────
def render_quota_text(providers: List[str]) -> str:
    try:
        from agent.account_usage import fetch_account_usage, render_account_usage_lines
    except Exception:
        return f"{DASHBOARD_MARKER}\n⚠️ Quota reporting unavailable on this Hermes version."
    blocks = []
    for name in providers:
        try:
            snap = fetch_account_usage(name)
        except Exception:
            logger.debug("quota fetch failed for %s", name, exc_info=True)
            snap = None
        if not snap:
            continue
        lines = render_account_usage_lines(snap, markdown=True)
        if lines:
            blocks.append("\n".join(lines))
    body = "\n\n".join(blocks) if blocks else "No configured provider exposes a live quota API right now."
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"{DASHBOARD_MARKER}\n**🤖 AI Usage Dashboard** — updated {stamp}\n\n{body}"


# ── channel/thread discovery (generic — config-driven, not hardcoded) ───────
def _discover_channels(ctx) -> List[str]:
    channels = [str(c) for c in (ctx.get_config("channels", []) or [])]
    parents = {str(p) for p in (ctx.get_config("auto_track_parents", []) or [])}
    guild_id = str(ctx.get_config("guild_id", "") or "")
    if parents and guild_id:
        try:
            for t in list_active_threads(guild_id):
                if str(t.get("parent_id")) in parents:
                    channels.append(str(t["id"]))
        except Exception:
            logger.exception("discord-quota-dashboard: thread discovery failed")
    seen, out = set(), []
    for c in channels:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ── refresh ───────────────────────────────────────────────────────────────
def refresh_all(ctx) -> str:
    providers = list(ctx.get_config("providers", DEFAULT_PROVIDERS) or DEFAULT_PROVIDERS)
    text = render_quota_text(providers)
    channels = _discover_channels(ctx)
    if not channels:
        return "No channels configured (set 'channels' and/or 'auto_track_parents' + 'guild_id')."
    state: Dict[str, str] = ctx.state.get("messages", {}) or {}
    results = []
    for channel_id in channels:
        msg_id = state.get(channel_id)
        ok = False
        if msg_id:
            try:
                edit_message(channel_id, msg_id, text)
                ok = True
            except Exception:
                ok = False  # fall through and repost (message was likely deleted)
        if not ok:
            try:
                msg = send_message(channel_id, text)
                state[channel_id] = msg.get("id")
                ok = True
            except Exception as e:
                results.append(f"{channel_id}: FAILED ({e})")
                continue
        results.append(f"{channel_id}: ok")
    ctx.state.set("messages", state)
    return "\n".join(results)
