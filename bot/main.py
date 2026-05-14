import asyncio
import logging
import os
from typing import Any

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

LOGGER = logging.getLogger(__name__)

DISCORD_TOKEN_ENV = "DISCORD_TOKEN"
DISCORD_GUILD_ID_ENV = "DISCORD_GUILD_ID"
PORT_ENV = "PORT"
DEFAULT_PORT = 3000


class PakFwaBot(commands.Bot):
    """Discord.py bot with slash commands and a small HTTP health server."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self) -> None:
        self.tree.add_command(build_help_command())
        await sync_commands(self)

    async def on_ready(self) -> None:
        if self.user is None:
            LOGGER.info("Discord client is ready.")
            return

        LOGGER.info("Logged in as %s (%s).", self.user, self.user.id)


def build_help_command() -> app_commands.Command[Any, ..., None]:
    async def help_callback(interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="PAK FWA Bot Help",
            description="Thanks for trying the bot! More commands will be added here soon.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="/help",
            value="Shows this placeholder help message.",
            inline=False,
        )
        embed.set_footer(text="PAK FWA Bot")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    return app_commands.Command(
        name="help",
        description="Show help information for this bot.",
        callback=help_callback,
    )


async def sync_commands(bot: commands.Bot) -> None:
    guild_id = os.getenv(DISCORD_GUILD_ID_ENV)

    if guild_id:
        try:
            guild = discord.Object(id=int(guild_id))
        except ValueError as exc:
            raise RuntimeError(f"{DISCORD_GUILD_ID_ENV} must be a numeric Discord server ID") from exc

        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        LOGGER.info("Synced %s guild slash command(s) to guild %s.", len(synced), guild_id)
        return

    synced = await bot.tree.sync()
    LOGGER.info("Synced %s global slash command(s).", len(synced))


def create_web_app(bot: commands.Bot) -> web.Application:
    app = web.Application()

    async def index(_request: web.Request) -> web.Response:
        return web.Response(text="PAK FWA Bot is awake.")

    async def health(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "discord_ready": bot.is_ready(),
                "latency_ms": None if bot.latency is None else round(bot.latency * 1000),
            }
        )

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    return app


async def start_web_server(bot: commands.Bot, port: int) -> web.AppRunner:
    runner = web.AppRunner(create_web_app(bot))
    await runner.setup()

    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    LOGGER.info("Health server listening on port %s.", port)
    return runner


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_port() -> int:
    raw_port = os.getenv(PORT_ENV, str(DEFAULT_PORT))
    try:
        return int(raw_port)
    except ValueError as exc:
        raise RuntimeError(f"{PORT_ENV} must be a valid integer") from exc


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = get_required_env(DISCORD_TOKEN_ENV)
    port = get_port()
    bot = PakFwaBot()

    runner = await start_web_server(bot, port)
    try:
        await bot.start(token)
    finally:
        await runner.cleanup()
        await bot.close()


def main() -> None:
    asyncio.run(run())
