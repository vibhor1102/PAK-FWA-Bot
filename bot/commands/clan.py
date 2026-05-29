from __future__ import annotations

from typing import Any

import coc
import discord
from discord import app_commands

from ..autocomplete import autocomplete_server_clans
from ..coc_service import CocConfigurationError
from ..resolver import LinkResolutionError, LinkResolver, clash_profile_url


def build_clan_command() -> app_commands.Command[Any, ..., None]:
    @app_commands.autocomplete(clan_tag=autocomplete_server_clans)
    @app_commands.describe(clan_tag="Clan tag or server alias. Optional when a linked default exists.")
    @app_commands.describe(user="Resolve this user's linked clan instead of yours.")
    async def clan_callback(
        interaction: discord.Interaction,
        clan_tag: str | None = None,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.client is None or not hasattr(interaction.client, "state"):
            await interaction.response.send_message("Clan lookup is unavailable right now.", ephemeral=False)
            return

        await interaction.response.defer(thinking=True, ephemeral=False)
        state = interaction.client.state  # type: ignore[attr-defined]
        try:
            resolved = await LinkResolver(state).resolve_clan_tag(interaction, clan_tag, user=user)
            client = await state.coc_service.get_client()
            clan = await client.get_clan(resolved.tag)
        except LinkResolutionError as exc:
            await interaction.followup.send(str(exc), ephemeral=False)
            return
        except (ValueError, CocConfigurationError) as exc:
            await interaction.followup.send(str(exc), ephemeral=False)
            return
        except coc.NotFound:
            await interaction.followup.send(f"No Clash clan was found for `{clan_tag or resolved.tag}`.", ephemeral=False)
            return

        embed = discord.Embed(
            title=f"{clan.name} (`{clan.tag}`)",
            description=f"Resolved from {resolved.source}.",
            color=discord.Color.blurple(),
            url=clash_profile_url("clan", clan.tag),
        )
        if getattr(clan, "badge", None) is not None and getattr(clan.badge, "url", None):
            embed.set_thumbnail(url=clan.badge.url)
        embed.add_field(name="Members", value=f"{clan.member_count}/50", inline=True)
        embed.add_field(name="Level", value=str(clan.level), inline=True)
        embed.add_field(name="Type", value=str(clan.type), inline=True)
        embed.add_field(name="War league", value=str(getattr(clan.war_league, "name", clan.war_league)), inline=True)
        embed.add_field(name="Capital league", value=str(getattr(clan.capital_league, "name", clan.capital_league)), inline=True)
        embed.add_field(name="War log", value="public" if clan.public_war_log else "private", inline=True)
        if clan.description:
            embed.add_field(name="Description", value=clan.description[:1024], inline=False)
        embed.set_footer(text=f"Tag source: {resolved.label}")
        await interaction.followup.send(embed=embed, ephemeral=False)

    return app_commands.Command(
        name="clan",
        description="Show a Clash clan summary using a tag, alias, channel, server, or user default.",
        callback=clan_callback,
    )
