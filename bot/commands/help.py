from __future__ import annotations

from typing import Any

import discord
from discord import app_commands


def build_help_command() -> app_commands.Command[Any, ..., None]:
    async def help_callback(interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="PAK FWA Bot Help",
            description="More commands will be added here as the bot grows.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="/settings",
            value="Shows live runtime, database, and feature-state details.",
            inline=False,
        )
        embed.add_field(
            name="/clanreport",
            value="Pulls a detailed Clash of Clans clan, war, and member report.",
            inline=False,
        )
        embed.set_footer(text="PAK FWA Bot")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    return app_commands.Command(
        name="help",
        description="Show help information for this bot.",
        callback=help_callback,
    )
