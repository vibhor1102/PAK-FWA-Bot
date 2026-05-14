# PAK FWA Bot

A small Python Discord bot scaffold designed for Render free-tier hosting and uptime pings from UptimeRobot.

## Architecture

- `bot/main.py` contains the Discord bot, slash-command registration, and HTTP health server.
- `bot/__main__.py` lets Render run the app with `python -m bot`.
- `requirements.txt` pins the Python dependencies.
- `.python-version` tells Render to use Python 3.12.13 instead of Render's newer default Python, which currently removes the `audioop` module imported by `discord.py` 2.4.
- `render.yaml` documents the Render web service settings if you later want to use Render Blueprints.
- `.env.example` documents the environment variables you need without committing secrets.

## What is included

- `discord.py` bot runtime.
- A `/help` slash command that replies with a small placeholder embed.
- An `aiohttp` web server with:
  - `GET /` for a simple awake message.
  - `GET /health` for Render/UptimeRobot health checks.
- Render-ready commands and optional `render.yaml` blueprint.

## Required environment variables

Configure these in Render under **Web Service > Environment**:

| Variable | Required | Where to get it | Notes |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Discord Developer Portal > your application > Bot > Token | Keep this secret. Never commit it to GitHub. |
| `DISCORD_GUILD_ID` | No | Discord client with Developer Mode enabled > right-click your server > Copy Server ID | Recommended while developing because guild commands update quickly. Remove later for global commands. |
| `PORT` | No | Render sets this automatically | Only set manually for local development if needed. |

`DISCORD_CLIENT_ID` is not required by this Python architecture because `discord.py` can sync application commands through the logged-in bot token.

## Render setup

If you are configuring the Render web service manually, use:

- **Runtime:** Python
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python -m bot`
- **Health check path:** `/health`
- **Environment variable:** `PYTHON_VERSION=3.12.13` if Render does not automatically pick up `.python-version`
- **Plan:** Free is fine for this starter app.

After deploy, your service URL should return a simple response at `/` and JSON at `/health`.

### Render environment setup

1. Open your Render web service.
2. Go to **Environment**.
3. Add `DISCORD_TOKEN` with your bot token as the value.
4. Confirm the deploy log says it is using Python `3.12.13`. If it still uses Render's default Python, add `PYTHON_VERSION=3.12.13`.
5. Optional but recommended while testing: add `DISCORD_GUILD_ID` with the ID of the server where you invited the bot.
6. Save changes and redeploy/restart the service.

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
4. Enable only the privileged intents you actually need. This scaffold currently uses only default, non-privileged intents.
5. If using `DISCORD_GUILD_ID`, copy the server ID from the same server where you invited the bot.
6. If `/help` does not appear immediately, confirm the bot was invited with `applications.commands` and that `DISCORD_GUILD_ID` matches your test server.

## Local development

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

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

## Slash command registration note

When `DISCORD_GUILD_ID` is set, the bot registers commands only to that server and updates should appear quickly. When `DISCORD_GUILD_ID` is unset, the bot registers global commands, which can take longer to appear across Discord.

## Troubleshooting reminders

- A missing `DISCORD_TOKEN` will stop the app on startup; add it in Render Environment settings.
- An invalid `DISCORD_GUILD_ID` must be corrected or removed; it should contain only the numeric Discord server ID.
- If Render says `ModuleNotFoundError: No module named 'audioop'`, the service is running Python 3.13 or newer. Confirm `.python-version` is committed and/or set `PYTHON_VERSION=3.12.13` in Render.
- If Render says the port is unavailable, make sure the start command is `python -m bot`; the app reads Render's `PORT` environment variable automatically.
- If UptimeRobot reports failures, check both `/` and `/health` on the Render service URL and inspect Render logs.
