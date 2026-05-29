from __future__ import annotations

import coc
import discord
from discord import app_commands

from ..autocomplete import autocomplete_server_clans
from ..coc_service import CocConfigurationError
from ..resolver import clash_profile_url, normalize_tag


def _user_names(user: discord.abc.User) -> tuple[str, str]:
    return user.name, user.display_name


def build_link_group() -> app_commands.Group:
    group = app_commands.Group(name="link", description="Link Clash players and clans to Discord users.")

    @group.command(name="create", description="Link a player or clan to a Discord user.")
    @app_commands.autocomplete(clan_tag=autocomplete_server_clans)
    @app_commands.describe(player_tag="Player tag to link.")
    @app_commands.describe(clan_tag="Clan tag to link as the user's default clan.")
    @app_commands.describe(user="User to link on behalf of; managers only for other users.")
    @app_commands.describe(is_default="Set this player as the user's default account.")
    async def link_create(
        interaction: discord.Interaction,
        player_tag: str | None = None,
        clan_tag: str | None = None,
        user: discord.Member | None = None,
        is_default: bool = False,
    ) -> None:
        if not player_tag and not clan_tag:
            await interaction.response.send_message("Provide a player tag, clan tag, or both.", ephemeral=True)
            return

        target = user or interaction.user
        if user is not None and user.id != interaction.user.id:
            permissions = interaction.permissions
            if permissions is None or not permissions.manage_guild:
                await interaction.response.send_message("Only server managers can link accounts for another user.", ephemeral=True)
                return

        await interaction.response.defer(thinking=True, ephemeral=True)
        username, display_name = _user_names(target)
        client = interaction.client.state.coc_service  # type: ignore[attr-defined]
        database = interaction.client.state.database  # type: ignore[attr-defined]
        lines: list[str] = []

        try:
            coc_client = await client.get_client()
            if player_tag:
                player = await coc_client.get_player(normalize_tag(player_tag))
                linked = await database.link_player(
                    user_id=target.id,
                    username=username,
                    display_name=display_name,
                    player_tag=player.tag,
                    player_name=player.name,
                    linked_by=interaction.user.id,
                    is_default=is_default,
                )
                default_note = " default" if linked["is_default"] else ""
                lines.append(f"Linked{default_note} player **{player.name}** (`{player.tag}`) to {target.mention}.")
            if clan_tag:
                clan = await coc_client.get_clan(normalize_tag(clan_tag))
                await database.upsert_user_clan(
                    user_id=target.id,
                    username=username,
                    display_name=display_name,
                    clan_tag=clan.tag,
                    clan_name=clan.name,
                )
                lines.append(f"Linked clan **{clan.name}** (`{clan.tag}`) to {target.mention}.")
        except (ValueError, CocConfigurationError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except coc.NotFound:
            await interaction.followup.send("One of those Clash tags was not found.", ephemeral=True)
            return

        embed = discord.Embed(title="Link created", description="\n".join(lines), color=discord.Color.green())
        embed.set_footer(text="Unverified links work now; use /link verify when you want ownership marked.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="verify", description="Verify ownership of a linked player with the in-game API token.")
    @app_commands.describe(player_tag="Player tag to verify.")
    @app_commands.describe(token="The in-game API token from Clash settings.")
    async def link_verify(interaction: discord.Interaction, player_tag: str, token: str) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            normalized = normalize_tag(player_tag)
            coc_client = await interaction.client.state.coc_service.get_client()  # type: ignore[attr-defined]
            player = await coc_client.get_player(normalized)
            result = await coc_client.verify_player_token(normalized, token)
            if not result:
                await interaction.followup.send("That token did not verify. Copy a fresh API token from Clash and try again.", ephemeral=True)
                return
            username, display_name = _user_names(interaction.user)
            await interaction.client.state.database.verify_player_link(  # type: ignore[attr-defined]
                user_id=interaction.user.id,
                username=username,
                display_name=display_name,
                player_tag=player.tag,
                player_name=player.name,
            )
        except (ValueError, CocConfigurationError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except coc.NotFound:
            await interaction.followup.send(f"No Clash player was found for `{player_tag}`.", ephemeral=True)
            return

        await interaction.followup.send(f"Verified **{player.name}** (`{player.tag}`) for {interaction.user.mention}.", ephemeral=True)

    @group.command(name="list", description="Show linked players and clan for a Discord user.")
    @app_commands.describe(user="User to inspect; defaults to you.")
    async def link_list(interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        target = user or interaction.user
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            profile = await interaction.client.state.database.get_user_profile(target.id)  # type: ignore[attr-defined]
            players = await interaction.client.state.database.list_player_links(target.id)  # type: ignore[attr-defined]
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        embed = discord.Embed(title=f"Linked accounts for {target.display_name}", color=discord.Color.blurple())
        if profile and profile.get("clan_tag"):
            embed.add_field(
                name="Linked clan",
                value=f"**{profile['clan_name']}** (`{profile['clan_tag']}`)",
                inline=False,
            )
        else:
            embed.add_field(name="Linked clan", value="None yet.", inline=False)

        if players:
            lines = []
            for player in players:
                markers = []
                if player["is_default"]:
                    markers.append("default")
                markers.append("verified" if player["verified"] else "unverified")
                lines.append(f"- **{player['player_name']}** (`{player['player_tag']}`) - {', '.join(markers)}")
            embed.add_field(name=f"Players ({len(players)})", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Players", value="No player accounts linked yet.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="delete", description="Remove a linked player or clan from your Discord identity.")
    @app_commands.autocomplete(clan_tag=autocomplete_server_clans)
    @app_commands.describe(player_tag="Player tag to unlink.")
    @app_commands.describe(clan_tag="Clan tag to unlink from your profile.")
    async def link_delete(
        interaction: discord.Interaction,
        player_tag: str | None = None,
        clan_tag: str | None = None,
    ) -> None:
        if not player_tag and not clan_tag:
            await interaction.response.send_message("Provide a player tag or clan tag to unlink.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        removed: list[str] = []
        try:
            if player_tag:
                player = await interaction.client.state.database.delete_player_link(  # type: ignore[attr-defined]
                    user_id=interaction.user.id,
                    player_tag=normalize_tag(player_tag),
                )
                if player:
                    removed.append(f"player **{player['player_name']}** (`{player['player_tag']}`)")
            if clan_tag:
                did_remove = await interaction.client.state.database.remove_user_clan(  # type: ignore[attr-defined]
                    interaction.user.id,
                    normalize_tag(clan_tag),
                )
                if did_remove:
                    removed.append(f"clan `{normalize_tag(clan_tag)}`")
        except (ValueError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        if not removed:
            await interaction.followup.send("No matching linked account was found.", ephemeral=True)
            return
        await interaction.followup.send(f"Unlinked {', '.join(removed)}.", ephemeral=True)

    return group


def linked_player_line(player: dict[str, object]) -> str:
    marker = "verified" if player["verified"] else "unverified"
    default = " default" if player["is_default"] else ""
    url = clash_profile_url("player", str(player["player_tag"]))
    return f"[{player['player_name']} (`{player['player_tag']}`)]({url}) - {marker}{default}"
