# PAK FWA Bot

A configurable Python Discord bot scaffold with local `.env` support, Render production env vars, a Postgres layer, and feature gates for local/testing/production-only behavior.

## Architecture

- `bot/config.py` loads runtime mode, local `.env`, and environment variables.
- `bot/state.py` keeps shared app state in one place.
- `bot/features.py` and `bot/registry.py` gate commands by deployment target.
- `bot/database.py` wraps PostgreSQL access.
- `bot/coc_service.py` manages Clash of Clans API access for clan reporting.
- `bot/commands/` holds slash-command modules.
- `bot/resolver.py` resolves explicit, channel, server, and user-linked Clash tags for commands.
- `bot/settings_data.py` powers the safe `/settings` HTTP route and the slash settings hub's system page.
- `bot/main.py` wires everything together.
- `render.yaml` defines the Render web service and environment variables.
- `.env.example` documents local and deployment variables without secrets.

## What is included

- `discord.py` bot runtime.
- A `/help` slash command.
- A `/settings` slash setup hub and matching safe `/settings` HTTP route.
- `/setup`, `/link`, `/profile`, `/player`, `/clan`, `/fwa`, and `/autorole` commands for Discord-first clan/player linking, lookup, active-war FWA instructions, and linked-account role sync.
- Proactive war monitoring for linked clans, with `/setup announcements` choosing where the bot posts war/FWA updates.
- Public FWA database lookups from `points.fwafarm.com`, public FWA Stats JSON exports from `fwastats.com`, and best-effort clan status lookups from `cc.fwafarm.com`.
- An `aiohttp` web server with:
  - `GET /` for a simple awake message.
  - `GET /health` for Render/UptimeRobot health checks.
  - `GET /settings` for a safe JSON config snapshot.
- Render-ready commands and optional `render.yaml` blueprint.

## Required environment variables

Configure these locally in `.env` and in Render under **Web Service > Environment**:

| Variable | Required | Where to get it | Notes |
| --- | --- | --- | --- |
| `BOT_RUNTIME` | No | Local choice or Render env var | Use `local`, `testing`, or `production` to control feature gates. |
| `DISCORD_TOKEN` | Yes | Discord Developer Portal > your application > Bot > Token | Keep this secret. Never commit it to GitHub. |
| `DISCORD_GUILD_ID` | No | Discord client with Developer Mode enabled > right-click your server > Copy Server ID | Recommended while developing because guild commands update quickly. Remove later for global commands. |
| `DATABASE_URL` | No now, yes later | Supabase or local PostgreSQL | The app is ready for Postgres-backed features even before they are all added. |
| `PORT` | No | Render sets this automatically | Only set manually for local development if needed. |
| `COC_EMAIL` | Optional | Supercell developer portal email | Used by `coc.py` to create and refresh API keys automatically. |
| `COC_PASSWORD` | Optional | Supercell developer portal password | Used with `COC_EMAIL` for automatic key management. |
| `COC_TOKENS` | Optional | Existing Clash of Clans API tokens | Comma-separated fallback if you already created tokens manually. |
| `COC_KEY_COUNT` | Optional | Your preference | How many API keys `coc.py` may manage, up to the library limit. |
| `COC_KEY_NAME` / `COC_KEY_NAMES` | Optional | Your preference | Label used for keys that `coc.py` creates. The code accepts both names. |
| `COC_KEY_SCOPES` | Optional | Your preference | Stored and shown in settings for visibility; the current `coc.py` client uses its own built-in key handling. |
| `COC_THROTTLE_LIMIT` | Optional | Your preference | Passed through to `coc.py` to control request throttling. |
| `WAR_MONITOR_INTERVAL_SECONDS` | Optional | Your preference | How often the background monitor checks linked clans through the official Clash API. Defaults to `300`. |
| `FWA_EXTERNAL_LOOKUP_START_HOURS` | Optional | Your preference | Earliest hour after CoC reports a match when the monitor may query FWA Stats and points. Defaults to `2`. |
| `FWA_EXTERNAL_LOOKUP_END_HOURS` | Optional | Your preference | Latest hour after CoC reports a match when the monitor may query FWA Stats and points. Defaults to `4`. |
| `MANUAL_EXTERNAL_LOOKUP_TAG_COOLDOWN_SECONDS` | Optional | Your preference | Same-clan cooldown for manual `/fwa` external checks. Defaults to `180`. |
| `AUTOMATIC_EXTERNAL_LOOKUP_TAG_COOLDOWN_SECONDS` | Optional | Your preference | Same-clan cooldown for background FWA/points checks. Defaults to `900`. |
| `EXTERNAL_LOOKUP_USER_BURST_PER_MINUTE` | Optional | Your preference | Per-user manual FWA/points burst limit. Defaults to `3`. |
| `EXTERNAL_LOOKUP_GUILD_BURST_PER_MINUTE` | Optional | Your preference | Per-server manual FWA/points burst limit. Defaults to `12`. |
| `AUTOROLE_SYNC_INTERVAL_SECONDS` | Optional | Your preference | How often linked-player autoroles are synced in the background. Defaults to `1800`. |
| `AUTOROLE_RETENTION_DAYS` | Optional | Your preference | Highest observed clan rank retention window for autorole grace. Defaults to `3`. |

`DISCORD_CLIENT_ID` is not required by this Python architecture because `discord.py` can sync application commands through the logged-in bot token.

## Attribution

Clan/player linking UX is inspired by [ClashPerk](https://github.com/clashperk/clashperk), an MIT-licensed Clash of Clans Discord bot. See `NOTICE.md` for the repository-wide attribution. This bot remains a Python implementation and does not copy ClashPerk source files.

## Render setup

If you are configuring the Render web service manually, use:

- **Runtime:** Python
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python -m bot`
- **Health check path:** `/health`
- **Plan:** Free is fine for this starter app.

After deploy, your service URL should return a simple response at `/`, JSON at `/health`, and a safe config snapshot at `/settings`.

### Render environment setup

1. Open your Render web service.
2. Go to **Environment**.
3. Add `BOT_RUNTIME=production`.
4. Add `DISCORD_TOKEN` with your bot token as the value.
5. Optional but recommended while testing: add `DISCORD_GUILD_ID` with the ID of the server where you invited the bot.
6. Add `DATABASE_URL` with your Supabase production pooler connection string.
7. Add `COC_EMAIL` and `COC_PASSWORD` from your Supercell developer portal account for Clash API-backed commands. `coc.py` can create the API keys for you automatically once those credentials are present. Use `COC_TOKENS` only if you are using pre-created tokens, and `COC_KEY_NAME` / `COC_KEY_NAMES` to label the generated keys.
8. Save changes and redeploy/restart the service.

Do not add your token to `.env.example`, `README.md`, or any committed file.

## UptimeRobot setup

Create an HTTP(s) monitor pointed at either:

- `https://your-render-service.onrender.com/health` (recommended), or
- `https://your-render-service.onrender.com/`

A 5-minute interval is typical for Render free-tier keep-alive monitoring. If Render still spins down occasionally, the next UptimeRobot ping should wake the service again.

## Discord-side checklist

You said you can manage these, but make sure the following are done:

1. Invite the bot with the `applications.commands` scope so slash commands can appear.
2. Invite the bot with the `bot` scope if you need normal bot presence and future gateway features.
3. Give it enough permissions for future features. The current `/help` command does not require special channel permissions beyond being usable in the server.
4. For `/autorole`, give the bot Manage Roles and keep its top role above the roles it manages. Server Members Intent is helpful for member caching, but the bot does not require it to start.
5. If using `DISCORD_GUILD_ID`, copy the server ID from the same server where you invited the bot.
6. If `/help` does not appear immediately, confirm the bot was invited with `applications.commands` and that `DISCORD_GUILD_ID` matches your test server.
7. If Clash API-backed commands say Clash of Clans is not configured, make sure `COC_EMAIL` and `COC_PASSWORD` are present locally and in Render, or provide `COC_TOKENS` if you are using pre-created tokens.
8. Use `/setup clan` to set a server or channel default clan, then `/setup announcements` to choose where proactive war updates should post.
9. Use `/link create` and `/link verify` for player/user defaults. `/clan`, `/player`, `/profile`, and `/fwa` can then reuse those defaults.

## Proactive war monitor

The bot polls linked server clans in the background once `DATABASE_URL` and Clash API credentials are configured. The official Clash of Clans API is the source of truth for active wars and war start times. FWA Stats and `points.fwafarm.com` are only queried during the configured early-war window after CoC confirms the match, so normal background checks do not continuously hit community endpoints.

Use `/setup announcements` to set the channel for proactive posts. The monitor announces newly detected active wars, posts copy-ready FWA instructions when FWA Stats plus points data are available inside the lookup window, and records war snapshots for later command use. `/fwa` reuses the stored snapshot for the current war before attempting a guarded live refresh. Manual refreshes allow short human bursts but apply same-clan, per-user, and per-server limits to protect community endpoints.

## Autorole

Use `/autorole set` per clan to map Discord roles for any clan member plus leader, co-leader, elder, and member ranks. Linked player accounts are checked separately, so one Discord user can receive roles from multiple linked accounts. When grace is enabled for a clan, the bot keeps the highest observed rank from the retention window before removing or demoting roles; by default that window is 3 days. Use `/autorole sync` to run it immediately, or let the background sync handle it. The bot fetches linked Discord members directly during sync, so the privileged Members Intent is optional rather than startup-critical.

## Local development

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

   Use Python 3.14.0 so your local environment matches `.python-version` and Render.

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in your values:

   ```bash
   cp .env.example .env
   ```

4. Load the environment and start the bot:

   ```bash
   set -a
   source .env
   set +a
   python -m bot
   ```

5. In another terminal, verify the local health endpoint:

   ```bash
   curl http://localhost:3000/health
   ```

6. Check the live settings snapshot:

   ```bash
   curl http://localhost:3000/settings
   ```

## Slash command registration note

When `DISCORD_GUILD_ID` is set, the bot registers commands only to that server and updates should appear quickly. When `DISCORD_GUILD_ID` is unset, the bot registers global commands, which can take longer to appear across Discord.

## Feature gating

`BOT_RUNTIME` controls whether a feature should behave as local, testing, or production. New features can declare a visibility with `FeatureVisibility` and get skipped automatically in modes they do not support.

## Troubleshooting reminders

- A missing `DISCORD_TOKEN` will stop the app on startup; add it in Render Environment settings.
- An invalid `DISCORD_GUILD_ID` must be corrected or removed; it should contain only the numeric Discord server ID.
- If Clash API-backed commands fail with a configuration error, confirm the COC environment variables are set in the same environment that starts the bot.
- If the CC lookup shows `blocked_by_cloudflare`, the points scrape still works, but that source is refusing automated access from the current network.
- If Render says the port is unavailable, make sure the start command is `python -m bot`; the app reads Render's `PORT` environment variable automatically.
- If UptimeRobot reports failures, check both `/` and `/health` on the Render service URL and inspect Render logs.
