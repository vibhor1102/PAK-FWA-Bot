from __future__ import annotations

import coc
import discord
from discord import app_commands

from ..autorole import sync_autoroles
from ..coc_service import CocConfigurationError
from ..resolver import normalize_tag


def build_autorole_group() -> app_commands.Group:
    group = app_commands.Group(name="autorole", description="Configure linked-player clan roles.")

    @group.command(name="set", description="Set clan roles for linked player autorole sync.")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(clan_tag="Clan tag this autorole config belongs to.")
    @app_commands.describe(general_role="Role given to any linked member seen in this clan.")
    @app_commands.describe(leader_role="Role for linked leader accounts.")
    @app_commands.describe(co_leader_role="Role for linked co-leader accounts.")
    @app_commands.describe(elder_role="Role for linked elder accounts.")
    @app_commands.describe(member_role="Role for linked member accounts.")
    @app_commands.describe(grace_enabled="Keep highest observed rank from the retention window.")
    async def autorole_set(
        interaction: discord.Interaction,
        clan_tag: str,
        general_role: discord.Role | None = None,
        leader_role: discord.Role | None = None,
        co_leader_role: discord.Role | None = None,
        elder_role: discord.Role | None = None,
        member_role: discord.Role | None = None,
        grace_enabled: bool = True,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Autorole setup can only be used inside a server.", ephemeral=True)
            return
        permission_message = _bot_role_setup_issue(interaction)
        if permission_message is not None:
            await interaction.response.send_message(permission_message, ephemeral=True)
            return
        if not any((general_role, leader_role, co_leader_role, elder_role, member_role)):
            await interaction.response.send_message("Choose at least one Discord role to manage.", ephemeral=True)
            return
        hierarchy_issue = _role_hierarchy_issue(interaction, general_role, leader_role, co_leader_role, elder_role, member_role)
        if hierarchy_issue is not None:
            await interaction.response.send_message(hierarchy_issue, ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            normalized = normalize_tag(clan_tag)
            client = await interaction.client.state.coc_service.get_client()  # type: ignore[attr-defined]
            clan = await client.get_clan(normalized)
            config = await interaction.client.state.database.upsert_autorole_config(  # type: ignore[attr-defined]
                guild_id=interaction.guild_id,
                clan_tag=clan.tag,
                clan_name=clan.name,
                general_role_id=general_role.id if general_role else None,
                leader_role_id=leader_role.id if leader_role else None,
                co_leader_role_id=co_leader_role.id if co_leader_role else None,
                elder_role_id=elder_role.id if elder_role else None,
                member_role_id=member_role.id if member_role else None,
                grace_enabled=grace_enabled,
            )
        except (ValueError, CocConfigurationError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except coc.NotFound:
            await interaction.followup.send(f"No Clash clan was found for `{clan_tag}`.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Autorole configured",
            description=f"Linked player roles are now configured for **{config['clan_name']}** (`{config['clan_tag']}`).",
            color=discord.Color.green(),
        )
        embed.add_field(name="General", value=_role_value(general_role), inline=True)
        embed.add_field(name="Leader", value=_role_value(leader_role), inline=True)
        embed.add_field(name="Co-leader", value=_role_value(co_leader_role), inline=True)
        embed.add_field(name="Elder", value=_role_value(elder_role), inline=True)
        embed.add_field(name="Member", value=_role_value(member_role), inline=True)
        embed.add_field(name="Grace", value="3-day highest-rank memory" if grace_enabled else "Off", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="list", description="Show autorole configs for this server.")
    @app_commands.default_permissions(manage_roles=True)
    async def autorole_list(interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Autorole setup can only be used inside a server.", ephemeral=True)
            return
        permission_message = _bot_role_setup_issue(interaction)
        if permission_message is not None:
            await interaction.response.send_message(permission_message, ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            configs = await interaction.client.state.database.list_autorole_configs(guild_id=interaction.guild_id)  # type: ignore[attr-defined]
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        embed = discord.Embed(title="Autorole configs", color=discord.Color.blurple())
        if not configs:
            embed.description = "No autorole configs yet. Use `/autorole set` to add one."
        else:
            lines = []
            for config in configs:
                roles = [
                    f"general {_mention(config.get('general_role_id'))}",
                    f"leader {_mention(config.get('leader_role_id'))}",
                    f"co-leader {_mention(config.get('co_leader_role_id'))}",
                    f"elder {_mention(config.get('elder_role_id'))}",
                    f"member {_mention(config.get('member_role_id'))}",
                ]
                grace = "grace on" if config["grace_enabled"] else "grace off"
                lines.append(f"- **{config['clan_name']}** (`{config['clan_tag']}`): {', '.join(roles)}; {grace}")
            embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="sync", description="Run autorole sync now.")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(clan_tag="Optional clan tag to sync; defaults to all autorole configs in this server.")
    async def autorole_sync(interaction: discord.Interaction, clan_tag: str | None = None) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Autorole sync can only be used inside a server.", ephemeral=True)
            return
        permission_message = _bot_role_setup_issue(interaction)
        if permission_message is not None:
            await interaction.response.send_message(permission_message, ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            summary = await sync_autoroles(
                interaction.client,  # type: ignore[arg-type]
                guild_id=interaction.guild_id,
                clan_tag=normalize_tag(clan_tag) if clan_tag else None,
            )
        except (ValueError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        text = (
            f"Autorole sync complete. Checked {summary.players_checked} linked player account(s); "
            f"added {summary.roles_added}, removed {summary.roles_removed}."
        )
        if summary.skipped_members:
            text += f" Skipped {summary.skipped_members} member(s) I could not access."
        if summary.errors:
            text += "\n" + "\n".join(f"- {error}" for error in summary.errors[:5])
        await interaction.followup.send(text, ephemeral=True)

    @group.command(name="remove", description="Disable autorole config for a clan.")
    @app_commands.default_permissions(manage_roles=True)
    async def autorole_remove(interaction: discord.Interaction, clan_tag: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Autorole setup can only be used inside a server.", ephemeral=True)
            return
        permission_message = _bot_role_setup_issue(interaction)
        if permission_message is not None:
            await interaction.response.send_message(permission_message, ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            removed = await interaction.client.state.database.remove_autorole_config(  # type: ignore[attr-defined]
                guild_id=interaction.guild_id,
                clan_tag=normalize_tag(clan_tag),
            )
        except (ValueError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if removed is None:
            await interaction.followup.send("No matching autorole config was found.", ephemeral=True)
            return
        await interaction.followup.send(f"Disabled autorole for **{removed['clan_name']}** (`{removed['clan_tag']}`).", ephemeral=True)

    return group


def _role_value(role: discord.Role | None) -> str:
    return role.mention if role else "Not managed"


def _mention(role_id: object | None) -> str:
    return f"<@&{role_id}>" if role_id else "off"


def _bot_role_setup_issue(interaction: discord.Interaction) -> str | None:
    guild = interaction.guild
    if guild is None or guild.me is None:
        return "I need to be fully loaded in this server before autorole can run. Try again in a moment."
    if not guild.me.guild_permissions.manage_roles:
        return (
            "I need the **Manage Roles** permission before I can manage autoroles. "
            "Please grant it to my bot role, then run this again."
        )
    return None


def _role_hierarchy_issue(interaction: discord.Interaction, *roles: discord.Role | None) -> str | None:
    guild = interaction.guild
    if guild is None or guild.me is None:
        return None
    bot_top_role = guild.me.top_role
    blocked = [role.mention for role in roles if role is not None and role >= bot_top_role]
    if not blocked:
        return None
    return (
        "I can only manage roles below my highest role. "
        f"Move my bot role above: {', '.join(blocked)}."
    )
