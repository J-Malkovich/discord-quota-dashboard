# discord-quota-dashboard

A Hermes Agent plugin that posts a live, self-updating "AI usage" dashboard
message into Discord channels and threads — your Anthropic/Nous/OpenRouter/
Codex subscription quota windows, visible right in the chat you're already in.

## Why

Hermes' built-in `/usage` gives you a token/cost estimate on request. This
plugin instead surfaces the *real* subscription quota (e.g. Anthropic's OAuth
`/api/oauth/usage` 5-hour/weekly windows) as a message that gets edited in
place on a timer — like a tiny live dashboard pinned in your server.

## How it's generic

Nothing about *which* channels get a dashboard is hardcoded:

- `channels` — a static list of channel/thread IDs to always maintain one in.
- `auto_track_parents` + `guild_id` — every currently-active thread under each
  listed parent channel is auto-discovered and gets its own dashboard message,
  picked up on the next refresh cycle without any manual step.
- `providers` — which quota backends to show (reuses Hermes' own
  `agent.account_usage` fetchers: `anthropic`, `nous`, `openrouter`,
  `openai-codex`, plus anything a model-provider plugin itself exposes
  `fetch_account_usage` for).

## Install

```bash
hermes plugins install <owner>/discord-quota-dashboard --enable
```

or drop this directory into `~/.hermes/plugins/discord-quota-dashboard/` and:

```bash
hermes plugins enable discord-quota-dashboard
```

## Configure

`~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - discord-quota-dashboard
  entries:
    discord-quota-dashboard:
      settings:
        channels: ["123456789012345678"]          # e.g. your HQ channel
        auto_track_parents: ["123456789012345678"] # same channel = cover its threads too
        guild_id: "987654321098765432"
        providers: ["anthropic", "nous"]
        refresh_minutes: 15
```

Requires `DISCORD_BOT_TOKEN` (same bot token Hermes' own `discord`/`discord_admin`
tools use) with `SEND_MESSAGES` in the target channels.

## Use

- Runs on its own timer inside the gateway process (`refresh_minutes`, default 15;
  set to `0` to disable the background loop and only refresh manually/via cron).
- `/quota_dashboard` — refresh every tracked channel right now, from any chat.
- `hermes quota-dashboard` — same, from the CLI (handy for an external cron job
  if you'd rather not run the in-process loop).

## Known limits

- Edits the same message in place (no pin-notification spam); if that message
  gets deleted it transparently reposts and tracks the new one.
- New threads under a tracked parent are picked up on the *next* refresh cycle,
  not instantly (no push event for thread-creation here — polls
  `GET /guilds/{id}/threads/active` each cycle).
- Quota availability depends entirely on what `agent.account_usage` can fetch for
  the credential actually configured (e.g. Anthropic quota needs an OAuth
  Claude.ai Pro/Max login, not a bare API key).
- **The in-process `refresh_minutes` timer (`ctx.spawn_task`) is unreliable
  under Hermes' default `plugins.isolation: host`.** Third-party plugins run in
  a separate host subprocess there, and a spawned background task gets
  attributed to "the request being served" at spawn time — which finishes
  right after plugin load, so the loop's `ctx.*` calls silently stop working
  on its very first tick. Confirmed by direct testing: the CLI command always
  works; the in-process timer does not survive past ~60s in host-isolation
  mode. **Until that's fixed upstream, don't rely on `refresh_minutes` — run
  `hermes quota-dashboard` from an external loop instead** (cron, systemd
  timer, or a simple `while true; do hermes quota-dashboard; sleep 60; done`).
  It costs nothing but a few HTTP calls either way.
