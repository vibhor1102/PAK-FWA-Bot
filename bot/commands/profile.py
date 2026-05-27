from __future__ import annotations

import coc
import discord
from discord import app_commands

from ..resolver import clash_profile_url
from .link import linked_player_line


def build_profile_command() -> app_commands.Command[None, ..., None]:
    @app_commands.describe(user="Discord user to inspect; defaults to you.")
    async def profile_callback(interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        target = user or interaction.user
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            database = interaction.client.state.database  # type: ignore[attr-defined]
            profile = await database.get_user_profile(target.id)
            players = await database.list_player_links(target.id)
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        embed = discord.Embed(title=f"{target.display_name}'s Clash profile", color=discord.Color.gold())
        embed.set_author(name=str(target), icon_url=target.display_avatar.url)

        if profile and profile.get("clan_tag"):
            url = clash_profile_url("clan", profile["clan_tag"])
            embed.add_field(
                name="Default clan",
                value=f"[{profile['clan_name']} (`{profile['clan_tag']}`)]({url})",
                inline=False,
            )
        else:
            embed.add_field(name="Default clan", value="No clan linked yet.", inline=False)

        if not players:
            embed.add_field(name="Players", value="No player accounts linked yet.", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        try:
            coc_client = await interaction.client.state.coc_service.get_client()  # type: ignore[attr-defined]
            fresh_players = await _fetch_players(coc_client, players)
        except Exception:
            fresh_players = {}

        lines: list[str] = []
        for player in players:
            fresh = fresh_players.get(player["player_tag"])
            if fresh is None:
                lines.append(linked_player_line(player))
                continue
            clan_name = getattr(getattr(fresh, "clan", None), "name", None)
            suffix = f" - TH{fresh.town_hall} - {clan_name}" if clan_name else f" - TH{fresh.town_hall}"
            lines.append(f"{linked_player_line(player)}{suffix}")

        embed.add_field(name=f"Players ({len(players)})", value="\n".join(lines[:15]), inline=False)
        if len(lines) > 15:
            embed.set_footer(text=f"Showing 15 of {len(lines)} linked players.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    return app_commands.Command(
        name="profile",
        description="Show a Discord user's linked Clash identity.",
        callback=profile_callback,
    )


async def _fetch_players(coc_client: coc.Client, players: list[dict[str, object]]) -> dict[str, coc.Player]:
    found: dict[str, coc.Player] = {}
    for player in players[:15]:
        tag = str(player["player_tag"])
        try:
            found[tag] = await coc_client.get_player(tag)
        except Exception:
            continue
    return found
