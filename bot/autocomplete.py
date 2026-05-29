from __future__ import annotations

from typing import Any

import discord
from discord import app_commands


async def autocomplete_server_clans(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    clans = await _load_server_clans(interaction)
    return _choices_from_clans(clans, current)


async def autocomplete_csv_server_clans(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    prefix, query = _split_csv_current(current)
    clans = await _load_server_clans(interaction)
    choices = _choices_from_clans(clans, query)
    if not prefix:
        return choices
    return [
        app_commands.Choice(name=choice.name, value=f"{prefix}{choice.value}")
        for choice in choices
    ]


async def autocomplete_autorole_clans(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None or not hasattr(interaction.client, "state"):
        return []

    try:
        configs = await interaction.client.state.database.list_autorole_configs(  # type: ignore[attr-defined]
            guild_id=interaction.guild_id,
        )
    except RuntimeError:
        configs = []
    if configs:
        return _choices_from_clans(
            [
                {
                    "clan_name": config["clan_name"],
                    "clan_tag": config["clan_tag"],
                    "alias": None,
                    "nickname": None,
                }
                for config in configs
            ],
            current,
        )
    return await autocomplete_server_clans(interaction, current)


async def _load_server_clans(interaction: discord.Interaction) -> list[dict[str, Any]]:
    if interaction.guild_id is None or not hasattr(interaction.client, "state"):
        return []
    try:
        return await interaction.client.state.database.list_server_clans(interaction.guild_id)  # type: ignore[attr-defined]
    except RuntimeError:
        return []


def _choices_from_clans(clans: list[dict[str, Any]], current: str) -> list[app_commands.Choice[str]]:
    query = current.lower().strip()
    choices: list[app_commands.Choice[str]] = []
    for clan in clans:
        labels = [
            str(clan.get("nickname") or ""),
            str(clan.get("alias") or ""),
            str(clan.get("clan_name") or ""),
            str(clan.get("clan_tag") or ""),
        ]
        searchable = " ".join(labels).lower()
        if query and query not in searchable:
            continue
        name_bits = [str(clan["clan_name"]), str(clan["clan_tag"])]
        if clan.get("alias"):
            name_bits.append(f"alias {clan['alias']}")
        choices.append(app_commands.Choice(name=" - ".join(name_bits)[:100], value=str(clan["clan_tag"])))
        if len(choices) >= 25:
            break
    return choices


def _split_csv_current(current: str) -> tuple[str, str]:
    if "," not in current:
        return "", current
    prefix, query = current.rsplit(",", 1)
    return f"{prefix.strip()}, ", query.strip()
