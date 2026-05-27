from __future__ import annotations

from typing import Any

import coc
import discord
from discord import app_commands

from ..coc_service import CocConfigurationError
from ..resolver import LinkResolutionError, LinkResolver, clash_profile_url


def build_player_command() -> app_commands.Command[Any, ..., None]:
    @app_commands.describe(player_tag="Player tag. Optional when a linked default exists.")
    @app_commands.describe(user="Resolve this user's default linked player instead of yours.")
    async def player_callback(
        interaction: discord.Interaction,
        player_tag: str | None = None,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.client is None or not hasattr(interaction.client, "state"):
            await interaction.response.send_message("Player lookup is unavailable right now.", ephemeral=False)
            return

        await interaction.response.defer(thinking=True, ephemeral=False)
        state = interaction.client.state  # type: ignore[attr-defined]
        try:
            resolved = await LinkResolver(state).resolve_player_tag(interaction, player_tag, user=user)
            client = await state.coc_service.get_client()
            player = await client.get_player(resolved.tag)
        except LinkResolutionError as exc:
            await interaction.followup.send(str(exc), ephemeral=False)
            return
        except (ValueError, CocConfigurationError) as exc:
            await interaction.followup.send(str(exc), ephemeral=False)
            return
        except coc.NotFound:
            await interaction.followup.send(f"No Clash player was found for `{player_tag or resolved.tag}`.", ephemeral=False)
            return

        embed = discord.Embed(
            title=f"{player.name} (`{player.tag}`)",
            description=f"Resolved from {resolved.source}.",
            color=discord.Color.green(),
            url=clash_profile_url("player", player.tag),
        )
        embed.add_field(name="Town Hall", value=str(player.town_hall), inline=True)
        embed.add_field(name="Trophies", value=str(player.trophies), inline=True)
        embed.add_field(name="War stars", value=str(player.war_stars), inline=True)
        embed.add_field(name="Role", value=str(player.role), inline=True)
        embed.add_field(name="Donations", value=f"{player.donations} sent / {player.received} received", inline=True)
        embed.add_field(name="War preference", value=str(getattr(player, "war_opted_in", "unknown")), inline=True)
        if player.clan:
            embed.add_field(name="Clan", value=f"{player.clan.name} (`{player.clan.tag}`)", inline=False)
        heroes = [hero for hero in player.heroes if getattr(hero, "village", "home") == "home"]
        if heroes:
            embed.add_field(
                name="Heroes",
                value=", ".join(f"{hero.name} {hero.level}" for hero in heroes[:8]),
                inline=False,
            )
        embed.set_footer(text=f"Tag source: {resolved.label}")
        await interaction.followup.send(embed=embed, ephemeral=False)

    return app_commands.Command(
        name="player",
        description="Show a Clash player summary using a tag or linked Discord user.",
        callback=player_callback,
    )
