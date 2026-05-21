from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from ..settings_data import build_settings_snapshot


def build_settings_command() -> app_commands.Command[Any, ..., None]:
    async def settings_callback(interaction: discord.Interaction) -> None:
        bot = interaction.client
        if bot is None or not hasattr(bot, "state"):
            await interaction.response.send_message(
                "Settings are unavailable right now.",
                ephemeral=True,
            )
            return

        snapshot = build_settings_snapshot(
            bot.state,
            discord_ready=bot.is_ready(),
            latency_ms=None if bot.latency is None else round(bot.latency * 1000),
        )

        embed = discord.Embed(
            title=str(snapshot["title"]),
            description="Live configuration summary for this deployment.",
            color=discord.Color.green(),
        )

        for section in snapshot["sections"]:
            lines = [f"**{label}:** {value}" for label, value in section["items"]]
            embed.add_field(
                name=section["title"],
                value="\n".join(lines),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    return app_commands.Command(
        name="settings",
        description="Show live runtime settings for this bot.",
        callback=settings_callback,
    )
