from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands

from ..command_mentions import command_mention
from ..settings_data import build_settings_snapshot


@dataclass(slots=True)
class SettingsHubData:
    profile: dict[str, Any] | None
    players: list[dict[str, Any]]
    server_clans: list[dict[str, Any]]
    announcements: dict[str, Any] | None
    dashboards: list[dict[str, Any]]
    autoroles: list[dict[str, Any]]
    runtime: dict[str, object]


class SettingsPageButton(discord.ui.Button):
    def __init__(self, view: "SettingsHubView", label: str, page: str, *, row: int) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self._view_ref = view
        self._page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.switch_page(interaction, self._page)


class SettingsRefreshButton(discord.ui.Button):
    def __init__(self, view: "SettingsHubView", *, row: int) -> None:
        super().__init__(label="Refresh", style=discord.ButtonStyle.success, row=row)
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.refresh(interaction)


class ClanSelect(discord.ui.Select):
    def __init__(self, view: "SettingsHubView") -> None:
        options = [
            discord.SelectOption(
                label=str(clan.get("nickname") or clan["clan_name"])[:100],
                value=str(index),
                description=str(clan["clan_tag"])[:100],
            )
            for index, clan in enumerate(view.data.server_clans[:25])
        ]
        super().__init__(placeholder="Select linked clan", min_values=1, max_values=1, options=options, row=2)
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self._view_ref.selected_clan_index = int(self.values[0])
        await interaction.response.edit_message(embed=self._view_ref.build_embed(), view=self._view_ref)


class AutoroleSelect(discord.ui.Select):
    def __init__(self, view: "SettingsHubView") -> None:
        options = [
            discord.SelectOption(
                label=str(config["clan_name"])[:100],
                value=str(index),
                description=str(config["clan_tag"])[:100],
            )
            for index, config in enumerate(view.data.autoroles[:25])
        ]
        super().__init__(placeholder="Select autorole clan", min_values=1, max_values=1, options=options, row=2)
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self._view_ref.selected_autorole_index = int(self.values[0])
        await interaction.response.edit_message(embed=self._view_ref.build_embed(), view=self._view_ref)


class SettingsHubView(discord.ui.View):
    def __init__(
        self,
        bot: Any,
        owner: discord.abc.User,
        data: SettingsHubData,
        pages: list[tuple[str, str]],
        *,
        page: str = "user",
        selected_clan_index: int = 0,
        selected_autorole_index: int = 0,
    ) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.owner = owner
        self.data = data
        self.pages = pages
        self.page = page if any(key == page for key, _ in pages) else "user"
        self.selected_clan_index = selected_clan_index
        self.selected_autorole_index = selected_autorole_index
        self._build_items()

    def _build_items(self) -> None:
        for index, (key, label) in enumerate(self.pages):
            button = SettingsPageButton(self, label, key, row=0)
            if key == self.page:
                button.style = discord.ButtonStyle.primary
            self.add_item(button)
        self.add_item(SettingsRefreshButton(self, row=1))
        if self.page == "clans" and self.data.server_clans:
            self.add_item(ClanSelect(self))
        if self.page == "autoroles" and self.data.autoroles:
            self.add_item(AutoroleSelect(self))

    async def switch_page(self, interaction: discord.Interaction, page: str) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("Open your own settings panel to use these controls.", ephemeral=True)
            return
        data = await load_settings_data(self.bot, interaction)
        pages = available_pages(interaction)
        new_view = SettingsHubView(self.bot, interaction.user, data, pages, page=page)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

    async def refresh(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("Open your own settings panel to refresh it.", ephemeral=True)
            return
        data = await load_settings_data(self.bot, interaction)
        pages = available_pages(interaction)
        new_view = SettingsHubView(
            self.bot,
            interaction.user,
            data,
            pages,
            page=self.page,
            selected_clan_index=self.selected_clan_index,
            selected_autorole_index=self.selected_autorole_index,
        )
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Settings",
            description="One place to manage personal links, server defaults, feeds, and autoroles.",
            color=discord.Color.teal(),
        )
        if self.page == "user":
            self._user_page(embed)
        elif self.page == "clans":
            self._clans_page(embed)
        elif self.page == "feeds":
            self._feeds_page(embed)
        elif self.page == "autoroles":
            self._autoroles_page(embed)
        else:
            self._system_page(embed)
        return embed

    def _user_page(self, embed: discord.Embed) -> None:
        default_player = next((player for player in self.data.players if player.get("is_default")), None)
        if default_player is None and self.data.players:
            default_player = self.data.players[0]
        player_summary = (
            f"Default **{default_player['player_name']}** (`{default_player['player_tag']}`), "
            f"{len(self.data.players)} total"
            if default_player
            else "No linked players"
        )
        clan_summary = (
            f"**{self.data.profile['clan_name']}** (`{self.data.profile['clan_tag']}`)"
            if self.data.profile and self.data.profile.get("clan_tag")
            else "No linked clan"
        )
        embed.add_field(name="Player accounts", value=player_summary, inline=False)
        embed.add_field(name="User clan", value=clan_summary, inline=False)
        embed.add_field(
            name="Manage",
            value=(
                f"{command_mention(self.bot, '/setup player')} - setup player accounts\n"
                f"{command_mention(self.bot, '/setup user-clan')} - setup your clan\n"
                f"{command_mention(self.bot, '/link list')} - view all linked accounts"
            ),
            inline=False,
        )

    def _clans_page(self, embed: discord.Embed) -> None:
        embed.description = "Manage server-linked clans used for defaults, autocomplete, and FWA resolution."
        if not self.data.server_clans:
            embed.add_field(name="Linked clans", value="No clans linked yet.", inline=False)
        else:
            index = min(self.selected_clan_index, len(self.data.server_clans) - 1)
            clan = self.data.server_clans[index]
            channel = f"<#{clan['channel_id']}>" if clan.get("channel_id") else "server default"
            embed.add_field(
                name=f"{clan['clan_name']} ({clan['clan_tag']})",
                value=(
                    f"Default: {channel}\n"
                    f"Alias: `{clan['alias']}`" if clan.get("alias") else f"Default: {channel}\nAlias: not set"
                ),
                inline=False,
            )
            embed.set_footer(text=f"{len(self.data.server_clans)} linked clan(s). Use the dropdown to inspect another.")
        embed.add_field(
            name="Manage",
            value=(
                f"{command_mention(self.bot, '/setup clan')} - link or update a server clan\n"
                f"{command_mention(self.bot, '/setup list')} - detailed linked clan list\n"
                f"{command_mention(self.bot, '/setup remove')} - remove a clan or channel mapping"
            ),
            inline=False,
        )

    def _feeds_page(self, embed: discord.Embed) -> None:
        if self.data.announcements:
            enabled = [
                label
                for label, flag in (
                    ("war found", self.data.announcements["war_found"]),
                    ("FWA ready", self.data.announcements["fwa_ready"]),
                    ("war ended", self.data.announcements["war_ended"]),
                )
                if flag
            ]
            summary = f"<#{self.data.announcements['channel_id']}> - {', '.join(enabled) or 'no events'}"
        else:
            summary = "No feed channel set"
        embed.add_field(name="War feed", value=summary, inline=False)
        if self.data.dashboards:
            lines = [
                f"<#{dashboard['channel_id']}> - {dashboard['default_clan_tag']} / {dashboard['default_page']}"
                for dashboard in self.data.dashboards[:8]
            ]
            dashboard_summary = "\n".join(lines)
        else:
            dashboard_summary = "No persistent clan dashboards set"
        embed.add_field(name="Clan dashboards", value=dashboard_summary, inline=False)
        embed.add_field(
            name="Manage",
            value=(
                f"{command_mention(self.bot, '/setup announcements')} - setup proactive war feed channel\n"
                f"{command_mention(self.bot, '/setup dashboard')} - setup persistent clan dashboard\n"
                f"{command_mention(self.bot, '/setup dashboard-remove')} - disable a dashboard"
            ),
            inline=False,
        )

    def _autoroles_page(self, embed: discord.Embed) -> None:
        if not self.data.autoroles:
            embed.add_field(name="Autoroles", value="No autorole configs yet.", inline=False)
        else:
            index = min(self.selected_autorole_index, len(self.data.autoroles) - 1)
            config = self.data.autoroles[index]
            roles = [
                f"General: {_mention(config.get('general_role_id'))}",
                f"Leader: {_mention(config.get('leader_role_id'))}",
                f"Co-leader: {_mention(config.get('co_leader_role_id'))}",
                f"Elder: {_mention(config.get('elder_role_id'))}",
                f"Member: {_mention(config.get('member_role_id'))}",
            ]
            embed.add_field(
                name=f"{config['clan_name']} ({config['clan_tag']})",
                value="\n".join(roles) + f"\nGrace: {'on' if config['grace_enabled'] else 'off'}",
                inline=False,
            )
            embed.set_footer(text=f"{len(self.data.autoroles)} autorole config(s). Use the dropdown to inspect another.")
        embed.add_field(
            name="Manage",
            value=(
                f"{command_mention(self.bot, '/autorole set')} - setup clan roles\n"
                f"{command_mention(self.bot, '/autorole sync')} - run role sync now\n"
                f"{command_mention(self.bot, '/autorole remove')} - disable autorole for a clan"
            ),
            inline=False,
        )

    def _system_page(self, embed: discord.Embed) -> None:
        snapshot = self.data.runtime
        lines = []
        for section in snapshot.get("sections", []):
            items = section.get("items", [])
            preview = ", ".join(f"{label}: {value}" for label, value in items[:2])
            lines.append(f"**{section['title']}** - {preview or 'no values'}")
        embed.add_field(name="Runtime", value="\n".join(lines[:8]) or "No runtime data.", inline=False)


async def load_settings_data(bot: Any, interaction: discord.Interaction) -> SettingsHubData:
    database = bot.state.database
    profile = None
    players: list[dict[str, Any]] = []
    server_clans: list[dict[str, Any]] = []
    announcements = None
    dashboards: list[dict[str, Any]] = []
    autoroles: list[dict[str, Any]] = []
    counts = await database.count_linking_rows()
    try:
        profile = await database.get_user_profile(interaction.user.id)
        players = await database.list_player_links(interaction.user.id)
        if interaction.guild_id is not None:
            if can_manage_server(interaction):
                server_clans = await database.list_server_clans(interaction.guild_id)
                announcements = await database.get_announcement_channel(interaction.guild_id)
                dashboards = await database.list_clan_dashboards(guild_id=interaction.guild_id)
            if can_manage_roles(interaction):
                autoroles = await database.list_autorole_configs(guild_id=interaction.guild_id)
    except RuntimeError:
        pass
    runtime = build_settings_snapshot(
        bot.state,
        discord_ready=bot.is_ready(),
        latency_ms=None if bot.latency is None else round(bot.latency * 1000),
        linking_counts=counts,
    )
    return SettingsHubData(profile, players, server_clans, announcements, dashboards, autoroles, runtime)


def available_pages(interaction: discord.Interaction) -> list[tuple[str, str]]:
    pages = [("user", "My Setup")]
    if can_manage_server(interaction):
        pages.extend([("clans", "Clans"), ("feeds", "Feeds")])
    if can_manage_roles(interaction):
        pages.append(("autoroles", "Autoroles"))
    if can_manage_server(interaction):
        pages.append(("system", "System"))
    return pages


def can_manage_server(interaction: discord.Interaction) -> bool:
    permissions = interaction.permissions
    return bool(permissions and permissions.manage_guild)


def can_manage_roles(interaction: discord.Interaction) -> bool:
    permissions = interaction.permissions
    return bool(permissions and permissions.manage_roles)


def _mention(role_id: object | None) -> str:
    return f"<@&{role_id}>" if role_id else "off"


def build_settings_command() -> app_commands.Command[Any, ..., None]:
    async def settings_callback(interaction: discord.Interaction) -> None:
        bot = interaction.client
        if bot is None or not hasattr(bot, "state"):
            await interaction.response.send_message("Settings are unavailable right now.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        data = await load_settings_data(bot, interaction)
        view = SettingsHubView(bot, interaction.user, data, available_pages(interaction))
        await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)

    return app_commands.Command(
        name="settings",
        description="Open your setup and server settings hub.",
        callback=settings_callback,
    )
