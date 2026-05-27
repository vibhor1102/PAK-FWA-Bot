from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from ..command_mentions import command_mention


class HelpPageButton(discord.ui.Button):
    def __init__(self, view: "HelpDashboardView", label: str, page_index: int, *, row: int) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self._view_ref = view
        self._page_index = page_index

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.switch_page(interaction, self._page_index)


class HelpRefreshButton(discord.ui.Button):
    def __init__(self, view: "HelpDashboardView", *, row: int) -> None:
        super().__init__(
            label="Refresh",
            style=discord.ButtonStyle.success,
            row=row,
        )
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.refresh(interaction)


class HelpDashboardView(discord.ui.View):
    def __init__(self, bot: Any, *, page_index: int = 0) -> None:
        super().__init__(timeout=600)
        self._bot = bot
        self._page_titles = ["Overview", "Commands", "Linking", "Clan Report", "Sources"]
        self._page_index = max(0, min(page_index, len(self._page_titles) - 1))
        self._build_buttons()

    def _build_buttons(self) -> None:
        for index, title in enumerate(self._page_titles):
            row = index // 5
            button = HelpPageButton(self, title, index, row=row)
            if index == self._page_index:
                button.style = discord.ButtonStyle.primary
            self.add_item(button)

        refresh_row = 1
        self.add_item(HelpRefreshButton(self, row=refresh_row))

        self.add_item(
            discord.ui.Button(
                label="FWA Stats",
                style=discord.ButtonStyle.link,
                url="https://fwastats.com/",
                row=refresh_row,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Points",
                style=discord.ButtonStyle.link,
                url="https://points.fwafarm.com/",
                row=refresh_row,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="CC",
                style=discord.ButtonStyle.link,
                url="https://cc.fwafarm.com/",
                row=refresh_row,
            )
        )

    async def switch_page(self, interaction: discord.Interaction, page_index: int) -> None:
        new_view = HelpDashboardView(self._bot, page_index=page_index)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

    async def refresh(self, interaction: discord.Interaction) -> None:
        new_view = HelpDashboardView(self._bot, page_index=self._page_index)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="PAK FWA Bot Help",
            description=(
                "Use the buttons below to move between command groups. The link buttons open the live FWA resources the bot uses."
            ),
            color=discord.Color.gold(),
        )
        embed.set_author(name=f"Page {self._page_index + 1}/{len(self._page_titles)}")
        embed.set_footer(text="Commands are expanding quickly, so this panel is built to scale.")

        if self._page_index == 0:
            embed.add_field(
                name="Quick Start",
                value=(
                    "`/clanreport` pulls a deep clan intelligence report.\n"
                    f"{command_mention(self._bot, '/setup clan')} and {command_mention(self._bot, '/link create')} teach the bot your default tags.\n"
                    f"{command_mention(self._bot, '/settings')} shows the live bot/runtime dashboard.\n"
                    f"{command_mention(self._bot, '/help')} opens this panel."
                ),
                inline=False,
            )
            embed.add_field(
                name="What this bot focuses on",
                value=(
                    "FWA clan intelligence, war scoring, member detail, source agreement checks, and human-readable war history."
                ),
                inline=False,
            )
        elif self._page_index == 1:
            embed.add_field(
                name="/settings",
                value=f"{command_mention(self._bot, '/settings')} - Live runtime, database, Discord, Clash of Clans, and feature-state dashboard.",
                inline=False,
            )
            embed.add_field(
                name="/help",
                value=f"{command_mention(self._bot, '/help')} - This interactive help panel with section buttons and source links.",
                inline=False,
            )
            embed.add_field(
                name="Lookup and linking",
                value=(
                    f"{command_mention(self._bot, '/clan')} - Clan summary from tag, alias, or default.\n"
                    f"{command_mention(self._bot, '/player')} - Player summary from tag or linked user.\n"
                    f"{command_mention(self._bot, '/fwa')} - Active-war FWA instructions and safety checks.\n"
                    f"{command_mention(self._bot, '/profile')} - Linked Clash identity for a Discord user."
                ),
                inline=False,
            )
        elif self._page_index == 2:
            embed.add_field(
                name="/setup clan",
                value=f"{command_mention(self._bot, '/setup clan')} - Managers link a clan to the server or a specific channel so commands can infer clan tags.",
                inline=False,
            )
            embed.add_field(
                name="/link create, /link verify, /link list, /link delete",
                value=(
                    f"{command_mention(self._bot, '/link create')} - Add player and clan links.\n"
                    f"{command_mention(self._bot, '/link verify')} - Mark player ownership verified.\n"
                    f"{command_mention(self._bot, '/link list')} - Review linked accounts.\n"
                    f"{command_mention(self._bot, '/link delete')} - Remove a linked player or clan."
                ),
                inline=False,
            )
            embed.add_field(
                name="/profile",
                value=f"{command_mention(self._bot, '/profile')} - Shows a user's linked clan and players with verified/default markers.",
                inline=False,
            )
        elif self._page_index == 3:
            embed.add_field(
                name="/fwa",
                value=(
                    f"{command_mention(self._bot, '/fwa')} - Checks the official active war, waits through the "
                    "3-hour FWA Stats safety margin, posts public diagnostics when data should exist, and gives "
                    "the caller a private copy-ready instruction block when data is good."
                ),
                inline=False,
            )
            embed.add_field(
                name="/clanreport",
                value=(
                    f"{command_mention(self._bot, '/clanreport')} - Generates a detailed clan intelligence report. The clan tag is optional when a linked server, "
                    "channel, or user clan can be inferred. It includes FWA Stats as the primary source, "
                    "points.fwafarm as the secondary cross-check, and war focus modes for `ongoing` or `recent`."
                ),
                inline=False,
            )
            embed.add_field(
                name="Report output",
                value=(
                    "The command returns a public message plus a text attachment packed with clan, war, roster, and source-agreement details."
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="FWA Stats",
                value="Primary public data source used for clan summaries, members, and war history exports.",
                inline=False,
            )
            embed.add_field(
                name="points.fwafarm",
                value="Secondary points and point-history cross-check source.",
                inline=False,
            )
            embed.add_field(
                name="cc.fwafarm",
                value="Best-effort clan status and flag lookup layer when the page is reachable.",
                inline=False,
            )

        return embed


def build_help_command() -> app_commands.Command[Any, ..., None]:
    async def help_callback(interaction: discord.Interaction) -> None:
        bot = interaction.client
        if bot is None:
            await interaction.response.send_message(
                "Help is unavailable right now.",
                ephemeral=False,
            )
            return

        view = HelpDashboardView(bot)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=False)

    return app_commands.Command(
        name="help",
        description="Show help information for this bot.",
        callback=help_callback,
    )
