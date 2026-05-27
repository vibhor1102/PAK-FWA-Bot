from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import coc
import discord

from .resolver import normalize_tag

if TYPE_CHECKING:
    from .main import PakFwaBot


LOGGER = logging.getLogger(__name__)

RANK_WEIGHTS = {
    "member": 1,
    "elder": 2,
    "admin": 2,
    "co_leader": 3,
    "coleader": 3,
    "co-leader": 3,
    "leader": 4,
}

ROLE_COLUMNS = {
    "member": "member_role_id",
    "elder": "elder_role_id",
    "co_leader": "co_leader_role_id",
    "leader": "leader_role_id",
}


@dataclass(slots=True)
class AutoroleSyncSummary:
    configs_checked: int = 0
    players_checked: int = 0
    observations_saved: int = 0
    roles_added: int = 0
    roles_removed: int = 0
    skipped_members: int = 0
    errors: list[str] = field(default_factory=list)


async def run_autorole_loop(bot: PakFwaBot) -> None:
    interval = bot.state.config.autorole_sync_interval_seconds
    LOGGER.info("Autorole sync loop started with %ss interval.", interval)
    while not bot.is_closed():
        try:
            await sync_autoroles(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Autorole sync failed.")
        await asyncio.sleep(interval)


async def sync_autoroles(
    bot: PakFwaBot,
    *,
    guild_id: int | str | None = None,
    clan_tag: str | None = None,
) -> AutoroleSyncSummary:
    summary = AutoroleSyncSummary()
    if not bot.state.database.connected or not bot.state.coc_service.configured:
        summary.errors.append("Autorole needs database and Clash API access.")
        return summary

    configs = await bot.state.database.list_autorole_configs(guild_id=guild_id, clan_tag=clan_tag)
    await bot.state.database.cleanup_autorole_observations(retention_days=bot.state.config.autorole_retention_days)
    client = await bot.state.coc_service.get_client()
    for config in configs:
        summary.configs_checked += 1
        guild = bot.get_guild(int(config["guild_id"]))
        if guild is None:
            summary.errors.append(f"Guild {config['guild_id']} is not available.")
            continue
        if guild.me is None:
            summary.errors.append(f"I am not ready in {guild.name} yet.")
            continue
        if not guild.me.guild_permissions.manage_roles:
            summary.errors.append(f"I need Manage Roles in {guild.name} before autorole can run.")
            continue
        await _sync_config(bot, client, guild, config, summary)
    return summary


async def _sync_config(
    bot: PakFwaBot,
    client: coc.Client,
    guild: discord.Guild,
    config: dict[str, Any],
    summary: AutoroleSyncSummary,
) -> None:
    clan_tag = normalize_tag(config["clan_tag"])
    subjects = await bot.state.database.list_autorole_subjects(
        guild_id=guild.id,
        clan_tag=clan_tag,
        retention_days=bot.state.config.autorole_retention_days,
    )
    current_effective: dict[str, dict[str, Any]] = {}
    for subject in subjects:
        summary.players_checked += 1
        try:
            player = await client.get_player(subject["player_tag"])
        except coc.NotFound:
            continue
        except Exception as exc:
            summary.errors.append(f"Could not check {subject['player_tag']}: {exc}")
            continue

        player_clan_tag = getattr(getattr(player, "clan", None), "tag", None)
        if not player_clan_tag or normalize_tag(player_clan_tag) != clan_tag:
            continue

        clan_role = normalize_clan_role(getattr(player, "role", "member"))
        rank_weight = rank_weight_for_role(clan_role)
        await bot.state.database.insert_autorole_observation(
            guild_id=guild.id,
            clan_tag=clan_tag,
            player_tag=player.tag,
            user_id=subject["user_id"],
            player_name=player.name,
            clan_role=clan_role,
            rank_weight=rank_weight,
        )
        summary.observations_saved += 1
        current_effective[player.tag] = {
            "player_tag": player.tag,
            "user_id": subject["user_id"],
            "player_name": player.name,
            "clan_role": clan_role,
            "rank_weight": rank_weight,
        }

    if config.get("grace_enabled"):
        effective = await bot.state.database.get_autorole_effective_roles(
            guild_id=guild.id,
            clan_tag=clan_tag,
            retention_days=bot.state.config.autorole_retention_days,
        )
    else:
        effective = list(current_effective.values())

    desired_by_user = desired_roles_by_user(config, effective)
    role_ids = configured_role_ids(config)
    user_ids = set(desired_by_user)
    user_ids.update(str(row["user_id"]) for row in subjects)
    for user_id in sorted(user_ids):
        await _apply_user_roles(
            bot,
            guild,
            config,
            user_id=user_id,
            desired_role_ids=desired_by_user.get(user_id, set()),
            managed_role_ids=role_ids,
            summary=summary,
        )

    await bot.state.database.update_autorole_synced_at(guild_id=guild.id, clan_tag=clan_tag)


async def _apply_user_roles(
    bot: PakFwaBot,
    guild: discord.Guild,
    config: dict[str, Any],
    *,
    user_id: str,
    desired_role_ids: set[str],
    managed_role_ids: set[str],
    summary: AutoroleSyncSummary,
) -> None:
    try:
        member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        summary.skipped_members += 1
        return

    current_role_ids = {str(role.id) for role in member.roles}
    manageable_role_ids = {
        role_id
        for role_id in managed_role_ids
        if (role := guild.get_role(int(role_id))) is not None and guild.me is not None and role < guild.me.top_role
    }
    blocked_role_ids = managed_role_ids - manageable_role_ids
    for role_id in sorted(blocked_role_ids):
        role = guild.get_role(int(role_id))
        if role is not None:
            summary.errors.append(f"Move my bot role above {role.name} before I can manage it.")

    desired_role_ids = desired_role_ids & manageable_role_ids
    to_add = desired_role_ids - current_role_ids
    to_remove = (manageable_role_ids - desired_role_ids) & current_role_ids

    for role_id in sorted(to_add):
        role = guild.get_role(int(role_id))
        if role is None:
            summary.errors.append(f"Role {role_id} is not available.")
            continue
        try:
            await member.add_roles(role, reason="PAK Originals autorole sync")
            summary.roles_added += 1
            await bot.state.database.mark_autorole_assignment(
                guild_id=guild.id,
                clan_tag=config["clan_tag"],
                user_id=user_id,
                role_id=role_id,
                desired=True,
            )
        except (discord.Forbidden, discord.HTTPException):
            summary.errors.append(f"Could not add {role.name} to {member.display_name}.")

    for role_id in sorted(to_remove):
        role = guild.get_role(int(role_id))
        if role is None:
            continue
        try:
            await member.remove_roles(role, reason="PAK Originals autorole sync")
            summary.roles_removed += 1
            await bot.state.database.mark_autorole_assignment(
                guild_id=guild.id,
                clan_tag=config["clan_tag"],
                user_id=user_id,
                role_id=role_id,
                desired=False,
            )
        except (discord.Forbidden, discord.HTTPException):
            summary.errors.append(f"Could not remove {role.name} from {member.display_name}.")


def desired_roles_by_user(config: dict[str, Any], effective_rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    desired: dict[str, set[str]] = {}
    general_role_id = config.get("general_role_id")
    for row in effective_rows:
        user_roles = desired.setdefault(str(row["user_id"]), set())
        if general_role_id:
            user_roles.add(str(general_role_id))
        rank = canonical_rank(row.get("clan_role"))
        role_column = ROLE_COLUMNS.get(rank)
        role_id = config.get(role_column) if role_column else None
        if role_id:
            user_roles.add(str(role_id))
    return desired


def configured_role_ids(config: dict[str, Any]) -> set[str]:
    role_ids = {
        config.get("general_role_id"),
        config.get("leader_role_id"),
        config.get("co_leader_role_id"),
        config.get("elder_role_id"),
        config.get("member_role_id"),
    }
    return {str(role_id) for role_id in role_ids if role_id}


def normalize_clan_role(value: Any) -> str:
    raw = str(getattr(value, "name", value) or "member").strip().lower()
    raw = raw.replace(" ", "_").replace("-", "_")
    if raw in {"admin", "elder"}:
        return "elder"
    if raw in {"coleader", "co_leader"}:
        return "co_leader"
    if raw == "leader":
        return "leader"
    return "member"


def canonical_rank(value: Any) -> str:
    return normalize_clan_role(value)


def rank_weight_for_role(value: Any) -> int:
    return RANK_WEIGHTS.get(normalize_clan_role(value), 1)
