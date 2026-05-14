# PAK FWA Bot

A small Python Discord bot scaffold designed for Render free-tier hosting and uptime pings from UptimeRobot.

## Architecture

- `bot/main.py` contains the Discord bot, slash-command registration, and HTTP health server.
- `bot/__main__.py` lets Render run the app with `python -m bot`.
- `requirements.txt` pins the Python dependencies.
- `runtime.txt` tells Render which Python version to use.
- `render.yaml` documents the Render web service settings.

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

After deploy, your service URL should return a simple response at `/` and JSON at `/health`.

## UptimeRobot setup

Create an HTTP(s) monitor pointed at either:

- `https://your-render-service.onrender.com/health` (recommended), or
- `https://your-render-service.onrender.com/`

A 5-minute interval is typical for Render free-tier keep-alive monitoring.

## Discord-side checklist

You said you can manage these, but make sure the following are done:

1. Invite the bot with the `applications.commands` scope so slash commands can appear.
2. Invite the bot with the `bot` scope if you need normal bot presence and future gateway features.
3. Give it enough permissions for future features. The current `/help` command does not require special channel permissions beyond being usable in the server.
4. Enable only the privileged intents you actually need. This scaffold currently uses only default, non-privileged intents.
5. If using `DISCORD_GUILD_ID`, copy the server ID from the same server where you invited the bot.

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

## Slash command registration note

When `DISCORD_GUILD_ID` is set, the bot registers commands only to that server and updates should appear quickly. When `DISCORD_GUILD_ID` is unset, the bot registers global commands, which can take longer to appear across Discord.
