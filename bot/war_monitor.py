from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import coc
import discord

from .commands.fwa import (
    FwaEvent,
    _announcement_message,
    _event_from_record,
    _hours_since_match,
    _select_matching_fwa_war,
    _timestamp_to_datetime,
    _war_is_active,
)
from .resolver import normalize_tag

if TYPE_CHECKING:
    from .main import PakFwaBot


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class MonitoredWar:
    clan_tag: str
    clan_name: str
    opponent_tag: str | None
    opponent_name: str | None
    state: str
    war_key: str
    preparation_start: datetime | None
    battle_start: datetime | None
    battle_end: datetime | None
    team_size: int | None
    event_message: str | None = None
    event_kind: str | None = None
    classification: str | None = None
    external_checked_at: datetime | None = None


async def run_war_monitor(bot: PakFwaBot) -> None:
    interval = bot.state.config.war_monitor_interval_seconds
    LOGGER.info("War monitor started with %ss interval.", interval)

    while not bot.is_closed():
        try:
            await poll_once(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("War monitor poll failed.")

        await asyncio.sleep(interval)


async def poll_once(bot: PakFwaBot) -> None:
    if not bot.state.database.connected:
        return
    if not bot.state.coc_service.configured:
        return

    rows = await bot.state.database.list_monitored_clans()
    if not rows:
        return

    by_tag: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            by_tag.setdefault(normalize_tag(row["clan_tag"]), []).append(row)
        except ValueError:
            LOGGER.warning("Skipping invalid linked clan tag in monitor: %s", row.get("clan_tag"))

    client = await bot.state.coc_service.get_client()
    for clan_tag, targets in by_tag.items():
        try:
            monitored = await _build_monitored_war(bot, client, clan_tag)
        except coc.PrivateWarLog:
            LOGGER.info("Skipping %s because the war log is private.", clan_tag)
            continue
        except coc.NotFound:
            LOGGER.info("Skipping %s because Clash API did not find the clan.", clan_tag)
            continue
        except Exception:
            LOGGER.exception("Failed to poll war state for %s.", clan_tag)
            continue

        if monitored is None:
            snapshot = await bot.state.database.get_war_snapshot(clan_tag)
            if snapshot is not None:
                await _announce_war_ended(bot, snapshot, targets)
            continue

        await bot.state.database.upsert_war_snapshot(
            clan_tag=monitored.clan_tag,
            clan_name=monitored.clan_name,
            opponent_tag=monitored.opponent_tag,
            opponent_name=monitored.opponent_name,
            state=monitored.state,
            preparation_start=monitored.preparation_start,
            battle_start=monitored.battle_start,
            battle_end=monitored.battle_end,
            team_size=monitored.team_size,
            war_key=monitored.war_key,
            fwa_classification=monitored.classification,
            planned_result=monitored.event_kind,
            external_checked_at=monitored.external_checked_at,
            raw={
                "source": "clash_api",
                "event_kind": monitored.event_kind,
                "classification": monitored.classification,
            },
        )
        await _announce_to_targets(bot, monitored, targets)


async def _build_monitored_war(
    bot: PakFwaBot,
    client: coc.Client,
    clan_tag: str,
) -> MonitoredWar | None:
    war = await client.get_current_war(clan_tag)
    if not _war_is_active(war):
        return None

    state = str(getattr(getattr(war, "state", ""), "name", getattr(war, "state", "")))
    primary_tag = normalize_tag(war.clan.tag)
    opponent_tag = normalize_tag(war.opponent.tag)
    preparation_start = _timestamp_to_datetime(getattr(war, "preparation_start_time", None))
    battle_start = _timestamp_to_datetime(getattr(war, "start_time", None))
    battle_end = _timestamp_to_datetime(getattr(war, "end_time", None))
    war_key = _war_key(primary_tag, opponent_tag, preparation_start, battle_start)

    monitored = MonitoredWar(
        clan_tag=primary_tag,
        clan_name=war.clan.name,
        opponent_tag=opponent_tag,
        opponent_name=war.opponent.name,
        state=state,
        war_key=war_key,
        preparation_start=preparation_start,
        battle_start=battle_start,
        battle_end=battle_end,
        team_size=getattr(war, "team_size", None),
    )

    snapshot = await bot.state.database.get_war_snapshot(primary_tag)
    _hydrate_from_snapshot(monitored, snapshot)
    if monitored.event_message is not None:
        return monitored

    if not _external_lookup_allowed(bot, war):
        return monitored

    decision = await bot.state.external_lookup_gate.acquire_automatic(
        clan_tag=primary_tag,
        last_checked_at=snapshot.get("external_checked_at") if snapshot else None,
    )
    if not decision.allowed:
        return monitored

    async with bot.state.external_lookup_gate.endpoint_slot():
        primary_stats, primary_points, opponent_points = await asyncio.gather(
            bot.state.fwa_stats_service.build_clan_record(primary_tag),
            bot.state.fwa_service.lookup_points(primary_tag),
            bot.state.fwa_service.lookup_points(opponent_tag),
        )
    record = _select_matching_fwa_war(primary_stats, opponent_tag)
    monitored.external_checked_at = datetime.now(timezone.utc)
    if record is None:
        return monitored

    event = _event_from_record(record, war, primary_points, opponent_points)
    if event is None:
        return monitored

    monitored.event_kind = event.kind
    monitored.classification = event.classification
    monitored.event_message = _announcement_message(event)
    return monitored


def _external_lookup_allowed(bot: PakFwaBot, war: coc.ClanWar) -> bool:
    elapsed = _hours_since_match(war)
    if elapsed is None:
        return False
    return (
        bot.state.config.fwa_external_lookup_start_hours
        <= elapsed
        <= bot.state.config.fwa_external_lookup_end_hours
    )


def _war_key(
    clan_tag: str,
    opponent_tag: str | None,
    preparation_start: datetime | None,
    battle_start: datetime | None,
) -> str:
    started = preparation_start or battle_start
    started_key = started.isoformat(timespec="seconds") if started is not None else "unknown"
    return f"{clan_tag}:{opponent_tag or 'unknown'}:{started_key}"


async def _announce_to_targets(
    bot: PakFwaBot,
    war: MonitoredWar,
    targets: list[dict[str, Any]],
) -> None:
    for target in targets:
        channel_id = target.get("announcement_channel_id") or target.get("clan_channel_id")
        if not channel_id:
            continue

        if war.event_message and _enabled(target.get("fwa_ready")):
            await _send_once(
                bot,
                guild_id=target["guild_id"],
                clan_tag=war.clan_tag,
                channel_id=channel_id,
                event_key=f"{war.war_key}:fwa-ready:{war.event_kind}",
                content=war.event_message,
            )
            continue

        if _enabled(target.get("war_found")):
            await _send_once(
                bot,
                guild_id=target["guild_id"],
                clan_tag=war.clan_tag,
                channel_id=channel_id,
                event_key=f"{war.war_key}:war-found",
                content=(
                    f"War found: **{war.clan_name}** vs **{war.opponent_name or 'Unknown'}**.\n"
                    "I’ll post FWA instructions when they’re ready."
                ),
            )


async def _announce_war_ended(
    bot: PakFwaBot,
    snapshot: dict[str, Any],
    targets: list[dict[str, Any]],
) -> None:
    war_key = snapshot.get("war_key")
    if not war_key:
        return
    battle_end = snapshot.get("battle_end")
    if not isinstance(battle_end, datetime):
        return
    hours_since_end = (datetime.now(timezone.utc) - battle_end.astimezone(timezone.utc)).total_seconds() / 3600
    if hours_since_end > 6:
        return

    clan_tag = snapshot["clan_tag"]
    clan_name = snapshot.get("clan_name") or clan_tag
    opponent_name = snapshot.get("opponent_name") or "Unknown"
    for target in targets:
        channel_id = target.get("announcement_channel_id") or target.get("clan_channel_id")
        if not channel_id or not _enabled(target.get("war_ended")):
            continue

        await _send_once(
            bot,
            guild_id=target["guild_id"],
            clan_tag=clan_tag,
            channel_id=channel_id,
            event_key=f"{war_key}:war-ended",
            content=f"War ended: **{clan_name}** vs **{opponent_name}**.",
        )


async def _send_once(
    bot: PakFwaBot,
    *,
    guild_id: int | str,
    clan_tag: str,
    channel_id: int | str,
    event_key: str,
    content: str,
) -> None:
    should_send = await bot.state.database.mark_announcement_sent(
        guild_id=guild_id,
        clan_tag=clan_tag,
        event_key=event_key,
        channel_id=channel_id,
    )
    if not should_send:
        return

    channel = bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except (discord.Forbidden, discord.HTTPException, ValueError):
            LOGGER.warning("Could not fetch announcement channel %s.", channel_id)
            return

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        LOGGER.warning("Announcement target %s is not a text channel or thread.", channel_id)
        return

    try:
        await channel.send(content)
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Could not send proactive war announcement to channel %s.", channel_id)


def _enabled(value: Any) -> bool:
    return value is not False


def _hydrate_from_snapshot(war: MonitoredWar, snapshot: dict[str, Any] | None) -> None:
    if not snapshot:
        return
    if snapshot.get("war_key") != war.war_key:
        return

    kind = snapshot.get("planned_result")
    if kind not in {"win", "lose", "mismatch", "blacklist"}:
        return

    event = FwaEvent(
        kind=kind,
        opponent_name=snapshot.get("opponent_name") or war.opponent_name or "Unknown",
        war_record=SimpleNamespace(),
        classification=snapshot.get("fwa_classification") or "unknown",
    )
    war.event_kind = kind
    war.classification = event.classification
    war.event_message = _announcement_message(event)
