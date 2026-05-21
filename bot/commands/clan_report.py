from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import logging
from io import BytesIO
import re
from typing import Any, Literal, cast

import coc
import discord
from discord import app_commands

from ..coc_service import CocConfigurationError
from ..fwa_sources import (
    FwaExternalIntel,
    compare_fwa_records,
    compare_fwa_stats_records,
    render_fwa_guide_section,
    _stats_clan_name,
    _stats_summary_int,
    render_fwa_stats_section,
    render_cc_section,
    render_points_section,
    format_optional_float,
    format_yes_no,
)


LOGGER = logging.getLogger(__name__)
WarFocus = Literal["ongoing", "recent"]


@dataclass(slots=True)
class WarHistoryData:
    focus: WarFocus
    current_war: coc.ClanWar | None
    current_war_status: str
    current_war_error: str | None
    war_log_entries: tuple[Any, ...]
    war_log_status: str
    war_log_error: str | None


@dataclass(slots=True)
class ClanReportData:
    clan: coc.Clan
    war_focus: WarFocus
    war_history: WarHistoryData
    members: list[coc.Player]
    external: FwaExternalIntel | None
    text: str


def build_clan_report_command() -> app_commands.Command[Any, ..., None]:
    @app_commands.describe(clan_tag="The clan tag to inspect, with or without the # prefix.")
    @app_commands.describe(
        comparison_tag="Optional second clan tag to compare against. If omitted, the current war opponent is used when available.",
    )
    @app_commands.describe(
        war="Optional war focus. Use ongoing for the active war or recent for the latest 10 concluded wars.",
    )
    async def clan_report_callback(
        interaction: discord.Interaction,
        clan_tag: str,
        comparison_tag: str | None = None,
        war: WarFocus | None = None,
    ) -> None:
        bot = interaction.client
        if bot is None or not hasattr(bot, "state"):
            await interaction.response.send_message(
                "Clash of Clans reporting is unavailable right now.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            report = await build_clan_report(bot.state, clan_tag, comparison_tag, war_focus=war or "ongoing")
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except CocConfigurationError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except coc.NotFound:
            await interaction.followup.send(
                f"No Clash of Clans clan was found for `{_normalize_clan_tag(clan_tag)}`.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            LOGGER.exception("Clan report generation failed for %s", clan_tag)
            await interaction.followup.send(
                f"Clan report generation failed: `{type(exc).__name__}: {exc}`.",
                ephemeral=True,
            )
            return

        file_bytes = BytesIO(report.text.encode("utf-8"))
        attachment = discord.File(
            fp=file_bytes,
            filename=f"clan_report_{_sanitize_filename(report.clan.tag)}.txt",
        )

        summary_embed = build_summary_embed(report)
        await interaction.followup.send(embed=summary_embed, file=attachment, ephemeral=True)

    return app_commands.Command(
        name="clanreport",
        description="Generate a detailed Clash of Clans clan intelligence report with FWA context.",
        callback=clan_report_callback,
    )


async def build_clan_report(
    state,
    clan_tag: str,
    comparison_tag: str | None = None,
    *,
    war_focus: WarFocus = "ongoing",
) -> ClanReportData:
    war_focus = _normalize_war_focus(war_focus)
    client = await state.coc_service.get_client()
    normalized_tag = _normalize_clan_tag(clan_tag)
    clan = await client.get_clan(normalized_tag)

    detailed_members_task = asyncio.create_task(_collect_detailed_members(clan))
    current_war_task = asyncio.create_task(_safe_get_current_war(client, normalized_tag))
    war_log_limit = 10 if war_focus == "recent" else 1
    war_log_task = asyncio.create_task(_safe_get_war_log(client, normalized_tag, war_log_limit))
    members, current_war_result, war_log_result = await asyncio.gather(
        detailed_members_task,
        current_war_task,
        war_log_task,
    )

    current_war, current_war_status, current_war_error = current_war_result
    war_log_entries, war_log_status, war_log_error = war_log_result

    secondary_tag = comparison_tag or _select_secondary_tag(current_war, war_log_entries)
    external = await state.fwa_service.build_external_intel(
        normalized_tag,
        secondary_tag,
        stats_service=state.fwa_stats_service,
    )

    war_history = WarHistoryData(
        focus=war_focus,
        current_war=current_war,
        current_war_status=current_war_status,
        current_war_error=current_war_error,
        war_log_entries=war_log_entries,
        war_log_status=war_log_status,
        war_log_error=war_log_error,
    )

    text = build_report_text(clan, members, external, war_history)
    return ClanReportData(
        clan=clan,
        war_focus=war_focus,
        war_history=war_history,
        members=members,
        external=external,
        text=text,
    )


async def _collect_detailed_members(clan: coc.Clan) -> list[coc.Player]:
    members: list[coc.Player] = []
    async for member in clan.get_detailed_members():
        members.append(member)
    return members


def build_summary_embed(report: ClanReportData) -> discord.Embed:
    clan = report.clan
    war = report.war_history.current_war
    members = report.members
    external = report.external

    roster_count = len(members)
    total_trophies = sum(member.trophies for member in members)
    avg_trophies = round(total_trophies / roster_count) if roster_count else 0
    opted_in = sum(1 for member in members if getattr(member, "war_opted_in", False))
    th_levels = sorted({member.town_hall for member in members})

    embed = discord.Embed(
        title=f"{clan.name} clan report",
        description=(
            "Detailed clan intelligence pulled from FWA Stats first, then cross-checked against the Clash of Clans API, points.fwafarm, and cc.fwafarm when available. "
            "Base layouts and true war weights are not exposed by the public APIs, so the report focuses on the strongest available proxies."
        ),
        color=discord.Color.blurple(),
    )

    if getattr(clan, "badge", None) is not None and getattr(clan.badge, "url", None):
        embed.set_thumbnail(url=clan.badge.url)

    embed.add_field(
        name="Clan",
        value="\n".join(
            [
                f"Tag: `{clan.tag}`",
                f"Level: {clan.level}",
                f"Location: {getattr(clan.location, 'name', str(clan.location))}",
                f"Members: {clan.member_count}/{roster_count}",
            ]
        ),
        inline=False,
    )

    embed.add_field(
        name="War History",
        value="\n".join(
            [
                f"Wins: {format_optional_int(clan.war_wins)}",
                f"Losses: {format_optional_int(clan.war_losses)}",
                f"Ties: {format_optional_int(clan.war_ties)}",
                f"Win streak: {format_optional_int(clan.war_win_streak)}",
                f"War log: {'public' if clan.public_war_log else 'private'}",
            ]
        ),
        inline=False,
    )

    embed.add_field(
        name="Roster",
        value="\n".join(
            [
                f"Opt-in: {opted_in}/{roster_count}",
                f"Avg trophies: {avg_trophies}",
                f"Town halls: {', '.join(str(level) for level in th_levels)}",
                f"Capital points: {clan.capital_points}",
            ]
        ),
        inline=False,
    )

    embed.add_field(
        name="War Focus",
        value="\n".join(
            [
                f"Mode: `{report.war_focus}`",
                f"Current war fetch: {report.war_history.current_war_status}"
                + (f" ({getattr(war.state, 'name', war.state)})" if war is not None else ""),
                f"War log fetch: {report.war_history.war_log_status}",
            ]
        ),
        inline=False,
    )

    if external is not None and external.primary_points is not None:
        comparison_lines = compare_fwa_records(external.primary_points, external.secondary_points)
        cc_lines: list[str] = []
        stats_lines: list[str] = []
        if external.primary_cc is not None:
            cc_lines.append(
                f"CC status: {external.primary_cc.source_status}"
                + (f", flags: {len(external.primary_cc.labels)}" if external.primary_cc.labels else "")
            )
        if external.secondary_cc is not None:
            cc_lines.append(
                f"Opponent CC status: {external.secondary_cc.source_status}"
                + (f", flags: {len(external.secondary_cc.labels)}" if external.secondary_cc.labels else "")
            )
        if external.primary_stats is not None:
            stats_lines.append(
                f"FWA Stats status: {external.primary_stats.source_status}"
                + (f" ({len(external.primary_stats.members)} members)" if external.primary_stats.members else "")
            )
        if external.secondary_stats is not None:
            stats_lines.append(
                f"Opponent FWA Stats status: {external.secondary_stats.source_status}"
                + (f" ({len(external.secondary_stats.members)} members)" if external.secondary_stats.members else "")
            )
        stats_lines.extend(compare_fwa_stats_records(external.primary_stats, external.secondary_stats))

        embed.add_field(
            name="FWA Sources",
            value="\n".join((comparison_lines + stats_lines + cc_lines)[:10]) or "No external FWA data could be loaded.",
            inline=False,
        )

    if war is None:
        embed.add_field(
            name="Current War",
            value="No current war data was returned, or the war log is private.",
            inline=False,
        )
    else:
        our_clan = war.clan
        opponent = war.opponent
        embed.add_field(
            name="Current War",
            value="\n".join(
                [
                    f"State: `{war.state}`",
                    f"Status: `{war.status}`",
                    f"Type: `{war.type}`",
                    f"CWL: `{war.is_cwl}`",
                    f"Team size: {war.team_size}",
                    f"Attacks per member: {war.attacks_per_member}",
                    f"Our attacks used: {our_clan.attacks_used}/{our_clan.total_attacks}",
                    f"Our stars: {our_clan.stars}/{our_clan.max_stars}",
                    f"Our destruction: {our_clan.destruction:.1f}%",
                    f"Average attack duration: {our_clan.average_attack_duration}s",
                ]
            ),
            inline=False,
        )
        embed.add_field(
            name="Opponent",
            value="\n".join(
                [
                    f"{opponent.name} (level {opponent.level})",
                    f"Members: {len(opponent.members)}",
                    f"Attacks used: {opponent.attacks_used}/{opponent.total_attacks}",
                    f"Stars: {opponent.stars}/{opponent.max_stars}",
                    f"Destruction: {opponent.destruction:.1f}%",
                    f"Average attack duration: {opponent.average_attack_duration}s",
                ]
            ),
            inline=False,
        )

    embed.add_field(
        name="Data Limits",
        value=(
            "The official API does not expose the hidden base-layout export, war weight, or exact defensive layout placement. "
            "This report instead uses TH level, war participation, attacks, destruction, hero/troop levels, roster stats, "
            "and public FWA database lookups for point and status context."
        ),
        inline=False,
    )

    return embed


def build_report_text(
    clan: coc.Clan,
    members: list[coc.Player],
    external: FwaExternalIntel | None,
    war_history: WarHistoryData,
) -> str:
    members_by_tag = {member.tag: member for member in members}
    lines: list[str] = []

    lines.append("CLAN REPORT")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"War focus: {war_history.focus}")
    lines.append("Primary source: FWA Stats JSON exports")
    lines.append("Secondary source: points.fwafarm.com")
    lines.append("Supporting sources: Clash of Clans API, cc.fwafarm.com, public FWA guide")
    lines.append("")

    lines.append("CLAN OVERVIEW")
    lines.extend(_format_clan_overview(clan))
    lines.append("")

    lines.append("CLAN FLAGS AND CAPITAL")
    lines.extend(_format_capital_and_labels(clan))
    lines.append("")

    lines.append("FWA STATS PRIMARY SNAPSHOT")
    if external is not None and external.primary_stats is not None:
        lines.extend(render_fwa_stats_section(external.primary_stats, heading="Primary clan FWA Stats lookup"))
    else:
        lines.append("Primary clan FWA Stats lookup unavailable.")
    lines.append("")

    if external is not None and external.secondary_tag is not None:
        lines.append("FWA STATS COMPARISON")
        lines.extend(compare_fwa_stats_records(external.primary_stats, external.secondary_stats))
        lines.append("")

    lines.append("POINTS.FWAFARM SECONDARY CHECK")
    if external is not None and external.primary_points is not None:
        lines.extend(render_points_section(external.primary_points, heading="Primary clan point lookup"))
    else:
        lines.append("Primary clan point lookup unavailable.")
    lines.append("")

    if external is not None and external.secondary_tag is not None:
        if external.secondary_points is not None:
            lines.extend(render_points_section(external.secondary_points, heading="Opponent point lookup"))
        else:
            lines.append("Opponent point lookup unavailable.")
        lines.append("")

    if external is not None and external.primary_points is not None:
        lines.append("POINTS COMPARISON")
        lines.extend(compare_fwa_records(external.primary_points, external.secondary_points))
        lines.append("")

    lines.append("CC STATUS LOOKUP")
    if external is not None and external.primary_cc is not None:
        lines.extend(render_cc_section(external.primary_cc, heading="Primary clan CC lookup"))
    else:
        lines.append("Primary clan CC lookup unavailable.")
    lines.append("")
    if external is not None and external.secondary_tag is not None:
        if external.secondary_cc is not None:
            lines.extend(render_cc_section(external.secondary_cc, heading="Opponent clan CC lookup"))
        else:
            lines.append("Opponent clan CC lookup unavailable.")
        lines.append("")

    lines.append("SOURCE AGREEMENT")
    lines.extend(_format_source_agreement(clan, external, war_history))
    lines.append("")

    lines.append("FWA MATCHMAKING NOTES")
    lines.extend(render_fwa_guide_section())
    lines.append("")

    lines.extend(_format_war_focus_section(clan, external, war_history, members_by_tag))
    lines.append("")

    lines.append("FULL MEMBER DETAILS")
    stats_members_by_tag = {
        member.tag: member
        for member in (external.primary_stats.members if external is not None and external.primary_stats is not None else ())
    }
    for member in sorted(
        members,
        key=lambda item: (-item.town_hall, -item.trophies, item.name.lower(), item.tag),
    ):
        lines.extend(_format_player_detail(member, stats_members_by_tag.get(member.tag)))
        lines.append("")

    lines.append("LIMITATIONS")
    lines.append(
        "The public Clash of Clans API does not expose actual base-layout exports, trap placement, hidden war weight, or opponent scouting notes."
    )
    lines.append(
        "Player house cosmetic elements are included only as a count/list of cosmetic slots, not as a true layout export."
    )

    return "\n".join(lines).rstrip() + "\n"


def _format_source_agreement(
    clan: coc.Clan,
    external: FwaExternalIntel | None,
    war_history: WarHistoryData,
) -> list[str]:
    lines: list[str] = []
    lines.append(f"Clan API name: {clan.name}")
    lines.append(f"Clan API tag: {clan.tag}")

    if external is None:
        lines.append("FWA source agreement unavailable because external data did not load.")
        return lines

    if external.primary_stats is not None:
        stats_name = _stats_clan_name(external.primary_stats)
        stats_tag = external.primary_stats.clan_tag
        lines.append(f"FWA Stats clan name: {stats_name}")
        lines.append(f"FWA Stats clan tag: {stats_tag}")
        lines.append(
            "Clan identity with FWA Stats: "
            + ("match" if _normalize_clan_tag(stats_tag) == _normalize_clan_tag(clan.tag) else "mismatch")
        )
    else:
        lines.append("FWA Stats clan identity unavailable.")

    if external.primary_points is not None:
        points_name = external.primary_points.clan_name or "n/a"
        points_tag = external.primary_points.clan_tag
        lines.append(f"points.fwafarm clan name: {points_name}")
        lines.append(f"points.fwafarm clan tag: {points_tag}")
        lines.append(
            "Clan identity with points.fwafarm: "
            + ("match" if _normalize_clan_tag(points_tag) == _normalize_clan_tag(clan.tag) else "mismatch")
        )
    else:
        lines.append("points.fwafarm clan identity unavailable.")

    if external.primary_stats is not None and external.primary_points is not None:
        stats_points = _stats_summary_int(external.primary_stats.summary, "points")
        points_points = external.primary_points.point_balance
        if stats_points is not None and points_points is not None:
            if stats_points == points_points:
                lines.append(f"Clan points agreement: tally at {stats_points}.")
            else:
                lines.append(
                    f"Clan points agreement: mismatch between FWA Stats ({stats_points}) and points.fwafarm ({points_points})."
                )
        else:
            lines.append("Clan points agreement unavailable because one source did not expose points.")

    current_war = war_history.current_war
    if _is_current_war_ongoing(current_war) and external.primary_stats is not None:
        stats_current = _select_current_stats_war(external.primary_stats)
        if stats_current is not None:
            current_lines = _compare_current_war_sources(current_war, stats_current)
            lines.append("Current war source agreement:")
            lines.extend(f"  - {line}" for line in current_lines)
        else:
            lines.append("Current war source agreement unavailable because FWA Stats did not expose an active war.")

    if war_history.war_log_entries:
        lines.append(f"CoC war log fetch: {war_history.war_log_status}")
    if war_history.current_war_error:
        lines.append(f"Current war fetch note: {war_history.current_war_error}")
    if war_history.war_log_error:
        lines.append(f"War log fetch note: {war_history.war_log_error}")

    return lines


def _format_war_focus_section(
    clan: coc.Clan,
    external: FwaExternalIntel | None,
    war_history: WarHistoryData,
    members_by_tag: dict[str, coc.Player],
) -> list[str]:
    lines: list[str] = []
    lines.append("WAR FOCUS")
    if war_history.focus == "ongoing":
        lines.extend(_format_ongoing_war_focus(clan, external, war_history, members_by_tag))
    else:
        lines.extend(_format_recent_war_focus(clan, external, war_history))
    return lines


def _format_ongoing_war_focus(
    clan: coc.Clan,
    external: FwaExternalIntel | None,
    war_history: WarHistoryData,
    members_by_tag: dict[str, coc.Player],
) -> list[str]:
    lines: list[str] = []
    current_war = war_history.current_war
    is_current_war_ongoing = _is_current_war_ongoing(current_war)
    if is_current_war_ongoing:
        lines.append("Ongoing war (Clash of Clans API):")
        lines.extend(_format_war_overview(current_war))
        lines.append("")
        lines.append("Ongoing war roster:")
        lines.extend(_format_war_roster(current_war, members_by_tag))
        lines.append("")
    else:
        lines.append("No ongoing war object was returned from the Clash of Clans API.")

    if external is not None and external.primary_stats is not None:
        stats_current = _select_current_stats_war(external.primary_stats)
        if stats_current is not None:
            lines.append("Ongoing war snapshot (FWA Stats primary):")
            lines.extend(_format_fwa_stats_war_block(stats_current, index=None, coc_entry=None))
        else:
            lines.append("FWA Stats did not expose an ongoing war snapshot.")
        lines.append("")

    recent_fallback = _select_recent_stats_wars(external.primary_stats if external is not None else None, limit=1)
    if not is_current_war_ongoing and recent_fallback:
        lines.append("Latest concluded fallback (FWA Stats):")
        lines.extend(_format_fwa_stats_war_block(recent_fallback[0], index=None, coc_entry=war_history.war_log_entries[0] if war_history.war_log_entries else None))
    elif not is_current_war_ongoing and war_history.war_log_entries:
        lines.append("Latest concluded fallback (Clash of Clans war log):")
        lines.extend(_format_coc_war_log_block(war_history.war_log_entries[0], index=None))
    return lines


def _format_recent_war_focus(
    clan: coc.Clan,
    external: FwaExternalIntel | None,
    war_history: WarHistoryData,
) -> list[str]:
    lines: list[str] = []
    if external is None or external.primary_stats is None:
        lines.append("Recent war history unavailable because FWA Stats did not load.")
        return lines

    recent_stats_wars = _select_recent_stats_wars(external.primary_stats, limit=10)
    if not recent_stats_wars:
        lines.append("No concluded wars were returned by FWA Stats.")
        return lines

    lines.append("Recent concluded wars (FWA Stats primary):")
    if any(war.result and war.result.lower() == "inwar" for war in external.primary_stats.wars):
        lines.append("The current ongoing war is excluded from this list so the focus stays on concluded wars.")

    coc_log_entries = list(war_history.war_log_entries[:10])
    for index, stats_war in enumerate(recent_stats_wars, start=1):
        coc_entry = coc_log_entries[index - 1] if index - 1 < len(coc_log_entries) else None
        lines.extend(_format_fwa_stats_war_block(stats_war, index=index, coc_entry=coc_entry))
        lines.append("")

    if war_history.war_log_entries:
        lines.append("Clash of Clans war log cross-check:")
        for index, coc_entry in enumerate(coc_log_entries, start=1):
            lines.extend(_format_coc_war_log_block(coc_entry, index=index))
            lines.append("")

    return lines


def _format_fwa_stats_war_block(
    record: Any,
    *,
    index: int | None,
    coc_entry: Any | None,
) -> list[str]:
    lines: list[str] = []
    heading_bits = []
    if index is not None:
        heading_bits.append(f"War {index}")
        heading_bits.append(_ordinal_suffix(index))
    else:
        heading_bits.append("War snapshot")
    if record.end_time:
        heading_bits.append(f"ended {record.end_time}")
    lines.append(" - ".join(heading_bits))
    lines.append(f"  Source: FWA Stats JSON")
    lines.append(f"  Result: {record.result or 'unknown'}")
    if record.search_time:
        lines.append(f"  Search time: {record.search_time}")
    if record.team_size is not None:
        lines.append(f"  Team size: {record.team_size}")
    lines.append(
        f"  Clan: {record.clan_name or 'unknown'} ({record.clan_tag or 'n/a'})"
    )
    if record.clan_level is not None:
        lines.append(f"  Clan level: {record.clan_level}")
    lines.append(
        f"  Clan score: stars {format_optional_int(record.clan_stars)}, destruction {format_optional_float(record.clan_destruction_percentage)}%, attacks {format_optional_int(record.clan_attacks)}, exp {format_optional_int(record.clan_exp_earned)}"
    )
    lines.append(
        f"  Opponent: {record.opponent_name or 'unknown'} ({record.opponent_tag or 'n/a'})"
    )
    if record.opponent_level is not None:
        lines.append(f"  Opponent level: {record.opponent_level}")
    lines.append(
        f"  Opponent score: stars {format_optional_int(record.opponent_stars)}, destruction {format_optional_float(record.opponent_destruction_percentage)}%"
    )
    lines.append(f"  Opponent info: {record.opponent_info or 'n/a'}")
    lines.append(f"  Synced: {format_yes_no(record.synced)}")
    lines.append(f"  Matched: {format_yes_no(record.matched)}")

    if coc_entry is not None:
        lines.append("  CoC war log cross-check:")
        lines.extend(f"    - {line}" for line in _compare_fwa_stats_to_coc_entry(record, coc_entry))
    return lines


def _format_coc_war_log_block(entry: Any, *, index: int | None) -> list[str]:
    lines: list[str] = []
    heading_bits = []
    if index is not None:
        heading_bits.append(f"War log {index}")
    else:
        heading_bits.append("War log snapshot")
    if getattr(entry, "end_time", None) is not None:
        end_time = getattr(entry.end_time, "time", None)
        if end_time is not None:
            heading_bits.append(f"ended {end_time.isoformat(sep=' ', timespec='minutes')}")
    lines.append(" - ".join(heading_bits))
    lines.append("  Source: Clash of Clans war log")
    lines.append(f"  Result: {getattr(entry, 'result', 'unknown')}")
    lines.append(f"  League entry: {yes_no(getattr(entry, 'is_league_entry', False))}")
    lines.append(f"  Team size: {getattr(entry, 'team_size', 'n/a')}")

    clan = getattr(entry, "clan", None)
    opponent = getattr(entry, "opponent", None)
    if clan is not None:
        lines.append(
            f"  Clan: {getattr(clan, 'name', 'unknown')} ({getattr(clan, 'tag', 'n/a')})"
        )
        lines.append(
            f"  Clan score: stars {getattr(clan, 'stars', 'n/a')}, destruction {getattr(clan, 'destruction', 'n/a')}%, attacks {getattr(clan, 'attacks_used', 'n/a')}/{getattr(clan, 'total_attacks', 'n/a')}"
        )
    if opponent is not None:
        lines.append(
            f"  Opponent: {getattr(opponent, 'name', 'unknown')} ({getattr(opponent, 'tag', 'n/a')})"
        )
        lines.append(
            f"  Opponent score: stars {getattr(opponent, 'stars', 'n/a')}, destruction {getattr(opponent, 'destruction', 'n/a')}%, attacks {getattr(opponent, 'attacks_used', 'n/a')}/{getattr(opponent, 'total_attacks', 'n/a')}"
        )
    return lines


def _compare_fwa_stats_to_coc_entry(stats_record: Any, coc_entry: Any) -> list[str]:
    lines: list[str] = []
    comparisons: list[tuple[str, Any, Any]] = [
        ("result", stats_record.result, getattr(coc_entry, "result", None)),
        ("team size", stats_record.team_size, getattr(coc_entry, "team_size", None)),
        ("clan tag", stats_record.clan_tag, getattr(getattr(coc_entry, "clan", None), "tag", None)),
        ("opponent tag", stats_record.opponent_tag, getattr(getattr(coc_entry, "opponent", None), "tag", None)),
    ]
    matched = 0
    total = 0
    for label, left, right in comparisons:
        if left is None or right is None:
            continue
        total += 1
        if str(left).strip().lower() == str(right).strip().lower():
            matched += 1
            lines.append(f"{label}: match")
        else:
            lines.append(f"{label}: mismatch ({left} vs {right})")
    if total:
        lines.append(f"overall: {matched}/{total} fields matched")
    else:
        lines.append("overall: not enough shared fields to judge")
    return lines


def _compare_current_war_sources(current_war: coc.ClanWar, stats_record: Any) -> list[str]:
    lines: list[str] = []
    comparisons: list[tuple[str, Any, Any]] = [
        ("state", current_war.state, getattr(stats_record, "result", None)),
        ("team size", current_war.team_size, getattr(stats_record, "team_size", None)),
        ("clan tag", current_war.clan.tag, getattr(stats_record, "clan_tag", None)),
        ("opponent tag", current_war.opponent.tag, getattr(stats_record, "opponent_tag", None)),
        ("clan stars", current_war.clan.stars, getattr(stats_record, "clan_stars", None)),
        ("opponent stars", current_war.opponent.stars, getattr(stats_record, "opponent_stars", None)),
    ]
    matched = 0
    total = 0
    for label, left, right in comparisons:
        if left is None or right is None:
            continue
        total += 1
        if str(left).strip().lower() == str(right).strip().lower():
            matched += 1
            lines.append(f"{label}: match")
        else:
            lines.append(f"{label}: mismatch ({left} vs {right})")
    if total:
        lines.append(f"overall: {matched}/{total} fields matched")
    else:
        lines.append("overall: not enough shared fields to judge")
    return lines


def _select_secondary_tag(
    current_war: coc.ClanWar | None,
    war_log_entries: tuple[Any, ...],
) -> str | None:
    if current_war is not None and getattr(current_war, "opponent", None) is not None:
        opponent = current_war.opponent
        opponent_tag = getattr(opponent, "tag", None)
        if isinstance(opponent_tag, str) and opponent_tag.strip():
            return opponent_tag

    if war_log_entries:
        first = war_log_entries[0]
        opponent = getattr(first, "opponent", None)
        opponent_tag = getattr(opponent, "tag", None)
        if isinstance(opponent_tag, str) and opponent_tag.strip():
            return opponent_tag

    return None


def _is_current_war_ongoing(current_war: coc.ClanWar | None) -> bool:
    if current_war is None:
        return False
    state = getattr(current_war.state, "name", current_war.state)
    return str(state).strip().lower() == "inwar"


async def _safe_get_current_war(client: coc.Client, clan_tag: str) -> tuple[coc.ClanWar | None, str, str | None]:
    try:
        war = await client.get_current_war(clan_tag)
        if war is None:
            return None, "not_found", "No current war returned."
        return war, "ok", None
    except coc.PrivateWarLog:
        return None, "private", "private_war_log"
    except Exception as exc:
        LOGGER.exception("Failed to fetch current war for %s", clan_tag)
        return None, "request_failed", f"{type(exc).__name__}: {exc}"


async def _safe_get_war_log(client: coc.Client, clan_tag: str, limit: int) -> tuple[tuple[Any, ...], str, str | None]:
    try:
        war_log = await client.get_war_log(clan_tag, limit=limit)
        return tuple(war_log), "ok", None
    except coc.PrivateWarLog:
        return tuple(), "private", "private_war_log"
    except Exception as exc:
        LOGGER.exception("Failed to fetch war log for %s", clan_tag)
        return tuple(), "request_failed", f"{type(exc).__name__}: {exc}"


def _normalize_war_focus(raw_focus: WarFocus) -> WarFocus:
    focus = raw_focus.strip().lower() if isinstance(raw_focus, str) else "ongoing"
    if focus not in {"ongoing", "recent"}:
        raise ValueError("War focus must be either 'ongoing' or 'recent'.")
    return cast(WarFocus, focus)


def _select_current_stats_war(record: FwaStatsClanRecord) -> FwaStatsWarRecord | None:
    for war in record.wars:
        if war.result and war.result.lower() == "inwar":
            return war
    return record.wars[0] if record.wars else None


def _select_recent_stats_wars(record: FwaStatsClanRecord | None, limit: int) -> list[FwaStatsWarRecord]:
    if record is None:
        return []
    wars = [war for war in record.wars if not war.result or war.result.lower() != "inwar"]
    return wars[:limit]


def _ordinal_suffix(index: int) -> str:
    if 10 <= index % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(index % 10, "th")
    return f"{index}{suffix}"


def _format_clan_overview(clan: coc.Clan) -> list[str]:
    return [
        f"Name: {clan.name}",
        f"Tag: {clan.tag}",
        f"Share link: {clan.share_link}",
        f"Level: {clan.level}",
        f"Type: {clan.type}",
        f"Family friendly: {clan.family_friendly}",
        f"Description: {clan.description}",
        f"Location: {getattr(clan.location, 'name', str(clan.location))}",
        f"Points: {clan.points}",
        f"Builder base points: {clan.builder_base_points}",
        f"Capital points: {clan.capital_points}",
        f"Required trophies: {clan.required_trophies}",
        f"Required builder base trophies: {clan.required_builder_base_trophies}",
        f"Required town hall: {clan.required_townhall}",
        f"War frequency: {clan.war_frequency}",
        f"War win streak: {clan.war_win_streak}",
        f"War wins: {format_optional_int(clan.war_wins)}",
        f"War ties: {format_optional_int(clan.war_ties)}",
        f"War losses: {format_optional_int(clan.war_losses)}",
        f"Public war log: {clan.public_war_log}",
        f"Members: {clan.member_count}",
    ]


def _format_capital_and_labels(clan: coc.Clan) -> list[str]:
    lines = [
        f"Capital league: {getattr(clan.capital_league, 'name', str(clan.capital_league))}",
        f"War league: {getattr(clan.war_league, 'name', str(clan.war_league))}",
    ]

    if clan.labels:
        lines.append(f"Labels: {', '.join(getattr(label, 'name', str(label)) for label in clan.labels)}")
    else:
        lines.append("Labels: none")

    if clan.capital_districts:
        lines.append("Capital districts:")
        for district in clan.capital_districts:
            lines.append(
                f"  - {district.name} (hall level {district.hall_level}, district id {district.id})"
            )
    else:
        lines.append("Capital districts: none")

    return lines


def _format_war_overview(war: coc.ClanWar) -> list[str]:
    our_clan = war.clan
    opponent = war.opponent
    lines = [
        f"State: {war.state}",
        f"Status: {war.status}",
        f"Type: {war.type}",
        f"CWL: {war.is_cwl}",
        f"Team size: {war.team_size}",
        f"Attacks per member: {war.attacks_per_member}",
        f"Preparation start: {war.preparation_start_time}",
        f"Battle start: {war.start_time}",
        f"Battle end: {war.end_time}",
        "",
        f"Our clan: {our_clan.name} (level {our_clan.level})",
        f"Our stars: {our_clan.stars}/{our_clan.max_stars}",
        f"Our destruction: {our_clan.destruction:.1f}%",
        f"Our attacks used: {our_clan.attacks_used}/{our_clan.total_attacks}",
        f"Our average attack duration: {our_clan.average_attack_duration}s",
        f"Our exp earned: {our_clan.exp_earned}",
        "",
        f"Opponent clan: {opponent.name} (level {opponent.level})",
        f"Opponent stars: {opponent.stars}/{opponent.max_stars}",
        f"Opponent destruction: {opponent.destruction:.1f}%",
        f"Opponent attacks used: {opponent.attacks_used}/{opponent.total_attacks}",
        f"Opponent average attack duration: {opponent.average_attack_duration}s",
        "",
        f"Total attacks in war object: {len(war.attacks)}",
        f"Fresh attacks by our clan: {sum(1 for attack in our_clan.attacks if attack.is_fresh_attack)}",
        f"Three-star hits by our clan: {sum(1 for attack in our_clan.attacks if attack.stars == 3)}",
    ]

    top_attacks = sorted(
        our_clan.attacks,
        key=lambda attack: (-attack.stars, -attack.destruction, attack.order),
    )[:5]
    if top_attacks:
        lines.append("")
        lines.append("Top attacks:")
        for attack in top_attacks:
            defender = attack.defender
            lines.append(
                f"  - #{attack.order} vs {defender.name} (TH {defender.town_hall}, pos {defender.map_position}) -> {attack.stars} stars, {attack.destruction:.1f}%"
            )

    top_defenses = sorted(
        our_clan.members,
        key=lambda member: (-member.defense_count, member.map_position, member.name.lower()),
    )[:5]
    if top_defenses:
        lines.append("")
        lines.append("Most-hit bases:")
        for member in top_defenses:
            best_attack = _safe_best_opponent_attack(member)
            best_attack_text = "n/a"
            if best_attack is not None:
                best_attack_text = f"{best_attack.stars} stars, {best_attack.destruction:.1f}%"
            lines.append(
                f"  - #{member.map_position} {member.name} (TH {member.town_hall}) -> {member.defense_count} hits, best defense {best_attack_text}"
            )

    return lines


def _format_war_roster(war: coc.ClanWar, members_by_tag: dict[str, coc.Player]) -> list[str]:
    lines: list[str] = []
    war_member_tags = {member.tag for member in war.clan.members}

    lines.append("War participants:")
    for member in sorted(war.clan.members, key=lambda item: item.map_position):
        player = members_by_tag.get(member.tag)
        lines.extend(_format_war_member(member, player))
        lines.append("")

    bench_members = [
        member
        for member in members_by_tag.values()
        if member.tag not in war_member_tags
    ]
    if bench_members:
        lines.append("Not in current war:")
        for member in sorted(
            bench_members,
            key=lambda item: (-item.town_hall, -item.trophies, item.name.lower(), item.tag),
        ):
            lines.append(
                f"  - {member.name} (TH {member.town_hall}, trophies {member.trophies}, role {member.role}, opt-in {yes_no(getattr(member, 'war_opted_in', False))})"
            )

    return lines


def _format_war_member(member: coc.ClanWarMember, player: coc.Player | None) -> list[str]:
    name = player.name if player is not None else member.name
    role = str(player.role) if player is not None else "unknown"
    trophies = player.trophies if player is not None else "n/a"
    builder_trophies = player.builder_base_trophies if player is not None else "n/a"
    war_opted_in = yes_no(getattr(player, "war_opted_in", False)) if player is not None else "unknown"

    lines = [
        f"[#{member.map_position}] {name} ({member.tag})",
        f"  Clan role: {role}",
        f"  Town hall: {member.town_hall}",
        f"  Clan rank: {getattr(player, 'clan_rank', 'n/a')}",
        f"  Trophies: {trophies}",
        f"  Builder base trophies: {builder_trophies}",
        f"  War opted in: {war_opted_in}",
        f"  Defense count: {member.defense_count}",
        f"  Star count: {member.star_count}",
        f"  Attacks made: {len(member.attacks)}",
        f"  Best defense: {_format_best_attack(member.best_opponent_attack)}",
    ]

    if member.attacks:
        lines.append("  Attacks:")
        for attack in member.attacks:
            defender = attack.defender
            lines.append(
                f"    - #{attack.order} vs {defender.name} (TH {defender.town_hall}, pos {defender.map_position}) -> {attack.stars} stars, {attack.destruction:.1f}%"
            )

    if member.defenses:
        lines.append("  Defenses:")
        for attack in member.defenses:
            attacker = attack.attacker
            lines.append(
                f"    - #{attack.order} from {attacker.name} (TH {attacker.town_hall}, pos {attacker.map_position}) -> {attack.stars} stars, {attack.destruction:.1f}%"
            )

    return lines


def _format_player_detail(player: coc.Player, fwa_stats_member: Any | None = None) -> list[str]:
    lines = [
        f"[{player.town_hall}] {player.name} ({player.tag})",
        f"  Share link: {player.share_link}",
        f"  Role: {player.role}",
        f"  Experience level: {player.exp_level}",
        f"  League: {player.league}",
        f"  Builder league: {player.builder_base_league}",
        f"  Trophies: {player.trophies}",
        f"  Builder base trophies: {player.builder_base_trophies}",
        f"  Clan rank: {player.clan_rank}",
        f"  Previous clan rank: {player.clan_previous_rank}",
        f"  Builder base rank: {player.builder_base_rank}",
        f"  Donations: {player.donations}",
        f"  Received: {player.received}",
        f"  Attack wins: {player.attack_wins}",
        f"  Defense wins: {player.defense_wins}",
        f"  War stars: {player.war_stars}",
        f"  War opted in: {yes_no(getattr(player, 'war_opted_in', False))}",
        f"  Best trophies: {getattr(player, 'best_trophies', 'n/a')}",
        f"  Best builder base trophies: {getattr(player, 'best_builder_base_trophies', 'n/a')}",
        f"  Builder hall: {getattr(player, 'builder_hall', 'n/a')}",
        f"  Town hall weapon: {getattr(player, 'town_hall_weapon', 'n/a')}",
        f"  Clan capital contributions: {getattr(player, 'clan_capital_contributions', 'n/a')}",
    ]

    if fwa_stats_member is not None:
        lines.append(f"  FWA Stats weight: {format_optional_int(getattr(fwa_stats_member, 'weight', None))}")
        lines.append(f"  FWA Stats member rank: {format_optional_int(getattr(fwa_stats_member, 'rank', None))}")
        lines.append(f"  FWA Stats in war: {yes_no(getattr(fwa_stats_member, 'in_war', False))}")
        if getattr(fwa_stats_member, "league", None):
            lines.append(f"  FWA Stats league: {getattr(fwa_stats_member, 'league')}")

    legend_statistics = getattr(player, "legend_statistics", None)
    if legend_statistics is not None:
        lines.append(
            f"  Legend trophies: {getattr(legend_statistics, 'legend_trophies', 'n/a')}"
        )
        for season_name in (
            "current_season",
            "previous_season",
            "best_season",
            "previous_builder_base_season",
            "best_builder_base_season",
        ):
            season = getattr(legend_statistics, season_name, None)
            if season is not None:
                lines.append(f"  {season_name.replace('_', ' ').title()}: {season}")

    lines.append(f"  Player house elements: {len(player.player_house_elements)}")
    cosmetic_types = [
        str(getattr(element, "type", element))
        for element in player.player_house_elements
        if getattr(element, "type", None) is not None
    ]
    if cosmetic_types:
        lines.append(f"    Types: {', '.join(cosmetic_types)}")

    lines.append(f"  Heroes: {_format_game_object_list(player.heroes)}")
    lines.append(f"  Home troops: {_format_game_object_list(player.home_troops)}")
    lines.append(f"  Builder troops: {_format_game_object_list(player.builder_troops)}")
    lines.append(f"  Spells: {_format_game_object_list(player.spells)}")
    lines.append(f"  Pets: {_format_game_object_list(player.pets)}")
    lines.append(f"  Equipment: {_format_game_object_list(player.equipment)}")
    lines.append(f"  Super troops: {_format_game_object_list(player.super_troops)}")

    return lines


def _format_game_object_list(items: list[Any]) -> str:
    if not items:
        return "none"
    return ", ".join(_format_game_object(item) for item in items)


def _format_game_object(item: Any) -> str:
    name = getattr(item, "name", None) or getattr(item, "internal_name", None) or str(item)
    level = getattr(item, "level", None)
    max_level = getattr(item, "max_level", None)

    if level is None:
        return name
    if max_level is None:
        return f"{name} {level}"
    return f"{name} {level}/{max_level}"


def _format_best_attack(attack: Any) -> str:
    if attack is None:
        return "n/a"
    return f"{attack.stars} stars, {attack.destruction:.1f}%"


def _safe_best_opponent_attack(member: coc.ClanWarMember) -> Any:
    try:
        return member.best_opponent_attack
    except Exception:
        return None


def format_optional_int(value: int | None) -> str:
    if value is None or value < 0:
        return "n/a"
    return str(value)


def yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def _normalize_clan_tag(raw_tag: str) -> str:
    cleaned = raw_tag.strip().upper().replace(" ", "")
    if not cleaned:
        raise ValueError("Clan tag cannot be empty.")
    if not cleaned.startswith("#"):
        cleaned = f"#{cleaned}"
    return cleaned


def _sanitize_filename(raw_tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", raw_tag).strip("_").lower() or "clan"
