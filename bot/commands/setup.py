from __future__ import annotations

import coc
import discord
from discord import app_commands

from ..coc_service import CocConfigurationError
from ..resolver import clash_profile_url, normalize_tag


def _require_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild_id is not None


def build_setup_group() -> app_commands.Group:
    group = app_commands.Group(name="setup", description="Configure server-level Clash links.")

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

    return group
