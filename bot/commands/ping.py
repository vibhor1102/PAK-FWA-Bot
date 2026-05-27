from __future__ import annotations

from typing import Any

import discord
from discord import app_commands


def build_ping_command() -> app_commands.Command[Any, ..., None]:
    async def ping_callback(interaction: discord.Interaction) -> None:
        latency = interaction.client.latency if interaction.client is not None else None
        latency_ms = "unknown" if latency is None else f"{round(latency * 1000)} ms"
        await interaction.response.send_message(f"Pong. Discord latency: `{latency_ms}`.", ephemeral=True)

    return app_commands.Command(
        name="ping",
        description="Check whether the bot is responsive.",
        callback=ping_callback,
    )
