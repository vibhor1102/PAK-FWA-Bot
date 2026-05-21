from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import logging
from io import BytesIO
import re
from typing import Any

import coc
import discord
from discord import app_commands

from ..coc_service import CocConfigurationError
from ..fwa_sources import (
    FwaExternalIntel,
    compare_fwa_records,
    compare_fwa_stats_records,
    render_fwa_stats_section,
    render_cc_section,
    render_points_section,
)


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ClanReportData:
    clan: coc.Clan
    war: coc.ClanWar | None
    members: list[coc.Player]
    external: FwaExternalIntel | None
    text: str


def build_clan_report_command() -> app_commands.Command[Any, ..., None]:
    @app_commands.describe(clan_tag="The clan tag to inspect, with or without the # prefix.")
    @app_commands.describe(
        comparison_tag="Optional second clan tag to compare against. If omitted, the current war opponent is used when available.",
    )
    async def clan_report_callback(
        interaction: discord.Interaction,
        clan_tag: str,
        comparison_tag: str | None = None,
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
            report = await build_clan_report(bot.state, clan_tag, comparison_tag)
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
        except coc.PrivateWarLog:
            await interaction.followup.send(
                "The clan exists, but the API blocked a war-log lookup. The rest of the clan report should still be available.",
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
        description="Generate a detailed Clash of Clans clan intelligence report.",
        callback=clan_report_callback,
    )


async def build_clan_report(state, clan_tag: str, comparison_tag: str | None = None) -> ClanReportData:
    client = await state.coc_service.get_client()
    normalized_tag = _normalize_clan_tag(clan_tag)
    clan = await client.get_clan(normalized_tag)

    detailed_members_task = asyncio.create_task(_collect_detailed_members(clan))
    current_war_task = asyncio.create_task(client.get_current_war(normalized_tag))
    members, war = await asyncio.gather(detailed_members_task, current_war_task)

    secondary_tag = comparison_tag or (war.opponent.tag if war is not None else None)
    external = await state.fwa_service.build_external_intel(
        normalized_tag,
        secondary_tag,
        stats_service=state.fwa_stats_service,
    )

    text = build_report_text(clan, war, members, external)
    return ClanReportData(clan=clan, war=war, members=members, external=external, text=text)


async def _collect_detailed_members(clan: coc.Clan) -> list[coc.Player]:
    members: list[coc.Player] = []
    async for member in clan.get_detailed_members():
        members.append(member)
    return members


def build_summary_embed(report: ClanReportData) -> discord.Embed:
    clan = report.clan
    war = report.war
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
            "Detailed clan, roster, and war intelligence pulled from the Clash of Clans API. "
            "Base layouts and true war weights are not exposed by the official API, so the report focuses on the strongest available proxies."
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
    war: coc.ClanWar | None,
    members: list[coc.Player],
    external: FwaExternalIntel | None,
) -> str:
    members_by_tag = {member.tag: member for member in members}
    lines: list[str] = []

    lines.append("CLAN REPORT")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    lines.append("CLAN OVERVIEW")
    lines.extend(_format_clan_overview(clan))
    lines.append("")

    lines.append("CAPITAL AND LABELS")
    lines.extend(_format_capital_and_labels(clan))
    lines.append("")

    lines.append("CURRENT WAR OVERVIEW")
    if war is None:
        lines.append("No current war data was returned, or the war log is private.")
    else:
        lines.extend(_format_war_overview(war))
    lines.append("")

    lines.append("WAR ROSTER")
    if war is None:
        lines.append("No war roster is available.")
    else:
        lines.extend(_format_war_roster(war, members_by_tag))
    lines.append("")

    if external is not None:
        lines.append("FWA POINTS DATABASE")
        if external.primary_points is not None:
            lines.extend(render_points_section(external.primary_points, heading="Primary clan point lookup"))
        else:
            lines.append("Primary clan point lookup unavailable.")
        lines.append("")

        if external.secondary_tag is not None:
            lines.append("OPPONENT POINTS DATABASE")
            if external.secondary_points is not None:
                lines.extend(render_points_section(external.secondary_points, heading="Opponent point lookup"))
            else:
                lines.append("Opponent point lookup unavailable.")
            lines.append("")

        lines.append("POINT COMPARISON")
        lines.extend(compare_fwa_records(external.primary_points, external.secondary_points))
        lines.append("")

        lines.append("CC STATUS LOOKUP")
        if external.primary_cc is not None:
            lines.extend(render_cc_section(external.primary_cc, heading="Primary clan CC lookup"))
        else:
            lines.append("Primary clan CC lookup unavailable.")
        lines.append("")
        if external.secondary_tag is not None:
            if external.secondary_cc is not None:
                lines.extend(render_cc_section(external.secondary_cc, heading="Opponent clan CC lookup"))
            else:
                lines.append("Opponent clan CC lookup unavailable.")
            lines.append("")

        lines.append("FWA STATS DATABASE")
        if external.primary_stats is not None:
            lines.extend(render_fwa_stats_section(external.primary_stats, heading="Primary clan FWA Stats lookup"))
        else:
            lines.append("Primary clan FWA Stats lookup unavailable.")
        lines.append("")

        if external.secondary_tag is not None:
            if external.secondary_stats is not None:
                lines.extend(render_fwa_stats_section(external.secondary_stats, heading="Opponent clan FWA Stats lookup"))
            else:
                lines.append("Opponent clan FWA Stats lookup unavailable.")
            lines.append("")

        lines.append("FWA STATS COMPARISON")
        lines.extend(compare_fwa_stats_records(external.primary_stats, external.secondary_stats))
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
        "The official Clash of Clans API does not expose actual base-layout exports, trap placement, hidden war weight, or opponent scouting notes."
    )
    lines.append(
        "Player house cosmetic elements are included only as a count/list of cosmetic slots, not as a true layout export."
    )

    return "\n".join(lines).rstrip() + "\n"


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
