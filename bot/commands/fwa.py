from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from typing import Any, Literal

import asyncio
import coc
import discord
from discord import app_commands

from ..coc_service import CocConfigurationError
from ..fwa_sources import (
    FwaPointsRecord,
    FwaStatsClanRecord,
    FwaStatsWarRecord,
    render_fwa_stats_section,
    render_points_section,
)
from ..resolver import LinkResolutionError, LinkResolver, normalize_tag


FWA_DELAY_MARGIN_HOURS = 3
FwaEventKind = Literal["win", "lose", "mismatch", "blacklist"]
FwaClassification = Literal["fwa", "blacklist", "mismatch", "unknown"]


@dataclass(frozen=True, slots=True)
class FwaEvent:
    kind: FwaEventKind
    opponent_name: str
    war_record: FwaStatsWarRecord
    primary_points: int | None = None
    opponent_points: int | None = None
    point_note: str | None = None
    classification: FwaClassification = "unknown"


async def autocomplete_fwa_clans(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None or not hasattr(interaction.client, "state"):
        return []

    try:
        clans = await interaction.client.state.database.list_server_clans(interaction.guild_id)  # type: ignore[attr-defined]
    except RuntimeError:
        return []

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
        name_bits = [clan["clan_name"], clan["clan_tag"]]
        if clan.get("alias"):
            name_bits.append(f"alias {clan['alias']}")
        label = " - ".join(str(bit) for bit in name_bits)
        choices.append(app_commands.Choice(name=label[:100], value=str(clan["clan_tag"])))
        if len(choices) >= 25:
            break
    return choices


def build_fwa_command() -> app_commands.Command[Any, ..., None]:
    @app_commands.autocomplete(clan=autocomplete_fwa_clans)
    @app_commands.describe(clan="Linked clan, alias, or raw clan tag. Optional when a default exists.")
    async def fwa_callback(interaction: discord.Interaction, clan: str | None = None) -> None:
        if interaction.client is None or not hasattr(interaction.client, "state"):
            await interaction.response.send_message("FWA lookup is unavailable right now.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        state = interaction.client.state  # type: ignore[attr-defined]

        try:
            resolved = await LinkResolver(state).resolve_clan_tag(interaction, clan)
            client = await state.coc_service.get_client()
            war = await client.get_current_war(resolved.tag)
        except LinkResolutionError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except (ValueError, CocConfigurationError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except coc.NotFound:
            await interaction.followup.send(f"No Clash clan was found for `{clan}`.", ephemeral=True)
            return
        except coc.PrivateWarLog:
            await interaction.followup.send("The official Clash API says this clan's war log is private.", ephemeral=True)
            return

        if not _war_is_active(war):
            await interaction.followup.send(
                f"No active war is visible for **{war.clan.name}** (`{war.clan.tag}`) right now.",
                ephemeral=True,
            )
            return

        primary_tag = normalize_tag(war.clan.tag)
        opponent_tag = normalize_tag(war.opponent.tag)
        cached_event = await _event_from_cached_snapshot(state, war, primary_tag, opponent_tag)
        if cached_event is not None:
            public_sent = await _send_event_message(interaction, war, cached_event)
            if public_sent:
                await interaction.followup.send(_copy_block(cached_event), ephemeral=True)
            else:
                await interaction.followup.send(_fallback_main_message(cached_event), ephemeral=False)
            return

        decision = await _manual_external_lookup_decision(state, interaction, primary_tag)
        if not decision.allowed:
            await interaction.followup.send(_manual_lookup_wait_message(decision.retry_after_seconds), ephemeral=True)
            return

        async with state.external_lookup_gate.endpoint_slot():
            primary_stats, opponent_stats, primary_points, opponent_points = await asyncio.gather(
                state.fwa_stats_service.build_clan_record(primary_tag),
                state.fwa_stats_service.build_clan_record(opponent_tag),
                state.fwa_service.lookup_points(primary_tag),
                state.fwa_service.lookup_points(opponent_tag),
            )
        await _store_external_attempt(state, war, primary_tag, opponent_tag)
        current_record = _select_matching_fwa_war(primary_stats, opponent_tag)

        if current_record is None:
            elapsed_hours = _hours_since_match(war)
            if elapsed_hours is not None and elapsed_hours < FWA_DELAY_MARGIN_HOURS and _is_preparation(war):
                await interaction.followup.send(
                    _hold_tight_message(war, elapsed_hours),
                    ephemeral=True,
                )
                return

            debug_text = _build_debug_report(
                war=war,
                primary_stats=primary_stats,
                opponent_stats=opponent_stats,
                primary_points=primary_points,
                opponent_points=opponent_points,
                elapsed_hours=elapsed_hours,
                reason="FWA Stats did not expose a matching current war after the safety window.",
            )
            await _send_public_error(interaction, war, debug_text)
            return

        if not _fwa_record_matches_official_war(current_record, war):
            debug_text = _build_debug_report(
                war=war,
                primary_stats=primary_stats,
                opponent_stats=opponent_stats,
                primary_points=primary_points,
                opponent_points=opponent_points,
                elapsed_hours=_hours_since_match(war),
                reason="FWA Stats current war did not match the official Clash API opponent.",
            )
            await _send_public_error(interaction, war, debug_text)
            return

        event = _event_from_record(current_record, war, primary_points, opponent_points)
        if event is None:
            classification = _classify_fwa_record(current_record)
            debug_text = _build_debug_report(
                war=war,
                primary_stats=primary_stats,
                opponent_stats=opponent_stats,
                primary_points=primary_points,
                opponent_points=opponent_points,
                elapsed_hours=_hours_since_match(war),
                reason=(
                    "FWA Stats returned current war data, but no production-safe instruction could be derived. "
                    f"Classification: {classification}."
                ),
            )
            await _send_public_error(interaction, war, debug_text)
            return

        await _store_event_snapshot(state, war, primary_tag, opponent_tag, event)
        public_sent = await _send_event_message(interaction, war, event)
        if public_sent:
            await interaction.followup.send(_copy_block(event), ephemeral=True)
        else:
            await interaction.followup.send(_fallback_main_message(event), ephemeral=False)

    return app_commands.Command(
        name="fwa",
        description="Check the active war against FWA Stats and return the copy-ready war instruction.",
        callback=fwa_callback,
    )


def _war_is_active(war: coc.ClanWar) -> bool:
    state_obj = getattr(war, "state", "")
    state = str(getattr(state_obj, "name", state_obj)).lower()
    return state not in {"notinwar", "not_in_war", "warended", "ended"}


def _is_preparation(war: coc.ClanWar) -> bool:
    state_obj = getattr(war, "state", "")
    return "preparation" in str(getattr(state_obj, "name", state_obj)).lower()


def _hours_since_match(war: coc.ClanWar) -> float | None:
    started = _timestamp_to_datetime(getattr(war, "preparation_start_time", None))
    if started is None:
        started = _timestamp_to_datetime(getattr(war, "start_time", None))
    if started is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - started).total_seconds() / 3600)


def _timestamp_to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    candidate = getattr(value, "time", value)
    if isinstance(candidate, datetime):
        if candidate.tzinfo is None:
            return candidate.replace(tzinfo=timezone.utc)
        return candidate.astimezone(timezone.utc)
    if isinstance(candidate, str):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _select_matching_fwa_war(record: FwaStatsClanRecord, opponent_tag: str) -> FwaStatsWarRecord | None:
    normalized_opponent = normalize_tag(opponent_tag)
    for war in record.wars:
        if _same_tag(war.opponent_tag, normalized_opponent) and _is_current_fwa_stats_war(war):
            return war
    for war in record.wars[:5]:
        if _same_tag(war.opponent_tag, normalized_opponent) and (war.matched is True or war.end_time is None):
            return war
    return None


def _is_current_fwa_stats_war(war: FwaStatsWarRecord) -> bool:
    result = (war.result or "").lower()
    return result in {"inwar", "in_war", "preparation", "preparationday"} or (
        war.matched is True and result not in {"win", "lose", "loss", "lost", "won"}
    )


def _same_tag(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    try:
        return normalize_tag(left) == normalize_tag(right)
    except ValueError:
        return left.strip().upper() == right.strip().upper()


def _fwa_record_matches_official_war(record: FwaStatsWarRecord, war: coc.ClanWar) -> bool:
    return _same_tag(record.clan_tag, war.clan.tag) and _same_tag(record.opponent_tag, war.opponent.tag)


async def _event_from_cached_snapshot(
    state: Any,
    war: coc.ClanWar,
    primary_tag: str,
    opponent_tag: str,
) -> FwaEvent | None:
    try:
        snapshot = await state.database.get_war_snapshot(primary_tag)
    except RuntimeError:
        return None
    if not snapshot:
        return None
    if snapshot.get("war_key") != _war_key(primary_tag, opponent_tag, war):
        return None
    kind = snapshot.get("planned_result")
    if kind not in {"win", "lose", "mismatch", "blacklist"}:
        return None

    return FwaEvent(
        kind=kind,
        opponent_name=snapshot.get("opponent_name") or war.opponent.name,
        war_record=SimpleNamespace(),
        classification=snapshot.get("fwa_classification") or "unknown",
    )


def _war_key(primary_tag: str, opponent_tag: str, war: coc.ClanWar) -> str:
    started = _timestamp_to_datetime(getattr(war, "preparation_start_time", None))
    if started is None:
        started = _timestamp_to_datetime(getattr(war, "start_time", None))
    started_key = started.isoformat(timespec="seconds") if started is not None else "unknown"
    return f"{primary_tag}:{opponent_tag}:{started_key}"


def _external_lookup_allowed(state: Any, war: coc.ClanWar) -> bool:
    elapsed = _hours_since_match(war)
    if elapsed is None:
        return False
    config = state.config
    return config.fwa_external_lookup_start_hours <= elapsed <= config.fwa_external_lookup_end_hours


async def _manual_external_lookup_decision(state: Any, interaction: discord.Interaction, primary_tag: str) -> Any:
    snapshot = None
    try:
        snapshot = await state.database.get_war_snapshot(primary_tag)
    except RuntimeError:
        pass
    return await state.external_lookup_gate.acquire_manual(
        clan_tag=primary_tag,
        user_id=interaction.user.id,
        guild_id=interaction.guild_id,
        last_checked_at=snapshot.get("external_checked_at") if snapshot else None,
    )


def _manual_lookup_wait_message(retry_after_seconds: int) -> str:
    minutes = max(1, round(retry_after_seconds / 60))
    return f"I just checked this war recently. Try again in about {minutes} min."


async def _store_event_snapshot(
    state: Any,
    war: coc.ClanWar,
    primary_tag: str,
    opponent_tag: str,
    event: FwaEvent,
) -> None:
    try:
        await state.database.upsert_war_snapshot(
            clan_tag=primary_tag,
            clan_name=war.clan.name,
            opponent_tag=opponent_tag,
            opponent_name=war.opponent.name,
            state=str(getattr(getattr(war, "state", ""), "name", getattr(war, "state", ""))),
            preparation_start=_timestamp_to_datetime(getattr(war, "preparation_start_time", None)),
            battle_start=_timestamp_to_datetime(getattr(war, "start_time", None)),
            battle_end=_timestamp_to_datetime(getattr(war, "end_time", None)),
            team_size=getattr(war, "team_size", None),
            war_key=_war_key(primary_tag, opponent_tag, war),
            fwa_classification=event.classification,
            planned_result=event.kind,
            external_checked_at=datetime.now(timezone.utc),
            raw={"source": "manual_fwa_command", "event_kind": event.kind, "classification": event.classification},
        )
    except RuntimeError:
        return


async def _store_external_attempt(state: Any, war: coc.ClanWar, primary_tag: str, opponent_tag: str) -> None:
    try:
        await state.database.upsert_war_snapshot(
            clan_tag=primary_tag,
            clan_name=war.clan.name,
            opponent_tag=opponent_tag,
            opponent_name=war.opponent.name,
            state=str(getattr(getattr(war, "state", ""), "name", getattr(war, "state", ""))),
            preparation_start=_timestamp_to_datetime(getattr(war, "preparation_start_time", None)),
            battle_start=_timestamp_to_datetime(getattr(war, "start_time", None)),
            battle_end=_timestamp_to_datetime(getattr(war, "end_time", None)),
            team_size=getattr(war, "team_size", None),
            war_key=_war_key(primary_tag, opponent_tag, war),
            external_checked_at=datetime.now(timezone.utc),
            raw={"source": "manual_fwa_command", "event_kind": None},
        )
    except RuntimeError:
        return


def _event_from_record(
    record: FwaStatsWarRecord,
    war: coc.ClanWar,
    primary_points: FwaPointsRecord | None = None,
    opponent_points: FwaPointsRecord | None = None,
) -> FwaEvent | None:
    result = (record.result or "").lower()
    opponent_name = record.opponent_name or war.opponent.name
    classification = _classify_fwa_record(record)

    if classification == "blacklist":
        return FwaEvent(kind="blacklist", opponent_name=opponent_name, war_record=record, classification=classification)
    if classification == "mismatch":
        return FwaEvent(kind="mismatch", opponent_name=opponent_name, war_record=record, classification=classification)
    if "lose" in result or "loss" in result or result == "lost":
        return FwaEvent(kind="lose", opponent_name=opponent_name, war_record=record, classification=classification)
    if "win" in result or "won" in result:
        return FwaEvent(kind="win", opponent_name=opponent_name, war_record=record, classification=classification)
    if classification == "fwa":
        planned = _planned_fwa_result(primary_points, opponent_points)
        if planned is not None:
            return FwaEvent(
                kind=planned,
                opponent_name=opponent_name,
                war_record=record,
                primary_points=primary_points.point_balance if primary_points else None,
                opponent_points=opponent_points.point_balance if opponent_points else None,
                point_note=getattr(primary_points, "current_war_note", None),
                classification=classification,
            )
    return None


def _classify_fwa_record(record: FwaStatsWarRecord) -> FwaClassification:
    result = (record.result or "").lower()
    info = (record.opponent_info or "").lower()
    if "black" in info or "black" in result:
        return "blacklist"
    if "mismatch" in info or "mismatch" in result:
        return "mismatch"
    if "unknown" in info or record.matched is False:
        return "mismatch"
    if info == "fwa" and record.matched is True:
        return "fwa"
    if "no war" in info or "nowar" in info:
        return "mismatch"
    return "unknown"


def _planned_fwa_result(
    primary_points: FwaPointsRecord | None,
    opponent_points: FwaPointsRecord | None,
) -> Literal["win", "lose"] | None:
    if primary_points is None or opponent_points is None:
        return None
    primary_balance = primary_points.point_balance
    opponent_balance = opponent_points.point_balance
    if primary_balance is None or opponent_balance is None or primary_balance == opponent_balance:
        return None
    return "win" if primary_balance > opponent_balance else "lose"


def _copy_block(event: FwaEvent) -> str:
    if event.kind == "win":
        return (
            "```\n"
            f"🟩WIN WAR vs {event.opponent_name} 🟩\n"
            "1st attack on mirror For 3 Stars\n"
            "2nd attack any base For 1/2 Stars\n"
            "Clean Up: In last 10 hrs all bases are open for 3 Stars\n"
            "```"
        )
    if event.kind == "lose":
        return (
            "```\n"
            f"🟡LOSE WAR vs {event.opponent_name} 🟡\n"
            "1st attack on mirror For 2 Stars\n"
            "2nd attack any base For 1 Stars\n"
            "Clean Up: In last 10 hrs all bases are open for 2 Stars\n"
            "```"
        )
    if event.kind == "mismatch":
        return (
            "```\n"
            f"🟡Mismatch against {event.opponent_name} 🟡\n"
            "📌LEAVE BASES As 💎FWA💎\n"
            "```"
        )
    return (
        "```\n"
        f"🟥Blacklist War against {event.opponent_name} 🟥\n"
        "📌ACTIVATE WAR BASES\n"
        "```"
    )


async def _send_event_message(interaction: discord.Interaction, war: coc.ClanWar, event: FwaEvent) -> bool:
    if interaction.channel is None:
        return False

    try:
        await interaction.channel.send(_announcement_message(event))  # type: ignore[union-attr]
    except (discord.Forbidden, discord.HTTPException):
        return False
    return True


def _fallback_main_message(event: FwaEvent) -> str:
    return _announcement_message(event)


def _announcement_message(event: FwaEvent) -> str:
    return _copy_block(event).removeprefix("```\n").removesuffix("\n```")


def _hold_tight_message(war: coc.ClanWar, elapsed_hours: float) -> str:
    remaining = max(0.0, FWA_DELAY_MARGIN_HOURS - elapsed_hours)
    return (
        "Hold on tight. The official Clash API shows an active war, but FWA Stats may still be inside "
        f"the safety delay.\n\nWar: **{war.clan.name}** (`{war.clan.tag}`) vs "
        f"**{war.opponent.name}** (`{war.opponent.tag}`)\n"
        f"Elapsed since match: {elapsed_hours:.1f}h. Safety margin remaining: about {remaining:.1f}h."
    )


async def _send_public_error(interaction: discord.Interaction, war: coc.ClanWar, debug_text: str) -> None:
    file_bytes = BytesIO(debug_text.encode("utf-8"))
    attachment = discord.File(fp=file_bytes, filename=f"fwa_debug_{war.clan.tag.strip('#')}.txt")
    embed = discord.Embed(
        title="FWA safety check needs attention",
        description=(
            f"Official Clash API shows **{war.clan.name}** vs **{war.opponent.name}**, "
            "but the expected FWA Stats data was not usable after the safety window."
        ),
        color=discord.Color.red(),
    )
    embed.add_field(name="Action", value="Debug attachment included for review.", inline=False)
    if interaction.channel is not None:
        await interaction.channel.send(embed=embed, file=attachment)
    await interaction.followup.send("I posted a public FWA safety report with the debug file.", ephemeral=True)


def _build_debug_report(
    *,
    war: coc.ClanWar,
    primary_stats: FwaStatsClanRecord,
    opponent_stats: FwaStatsClanRecord,
    primary_points: FwaPointsRecord,
    opponent_points: FwaPointsRecord,
    elapsed_hours: float | None,
    reason: str,
) -> str:
    lines = [
        "FWA command debug report",
        f"Generated at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Reason: {reason}",
        "",
        "Official Clash API",
        f"Clan: {war.clan.name} ({war.clan.tag})",
        f"Opponent: {war.opponent.name} ({war.opponent.tag})",
        f"State: {war.state}",
        f"Status: {getattr(war, 'status', 'n/a')}",
        f"Team size: {war.team_size}",
        f"Preparation start: {getattr(war, 'preparation_start_time', 'n/a')}",
        f"Battle start: {getattr(war, 'start_time', 'n/a')}",
        f"Battle end: {getattr(war, 'end_time', 'n/a')}",
        f"Elapsed since match hours: {elapsed_hours if elapsed_hours is not None else 'unknown'}",
        f"Our stars/destruction/attacks: {war.clan.stars}/{war.clan.destruction:.1f}/{war.clan.attacks_used}",
        f"Opponent stars/destruction/attacks: {war.opponent.stars}/{war.opponent.destruction:.1f}/{war.opponent.attacks_used}",
        "",
    ]
    lines.extend(render_points_section(primary_points, heading="Primary FWA Points"))
    lines.append("")
    lines.extend(render_points_section(opponent_points, heading="Opponent FWA Points"))
    lines.append("")
    lines.extend(render_fwa_stats_section(primary_stats, heading="Primary FWA Stats"))
    lines.append("")
    lines.extend(render_fwa_stats_section(opponent_stats, heading="Opponent FWA Stats"))
    return "\n".join(lines)
