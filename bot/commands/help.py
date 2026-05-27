from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from ..command_mentions import command_mention


class HelpLinksView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=600)
        self.add_item(discord.ui.Button(label="FWA Stats", style=discord.ButtonStyle.link, url="https://fwastats.com/"))
        self.add_item(discord.ui.Button(label="Points", style=discord.ButtonStyle.link, url="https://points.fwafarm.com/"))
        self.add_item(discord.ui.Button(label="CC", style=discord.ButtonStyle.link, url="https://cc.fwafarm.com/"))


def build_help_embed(bot: Any) -> discord.Embed:
    embed = discord.Embed(
        title="PAK FWA Bot",
        description="Clan links, player identity, FWA war calls, proactive feeds, and linked-account autoroles.",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Start",
        value=(
            f"{command_mention(bot, '/settings')} - Open your setup hub.\n"
            f"{command_mention(bot, '/setup clan')} - Link a clan to this server or channel.\n"
            f"{command_mention(bot, '/setup player')} - Link a player account.\n"
            f"{command_mention(bot, '/setup user-clan')} - Link your clan."
        ),
        inline=False,
    )
    embed.add_field(
        name="Identity",
        value=(
            f"{command_mention(bot, '/profile')} - Show a user's linked Clash identity.\n"
            f"{command_mention(bot, '/setup player')} - Add or update a linked player.\n"
            f"{command_mention(bot, '/setup user-clan')} - Add or update a linked clan.\n"
            f"{command_mention(bot, '/link verify')} - Verify player ownership with an API token.\n"
            f"{command_mention(bot, '/link list')} / {command_mention(bot, '/link delete')} - Review or remove links."
        ),
        inline=False,
    )
    embed.add_field(
        name="Lookup",
        value=(
            f"{command_mention(bot, '/clan')} - Show a clan from tag, alias, channel, server, or user link.\n"
            f"{command_mention(bot, '/player')} - Show a player from tag or linked default account."
        ),
        inline=False,
    )
    embed.add_field(
        name="FWA",
        value=(
            f"{command_mention(bot, '/fwa')} - Post concise active-war instructions.\n"
            f"{command_mention(bot, '/setup announcements')} - Set proactive war feed channel."
        ),
        inline=False,
    )
    embed.add_field(
        name="Autorole",
        value=(
            f"{command_mention(bot, '/autorole set')} - Map Discord roles to linked clan ranks.\n"
            f"{command_mention(bot, '/autorole sync')} - Run role sync now.\n"
            f"{command_mention(bot, '/autorole list')} / {command_mention(bot, '/autorole remove')} - Review or disable role configs."
        ),
        inline=False,
    )
    embed.set_footer(text="Most commands infer tags from links and channel/server defaults.")
    return embed


def build_help_command() -> app_commands.Command[Any, ..., None]:
    async def help_callback(interaction: discord.Interaction) -> None:
        bot = interaction.client
        if bot is None:
            await interaction.response.send_message("Help is unavailable right now.", ephemeral=False)
            return

        await interaction.response.send_message(embed=build_help_embed(bot), view=HelpLinksView(), ephemeral=False)

    return app_commands.Command(
        name="help",
        description="Show help information for this bot.",
        callback=help_callback,
    )
