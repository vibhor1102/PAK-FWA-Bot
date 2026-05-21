import asyncio
import logging
import sys

if sys.version_info < (3, 14):
    raise RuntimeError(
        "PAK FWA Bot must run on Python 3.14 or newer. "
        "Set PYTHON_VERSION=3.14.0 on Render or keep the repo-root .python-version file."
    )

import discord
from aiohttp import web
from discord.ext import commands

from .config import AppConfig
from .coc_service import CocService
from .database import Database
from .registry import build_feature_specs
from .settings_data import build_settings_snapshot
from .state import AppState

LOGGER = logging.getLogger(__name__)


class PakFwaBot(commands.Bot):
    """Discord bot with gated features, shared settings, and HTTP health routes."""

    state: AppState

    def __init__(self, state: AppState) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.state = state

    async def setup_hook(self) -> None:
        for spec in self.state.feature_specs:
            if spec.is_enabled(self.state.config.runtime_mode):
                self.tree.add_command(spec.command)
                LOGGER.info("Enabled feature %s.", spec.name)
            else:
                LOGGER.info(
                    "Skipped feature %s in %s mode.",
                    spec.name,
                    self.state.config.runtime_mode.value,
                )

        await sync_commands(self)

    async def on_ready(self) -> None:
        if self.user is None:
            LOGGER.info("Discord client is ready.")
            return

        LOGGER.info("Logged in as %s (%s).", self.user, self.user.id)


async def sync_commands(bot: PakFwaBot) -> None:
    guild_id = bot.state.config.discord_guild_id

    if guild_id is not None:
        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        LOGGER.info("Synced %s guild slash command(s) to guild %s.", len(synced), guild_id)
        return

    synced = await bot.tree.sync()
    LOGGER.info("Synced %s global slash command(s).", len(synced))


def create_web_app(bot: PakFwaBot) -> web.Application:
    app = web.Application()

    async def index(_request: web.Request) -> web.Response:
        return web.Response(text="PAK FWA Bot is awake.")

    async def health(_request: web.Request) -> web.Response:
        database_health = await bot.state.database.health()
        return web.json_response(
            {
                "status": "ok",
                "runtime_mode": bot.state.config.runtime_mode.value,
                "discord_ready": bot.is_ready(),
                "latency_ms": None if bot.latency is None else round(bot.latency * 1000),
                "database": database_health,
            }
        )

    async def settings(_request: web.Request) -> web.Response:
        snapshot = build_settings_snapshot(
            bot.state,
            discord_ready=bot.is_ready(),
            latency_ms=None if bot.latency is None else round(bot.latency * 1000),
        )
        return web.json_response(snapshot)

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/settings", settings)
    return app


async def start_web_server(bot: PakFwaBot, port: int) -> web.AppRunner:
    runner = web.AppRunner(create_web_app(bot))
    await runner.setup()

    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    LOGGER.info("Health server listening on port %s.", port)
    return runner


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = AppConfig.from_environment()
    database = Database(config.database_url)
    await database.connect()
    coc_service = CocService(config)

    state = AppState(
        config=config,
        database=database,
        coc_service=coc_service,
        feature_specs=build_feature_specs(),
    )

    bot = PakFwaBot(state)

    runner = await start_web_server(bot, config.port)
    try:
        await bot.start(config.discord_token)
    finally:
        await runner.cleanup()
        await coc_service.close()
        await database.close()
        await bot.close()


def main() -> None:
    asyncio.run(run())
