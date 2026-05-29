from __future__ import annotations

import coc
import discord
from discord import app_commands

from ..clan_dashboard import PAGE_LABELS, ensure_dashboard_message, normalize_page
from ..coc_service import CocConfigurationError
from ..resolver import clash_profile_url, normalize_tag


def _require_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild_id is not None


def _user_names(user: discord.abc.User) -> tuple[str, str]:
    return user.name, user.display_name


def build_setup_group() -> app_commands.Group:
    group = app_commands.Group(name="setup", description="Configure server-level Clash links.")

    @group.command(name="player", description="Link a Clash player to yourself or another Discord user.")
    @app_commands.describe(player_tag="Player tag to link.")
    @app_commands.describe(user="User to link on behalf of; managers only for other users.")
    @app_commands.describe(is_default="Set this player as the user's default account.")
    async def setup_player(
        interaction: discord.Interaction,
        player_tag: str,
        user: discord.Member | None = None,
        is_default: bool = False,
    ) -> None:
        target = user or interaction.user
        if user is not None and user.id != interaction.user.id:
            permissions = interaction.permissions
            if permissions is None or not permissions.manage_guild:
                await interaction.response.send_message("Only server managers can link accounts for another user.", ephemeral=True)
                return

        await interaction.response.defer(thinking=True, ephemeral=True)
        username, display_name = _user_names(target)
        try:
            client = await interaction.client.state.coc_service.get_client()  # type: ignore[attr-defined]
            player = await client.get_player(normalize_tag(player_tag))
            linked = await interaction.client.state.database.link_player(  # type: ignore[attr-defined]
                user_id=target.id,
                username=username,
                display_name=display_name,
                player_tag=player.tag,
                player_name=player.name,
                linked_by=interaction.user.id,
                is_default=is_default,
            )
        except (ValueError, CocConfigurationError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except coc.NotFound:
            await interaction.followup.send(f"No Clash player was found for `{player_tag}`.", ephemeral=True)
            return

        default_note = " default" if linked["is_default"] else ""
        await interaction.followup.send(
            f"Linked{default_note} player **{player.name}** (`{player.tag}`) to {target.mention}.",
            ephemeral=True,
        )

    @group.command(name="user-clan", description="Set your linked clan, or set one for another user.")
    @app_commands.describe(clan_tag="Clan tag to link as the user's clan.")
    @app_commands.describe(user="User to link on behalf of; managers only for other users.")
    async def setup_user_clan(
        interaction: discord.Interaction,
        clan_tag: str,
        user: discord.Member | None = None,
    ) -> None:
        target = user or interaction.user
        if user is not None and user.id != interaction.user.id:
            permissions = interaction.permissions
            if permissions is None or not permissions.manage_guild:
                await interaction.response.send_message("Only server managers can link accounts for another user.", ephemeral=True)
                return

        await interaction.response.defer(thinking=True, ephemeral=True)
        username, display_name = _user_names(target)
        try:
            client = await interaction.client.state.coc_service.get_client()  # type: ignore[attr-defined]
            clan = await client.get_clan(normalize_tag(clan_tag))
            await interaction.client.state.database.upsert_user_clan(  # type: ignore[attr-defined]
                user_id=target.id,
                username=username,
                display_name=display_name,
                clan_tag=clan.tag,
                clan_name=clan.name,
            )
        except (ValueError, CocConfigurationError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except coc.NotFound:
            await interaction.followup.send(f"No Clash clan was found for `{clan_tag}`.", ephemeral=True)
            return

        await interaction.followup.send(f"Linked clan **{clan.name}** (`{clan.tag}`) to {target.mention}.", ephemeral=True)

    @group.command(name="clan", description="Link a Clash clan to this server or channel.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(clan_tag="The clan tag to link, with or without #.")
    @app_commands.describe(channel="Optional channel where this clan should be the default.")
    @app_commands.describe(alias="Optional short name users can type instead of the tag.")
    @app_commands.describe(nickname="Optional display name for this clan inside the bot.")
    async def setup_clan(
        interaction: discord.Interaction,
        clan_tag: str,
        channel: discord.TextChannel | discord.Thread | None = None,
        alias: str | None = None,
        nickname: str | None = None,
    ) -> None:
        if not _require_guild(interaction):
            await interaction.response.send_message("Server setup can only be used inside a Discord server.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            normalized = normalize_tag(clan_tag)
            client = await interaction.client.state.coc_service.get_client()  # type: ignore[attr-defined]
            clan = await client.get_clan(normalized)
            linked = await interaction.client.state.database.upsert_server_clan(  # type: ignore[attr-defined]
                guild_id=interaction.guild_id,
                clan_tag=clan.tag,
                clan_name=clan.name,
                alias=alias.strip() if alias else None,
                nickname=nickname.strip() if nickname else None,
                channel_id=channel.id if channel else None,
            )
        except (ValueError, CocConfigurationError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except coc.NotFound:
            await interaction.followup.send(f"No Clash clan was found for `{clan_tag}`.", ephemeral=True)
            return

        location = f" for {channel.mention}" if channel else ""
        display = linked.get("nickname") or clan.name
        embed = discord.Embed(
            title="Clan linked",
            description=f"{display} (`{clan.tag}`) is now linked to this server{location}.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Alias", value=linked.get("alias") or "Not set", inline=True)
        embed.add_field(name="Default channel", value=channel.mention if channel else "Server default", inline=True)
        embed.add_field(name="Profile", value=f"[Open in Clash]({clash_profile_url('clan', clan.tag)})", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="list", description="Show clans linked to this Discord server.")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_list(interaction: discord.Interaction) -> None:
        if not _require_guild(interaction):
            await interaction.response.send_message("Server setup can only be used inside a Discord server.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            clans = await interaction.client.state.database.list_server_clans(interaction.guild_id)  # type: ignore[attr-defined]
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        embed = discord.Embed(title="Linked clans", color=discord.Color.teal())
        if not clans:
            embed.description = "No server clans are linked yet. Use `/setup clan` to add one."
        else:
            lines: list[str] = []
            for clan in clans:
                channel = f"<#{clan['channel_id']}>" if clan.get("channel_id") else "server default"
                alias = f" alias `{clan['alias']}`" if clan.get("alias") else ""
                nickname = f" as **{clan['nickname']}**" if clan.get("nickname") else ""
                lines.append(f"- **{clan['clan_name']}** (`{clan['clan_tag']}`){nickname}, {channel}{alias}")
            embed.description = "\n".join(lines)
        try:
            announcements = await interaction.client.state.database.get_announcement_channel(interaction.guild_id)  # type: ignore[attr-defined]
        except RuntimeError:
            announcements = None
        if announcements:
            enabled = [
                name
                for name, flag in (
                    ("war found", announcements["war_found"]),
                    ("FWA ready", announcements["fwa_ready"]),
                    ("war ended", announcements["war_ended"]),
                )
                if flag
            ]
            embed.add_field(
                name="Announcement channel",
                value=f"<#{announcements['channel_id']}> ({', '.join(enabled) or 'none'})",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="remove", description="Remove a linked server clan or channel default.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(clan_tag="Clan tag to unlink from the server.")
    @app_commands.describe(channel="Channel default to clear instead of removing the clan.")
    async def setup_remove(
        interaction: discord.Interaction,
        clan_tag: str | None = None,
        channel: discord.TextChannel | discord.Thread | None = None,
    ) -> None:
        if not _require_guild(interaction):
            await interaction.response.send_message("Server setup can only be used inside a Discord server.", ephemeral=True)
            return
        if not clan_tag and channel is None:
            await interaction.response.send_message("Provide a clan tag or a channel to remove.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            removed = await interaction.client.state.database.remove_server_clan(  # type: ignore[attr-defined]
                guild_id=interaction.guild_id,
                clan_tag=normalize_tag(clan_tag) if clan_tag else None,
                channel_id=channel.id if channel else None,
            )
        except (ValueError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        if removed is None:
            await interaction.followup.send("No matching server clan link was found.", ephemeral=True)
            return

        target = channel.mention if channel else f"{removed['clan_name']} (`{removed['clan_tag']}`)"
        await interaction.followup.send(f"Removed the linked clan setting for {target}.", ephemeral=True)

    @group.command(name="announcements", description="Set the channel for proactive war announcements.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="Channel where the bot should post proactive war/FWA announcements.")
    @app_commands.describe(war_found="Post when the official Clash API finds a new active war.")
    @app_commands.describe(fwa_ready="Post copy-ready FWA instructions when external data is ready.")
    @app_commands.describe(war_ended="Reserve war-ended announcements for the background monitor.")
    async def setup_announcements(
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread,
        war_found: bool = True,
        fwa_ready: bool = True,
        war_ended: bool = True,
    ) -> None:
        if not _require_guild(interaction):
            await interaction.response.send_message("Server setup can only be used inside a Discord server.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            linked = await interaction.client.state.database.upsert_announcement_channel(  # type: ignore[attr-defined]
                guild_id=interaction.guild_id,
                channel_id=channel.id,
                war_found=war_found,
                fwa_ready=fwa_ready,
                war_ended=war_ended,
            )
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        enabled = [
            name
            for name, flag in (
                ("war found", linked["war_found"]),
                ("FWA ready", linked["fwa_ready"]),
                ("war ended", linked["war_ended"]),
            )
            if flag
        ]
        await interaction.followup.send(
            f"Proactive announcements will post in {channel.mention}. Enabled: {', '.join(enabled) or 'none'}.",
            ephemeral=True,
        )

    @group.command(name="dashboard", description="Create or update a persistent clan dashboard message.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="Channel where the persistent dashboard message should live.")
    @app_commands.describe(default_clan="Default clan tag or linked alias.")
    @app_commands.describe(additional_clans="Optional comma-separated clan tags or linked aliases.")
    @app_commands.describe(default_page=f"Default page: {', '.join(PAGE_LABELS)}.")
    @app_commands.describe(reset_minutes="Minutes before public controls return to the default page.")
    @app_commands.describe(show_public_controls="Show dropdowns/buttons on the persistent message.")
    async def setup_dashboard(
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread,
        default_clan: str,
        additional_clans: str | None = None,
        default_page: str = "overview",
        reset_minutes: int = 20,
        show_public_controls: bool = True,
    ) -> None:
        if not _require_guild(interaction):
            await interaction.response.send_message("Dashboard setup can only be used inside a Discord server.", ephemeral=True)
            return
        if reset_minutes < 1:
            await interaction.response.send_message("reset_minutes must be at least 1.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            client = await interaction.client.state.coc_service.get_client()  # type: ignore[attr-defined]
            clan_tags = await _resolve_dashboard_tags(
                interaction,
                default_clan=default_clan,
                additional_clans=additional_clans,
            )
            clans = []
            for tag in clan_tags:
                clans.append(await client.get_clan(tag))
            dashboard = await interaction.client.state.database.upsert_clan_dashboard(  # type: ignore[attr-defined]
                guild_id=interaction.guild_id,
                channel_id=channel.id,
                message_id=None,
                default_clan_tag=clans[0].tag,
                default_page=normalize_page(default_page),
                selected_clan_tags=[clan.tag for clan in clans],
                reset_minutes=reset_minutes,
                show_public_controls=show_public_controls,
            )
            await ensure_dashboard_message(interaction.client, dashboard)  # type: ignore[arg-type]
            dashboard = await interaction.client.state.database.get_clan_dashboard_by_channel(  # type: ignore[attr-defined]
                guild_id=interaction.guild_id,
                channel_id=channel.id,
            ) or dashboard
        except coc.NotFound:
            await interaction.followup.send("One of those clans could not be found by the Clash API.", ephemeral=True)
            return
        except (ValueError, CocConfigurationError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        await interaction.followup.send(
            (
                f"Dashboard is active in {channel.mention} with {len(dashboard['selected_clan_tags'])} clan(s). "
                f"Message ID: `{dashboard.get('message_id') or 'pending'}`."
            ),
            ephemeral=True,
        )

    @group.command(name="dashboard-remove", description="Disable a persistent clan dashboard.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="Dashboard channel to disable. Defaults to this channel.")
    async def setup_dashboard_remove(
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread | None = None,
    ) -> None:
        if not _require_guild(interaction):
            await interaction.response.send_message("Dashboard setup can only be used inside a Discord server.", ephemeral=True)
            return

        target_channel_id = channel.id if channel else interaction.channel_id
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            removed = await interaction.client.state.database.disable_clan_dashboard(  # type: ignore[attr-defined]
                guild_id=interaction.guild_id,
                channel_id=target_channel_id,
            )
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if removed is None:
            await interaction.followup.send("No active dashboard was found for that channel.", ephemeral=True)
            return
        await interaction.followup.send(f"Disabled the clan dashboard in <#{removed['channel_id']}>.", ephemeral=True)

    return group


async def _resolve_dashboard_tags(
    interaction: discord.Interaction,
    *,
    default_clan: str,
    additional_clans: str | None,
) -> list[str]:
    values = [default_clan]
    if additional_clans:
        values.extend(item.strip() for item in additional_clans.split(",") if item.strip())
    tags: list[str] = []
    for value in values:
        try:
            tag = normalize_tag(value)
        except ValueError:
            linked = await interaction.client.state.database.find_server_clan(  # type: ignore[attr-defined]
                guild_id=interaction.guild_id,
                query=value,
            )
            if linked is None:
                raise ValueError(f"`{value}` is not a valid clan tag or linked clan alias.")
            tag = linked["clan_tag"]
        if tag not in tags:
            tags.append(tag)
    return tags
