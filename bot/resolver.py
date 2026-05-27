from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal

import discord


TAG_PATTERN = re.compile(r"^#?[0289PYLQGRJCUV]{3,}$", re.IGNORECASE)
TargetKind = Literal["clan", "player"]


class LinkResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedTag:
    tag: str
    source: str
    label: str


def normalize_tag(raw_tag: str) -> str:
    tag = raw_tag.strip().upper().replace("O", "0")
    tag = re.sub(r"[^0289PYLQGRJCUV#]", "", tag)
    if not tag.startswith("#"):
        tag = f"#{tag}"
    if not TAG_PATTERN.fullmatch(tag):
        raise ValueError(f"`{raw_tag}` does not look like a valid Clash tag.")
    return tag


def clash_profile_url(kind: TargetKind, tag: str) -> str:
    action = "OpenClanProfile" if kind == "clan" else "OpenPlayerProfile"
    return f"https://link.clashofclans.com/en?action={action}&tag={tag.lstrip('#')}"


class LinkResolver:
    def __init__(self, state: Any) -> None:
        self._state = state

    async def resolve_clan_tag(
        self,
        interaction: discord.Interaction,
        explicit_tag: str | None,
        *,
        user: discord.abc.User | None = None,
    ) -> ResolvedTag:
        if explicit_tag:
            try:
                normalized = normalize_tag(explicit_tag)
            except ValueError:
                if interaction.guild_id is not None:
                    linked = await self._state.database.find_server_clan(
                        guild_id=interaction.guild_id,
                        query=explicit_tag,
                    )
                    if linked is not None:
                        return ResolvedTag(
                            tag=linked["clan_tag"],
                            source="server alias",
                            label=linked.get("nickname") or linked["clan_name"],
                        )
                raise
            return ResolvedTag(tag=normalized, source="explicit tag", label=normalized)

        if interaction.guild_id is not None and interaction.channel_id is not None:
            linked = await self._state.database.find_server_clan(
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
            )
            if linked is not None:
                return ResolvedTag(
                    tag=linked["clan_tag"],
                    source="channel default",
                    label=linked.get("nickname") or linked["clan_name"],
                )

        target_user = user or interaction.user
        profile = await self._state.database.get_user_profile(target_user.id)
        if profile and profile.get("clan_tag"):
            return ResolvedTag(
                tag=profile["clan_tag"],
                source="linked user clan",
                label=profile.get("clan_name") or profile["clan_tag"],
            )

        if interaction.guild_id is not None:
            linked = await self._state.database.find_server_clan(guild_id=interaction.guild_id)
            if linked is not None:
                return ResolvedTag(
                    tag=linked["clan_tag"],
                    source="server default",
                    label=linked.get("nickname") or linked["clan_name"],
                )

        raise LinkResolutionError(
            "No clan tag was provided and I could not infer one yet. Use `/setup clan` for this server "
            "or `/link create` to link your own clan."
        )

    async def resolve_player_tag(
        self,
        interaction: discord.Interaction,
        explicit_tag: str | None,
        *,
        user: discord.abc.User | None = None,
    ) -> ResolvedTag:
        if explicit_tag:
            normalized = normalize_tag(explicit_tag)
            return ResolvedTag(tag=normalized, source="explicit tag", label=normalized)

        target_user = user or interaction.user
        linked = await self._state.database.get_default_player_link(target_user.id)
        if linked is not None:
            return ResolvedTag(
                tag=linked["player_tag"],
                source="default linked player",
                label=linked["player_name"],
            )

        profile = await self._state.database.get_user_profile(target_user.id)
        if profile and profile.get("last_player_tag"):
            return ResolvedTag(
                tag=profile["last_player_tag"],
                source="last linked player",
                label=profile["last_player_tag"],
            )

        raise LinkResolutionError(
            "No player tag was provided and I could not infer one yet. Use `/link create` to link a player."
        )
