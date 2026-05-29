from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import coc
import discord

from .resolver import normalize_tag

if TYPE_CHECKING:
    from .main import PakFwaBot


LOGGER = logging.getLogger(__name__)

PAGE_LABELS = {
    "overview": "Overview",
    "members": "Members",
    "discord": "Discord",
    "activity": "Activity",
    "donations": "Donations",
    "capital": "Capital",
    "clan_games": "Clan Games",
    "war": "War/FWA",
}
PAGE_SORTS = {
    "members": ("th", "role", "trophies", "name"),
    "discord": ("linked", "name"),
    "activity": ("last_seen", "score", "name"),
    "donations": ("donated", "received", "ratio", "name"),
    "capital": ("loot", "attacks", "name"),
    "clan_games": ("points", "name"),
}
DEFAULT_PAGE = "overview"
TABLE_LIMIT = 3400


class DashboardPageSelect(discord.ui.Select):
    def __init__(self, view: "ClanDashboardView") -> None:
        options = [
            discord.SelectOption(label=label, value=key, default=key == view.page)
            for key, label in PAGE_LABELS.items()
        ]
        super().__init__(
            placeholder="Dashboard page",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"clan_dashboard:{view.dashboard_id}:page",
            row=0,
        )
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.change_state(interaction, page=self.values[0])


class DashboardClanSelect(discord.ui.Select):
    def __init__(self, view: "ClanDashboardView") -> None:
        options = [
            discord.SelectOption(label=tag, value=tag, default=tag == view.clan_tag)
            for tag in view.selected_clan_tags[:25]
        ]
        super().__init__(
            placeholder="Dashboard clan",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"clan_dashboard:{view.dashboard_id}:clan",
            row=1,
        )
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.change_state(interaction, clan_tag=self.values[0])


class DashboardRefreshButton(discord.ui.Button):
    def __init__(self, view: "ClanDashboardView") -> None:
        super().__init__(
            label="Refresh",
            style=discord.ButtonStyle.success,
            custom_id=f"clan_dashboard:{view.dashboard_id}:refresh",
            row=2,
        )
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.change_state(interaction, force_refresh=True)


class DashboardSortButton(discord.ui.Button):
    def __init__(self, view: "ClanDashboardView") -> None:
        super().__init__(
            label=f"Sort: {view.sort_key or 'default'}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"clan_dashboard:{view.dashboard_id}:sort",
            row=2,
        )
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.change_state(interaction, sort_key=next_sort(self._view_ref.page, self._view_ref.sort_key))


class ClanDashboardView(discord.ui.View):
    def __init__(
        self,
        bot: PakFwaBot,
        dashboard: dict[str, Any],
        *,
        page: str | None = None,
        clan_tag: str | None = None,
        sort_key: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.dashboard = dashboard
        self.dashboard_id = int(dashboard["id"])
        self.selected_clan_tags = normalize_tag_list(dashboard.get("selected_clan_tags") or [dashboard["default_clan_tag"]])
        self.page = normalize_page(page or dashboard.get("current_page") or dashboard["default_page"])
        self.clan_tag = normalize_dashboard_clan(
            clan_tag or dashboard.get("current_clan_tag") or dashboard["default_clan_tag"],
            self.selected_clan_tags,
        )
        self.sort_key = sort_key if sort_key in PAGE_SORTS.get(self.page, ()) else dashboard.get("current_sort")
        self._build_items()

    def _build_items(self) -> None:
        if not self.dashboard.get("show_public_controls", True):
            return
        self.add_item(DashboardPageSelect(self))
        if len(self.selected_clan_tags) > 1:
            self.add_item(DashboardClanSelect(self))
        if self.page in PAGE_SORTS:
            self.add_item(DashboardSortButton(self))
        self.add_item(DashboardRefreshButton(self))

    async def change_state(
        self,
        interaction: discord.Interaction,
        *,
        page: str | None = None,
        clan_tag: str | None = None,
        sort_key: str | None = None,
        force_refresh: bool = False,
    ) -> None:
        next_page = normalize_page(page or self.page)
        next_clan = normalize_dashboard_clan(clan_tag or self.clan_tag, self.selected_clan_tags)
        if sort_key is None and next_page != self.page:
            next_sort = None
        else:
            next_sort = sort_key if sort_key in PAGE_SORTS.get(next_page, ()) else self.sort_key

        dashboard = await self.bot.state.database.update_clan_dashboard_view_state(
            dashboard_id=self.dashboard_id,
            current_clan_tag=next_clan,
            current_page=next_page,
            current_sort=next_sort,
            mark_interaction=True,
        )
        if dashboard is None:
            await interaction.response.send_message("This clan dashboard is no longer active.", ephemeral=True)
            return

        await interaction.response.defer()
        embed = await build_dashboard_embed(self.bot, dashboard, clan_tag=next_clan, page=next_page, sort_key=next_sort)
        view = ClanDashboardView(self.bot, dashboard, page=next_page, clan_tag=next_clan, sort_key=next_sort)
        await interaction.edit_original_response(embed=embed, view=view)


async def run_clan_dashboard_loop(bot: PakFwaBot) -> None:
    interval = bot.state.config.clan_dashboard_refresh_seconds
    LOGGER.info("Clan dashboard loop started with %ss interval.", interval)
    await rehydrate_clan_dashboards(bot)
    while not bot.is_closed():
        try:
            await refresh_clan_dashboards(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Clan dashboard refresh failed.")
        await asyncio.sleep(interval)


async def run_clan_activity_loop(bot: PakFwaBot) -> None:
    interval = bot.state.config.clan_activity_poll_seconds
    LOGGER.info("Clan activity tracker loop started with %ss interval.", interval)
    while not bot.is_closed():
        try:
            await poll_clan_activity(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Clan activity poll failed.")
        await asyncio.sleep(interval)


async def rehydrate_clan_dashboards(bot: PakFwaBot) -> None:
    if not bot.state.database.connected:
        return
    dashboards = await bot.state.database.list_clan_dashboards()
    for dashboard in dashboards:
        message_id = dashboard.get("message_id")
        if message_id:
            bot.add_view(ClanDashboardView(bot, dashboard), message_id=int(message_id))


async def refresh_clan_dashboards(bot: PakFwaBot) -> None:
    if not bot.state.database.connected or not bot.state.coc_service.configured:
        return
    dashboards = await bot.state.database.list_clan_dashboards()
    for dashboard in dashboards:
        await ensure_dashboard_message(bot, dashboard)


async def ensure_dashboard_message(bot: PakFwaBot, dashboard: dict[str, Any]) -> None:
    channel = bot.get_channel(int(dashboard["channel_id"]))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(dashboard["channel_id"]))
        except (discord.Forbidden, discord.HTTPException, ValueError):
            LOGGER.warning("Could not fetch dashboard channel %s.", dashboard["channel_id"])
            return
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        LOGGER.warning("Dashboard target %s is not a text channel or thread.", dashboard["channel_id"])
        return

    page, clan_tag, sort_key = dashboard_state_for_refresh(dashboard)
    if page != dashboard.get("current_page") or clan_tag != dashboard.get("current_clan_tag"):
        updated = await bot.state.database.update_clan_dashboard_view_state(
            dashboard_id=dashboard["id"],
            current_clan_tag=clan_tag,
            current_page=page,
            current_sort=sort_key,
            mark_interaction=False,
        )
        if updated is not None:
            dashboard = updated

    embed = await build_dashboard_embed(bot, dashboard, clan_tag=clan_tag, page=page, sort_key=sort_key)
    view = ClanDashboardView(bot, dashboard, page=page, clan_tag=clan_tag, sort_key=sort_key)
    message = None
    if dashboard.get("message_id"):
        try:
            message = await channel.fetch_message(int(dashboard["message_id"]))
            await message.edit(embed=embed, view=view)
        except discord.NotFound:
            message = None
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not edit dashboard message %s.", dashboard.get("message_id"))
            return
    if message is None:
        try:
            message = await channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not send clan dashboard to channel %s.", dashboard["channel_id"])
            return
        dashboard = await bot.state.database.update_clan_dashboard_message_id(
            dashboard_id=dashboard["id"],
            message_id=message.id,
        ) or dashboard
        bot.add_view(ClanDashboardView(bot, dashboard), message_id=message.id)
    await bot.state.database.mark_clan_dashboard_refreshed(dashboard_id=dashboard["id"])


async def build_dashboard_embed(
    bot: PakFwaBot,
    dashboard: dict[str, Any],
    *,
    clan_tag: str,
    page: str,
    sort_key: str | None,
) -> discord.Embed:
    try:
        client = await bot.state.coc_service.get_client()
        clan = await client.get_clan(clan_tag)
    except (coc.NotFound, ValueError) as exc:
        embed = discord.Embed(title="Clan Dashboard", description=f"Could not load `{clan_tag}`: {exc}", color=discord.Color.red())
        return embed
    except Exception as exc:
        embed = discord.Embed(title="Clan Dashboard", description=f"Clash API refresh failed: {exc}", color=discord.Color.red())
        return embed

    members = member_rows(clan)
    if page == "overview":
        embed = await overview_embed(bot, clan, members)
    elif page == "members":
        embed = table_embed(clan, "Members", members_page(members, sort_key))
    elif page == "discord":
        embed = await discord_page(bot, dashboard, clan, members, sort_key)
    elif page == "activity":
        embed = await activity_page(bot, clan, members, sort_key)
    elif page == "donations":
        embed = table_embed(clan, "Donations", donations_page(members, sort_key))
    elif page == "capital":
        embed = table_embed(clan, "Capital", capital_page(bot, members, sort_key))
    elif page == "clan_games":
        embed = await clan_games_page(bot, clan, members, sort_key)
    else:
        embed = await war_page(bot, clan)
    embed.set_footer(text=f"{PAGE_LABELS[page]} | Estimated activity is based on API polling, not live online presence.")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


async def overview_embed(bot: PakFwaBot, clan: Any, members: list[dict[str, Any]]) -> discord.Embed:
    tag = getattr(clan, "tag", "unknown")
    embed = discord.Embed(
        title=f"{getattr(clan, 'name', 'Clan')} ({tag})",
        description=(getattr(clan, "description", None) or "Persistent clan dashboard."),
        color=discord.Color.blurple(),
        url=f"https://link.clashofclans.com/en?action=OpenClanProfile&tag={tag.replace('#', '%23')}",
    )
    badge = getattr(getattr(clan, "badge", None), "url", None)
    if badge:
        embed.set_thumbnail(url=badge)
    leaders = ", ".join(member["name"] for member in members if member["role_key"] == "leader") or "Unknown"
    th_counts = sorted(town_hall_counts(members).items(), reverse=True)
    donated = sum(member["donations"] for member in members)
    received = sum(member["received"] for member in members)
    embed.add_field(name="Clan", value=f"Level {getattr(clan, 'level', '?')} | {len(members)}/50 members\nLeader: {leaders}", inline=False)
    embed.add_field(
        name="Requirements",
        value=(
            f"Type: {getattr(clan, 'type', 'unknown')}\n"
            f"Trophies: {getattr(clan, 'required_trophies', getattr(clan, 'requiredTrophies', 'unknown'))}\n"
            f"TH: {getattr(clan, 'required_townhall', getattr(clan, 'required_townhall_level', 'any'))}"
        ),
        inline=True,
    )
    embed.add_field(
        name="War",
        value=(
            f"League: {league_name(getattr(clan, 'war_league', None))}\n"
            f"War log: {'public' if getattr(clan, 'public_war_log', False) else 'private'}\n"
            f"Wins: {getattr(clan, 'war_wins', getattr(clan, 'warWins', '?'))}"
        ),
        inline=True,
    )
    embed.add_field(name="Town Halls", value=", ".join(f"TH{th}: {count}" for th, count in th_counts[:12]) or "No members", inline=False)
    embed.add_field(name="Current Donations", value=f"Donated {donated:,} | Received {received:,}", inline=False)
    snapshot = await bot.state.database.get_war_snapshot(tag)
    if snapshot:
        embed.add_field(
            name="War/FWA Snapshot",
            value=(
                f"{snapshot.get('clan_name') or tag} vs {snapshot.get('opponent_name') or 'Unknown'}\n"
                f"State: {snapshot.get('state') or 'unknown'} | Plan: {snapshot.get('planned_result') or 'pending'}"
            ),
            inline=False,
        )
    return embed


async def discord_page(
    bot: PakFwaBot,
    dashboard: dict[str, Any],
    clan: Any,
    members: list[dict[str, Any]],
    sort_key: str | None,
) -> discord.Embed:
    links = {row["player_tag"]: row for row in await bot.state.database.list_player_links_by_tags([m["tag"] for m in members])}
    guild = bot.get_guild(int(dashboard["guild_id"]))
    rows = []
    for member in members:
        link = links.get(member["tag"])
        discord_name = "unlinked"
        if link:
            discord_member = guild.get_member(int(link["user_id"])) if guild is not None else None
            discord_name = discord_member.display_name if discord_member else f"user {link['user_id']}"
        rows.append({**member, "discord": discord_name, "linked": link is not None})
    if sort_key == "linked":
        rows.sort(key=lambda row: (not row["linked"], row["name"].lower()))
    else:
        rows.sort(key=lambda row: row["name"].lower())
    lines = ["TH  DISCORD              NAME", *[f"{row['th']:>2}  {row['discord'][:19]:<19}  {row['name'][:18]}" for row in rows]]
    return table_embed(clan, "Discord Links", lines)


async def activity_page(bot: PakFwaBot, clan: Any, members: list[dict[str, Any]], sort_key: str | None) -> discord.Embed:
    activity = {row["player_tag"]: row for row in await bot.state.database.get_latest_activity_by_clan(getattr(clan, "tag"))}
    rows = []
    for member in members:
        seen = activity.get(member["tag"])
        rows.append(
            {
                **member,
                "score": int(seen.get("score") or 0) if seen else 0,
                "last_seen_at": seen.get("last_seen_at") if seen else None,
            }
        )
    if sort_key == "score":
        rows.sort(key=lambda row: (-row["score"], row["name"].lower()))
    elif sort_key == "name":
        rows.sort(key=lambda row: row["name"].lower())
    else:
        rows.sort(
            key=lambda row: (
                row["last_seen_at"] is None,
                -(row["last_seen_at"].timestamp() if isinstance(row["last_seen_at"], datetime) else 0),
                row["name"].lower(),
            )
        )
    lines = [
        "TH  LAST OBSERVED     SCR  NAME",
        *[f"{row['th']:>2}  {relative_time(row['last_seen_at']):<15}  {row['score']:>3}  {row['name'][:18]}" for row in rows],
    ]
    return table_embed(clan, "Estimated Activity", lines)


async def clan_games_page(bot: PakFwaBot, clan: Any, members: list[dict[str, Any]], sort_key: str | None) -> discord.Embed:
    snapshots = {row["player_tag"]: row for row in await bot.state.database.list_clan_member_snapshots(getattr(clan, "tag"))}
    rows = []
    for member in members:
        snapshot = snapshots.get(member["tag"])
        rows.append({**member, "points": int(snapshot.get("clan_games_value") or 0) if snapshot else 0})
    if sort_key == "name":
        rows.sort(key=lambda row: row["name"].lower())
    else:
        rows.sort(key=lambda row: (-row["points"], row["name"].lower()))
    lines = ["TH  POINTS  NAME", *[f"{row['th']:>2}  {row['points']:>6}  {row['name'][:22]}" for row in rows]]
    embed = table_embed(clan, "Clan Games", lines)
    embed.add_field(name="Tracking note", value="Points come from the Games Champion achievement observed during polling.", inline=False)
    return embed


async def war_page(bot: PakFwaBot, clan: Any) -> discord.Embed:
    tag = getattr(clan, "tag")
    snapshot = await bot.state.database.get_war_snapshot(tag)
    embed = discord.Embed(title=f"War/FWA - {getattr(clan, 'name', tag)}", color=discord.Color.red())
    if not snapshot:
        embed.description = "No active war snapshot is stored yet."
        return embed
    embed.description = "\n".join(
        [
            f"Clan: {snapshot.get('clan_name') or tag}",
            f"Opponent: {snapshot.get('opponent_name') or 'Unknown'}",
            f"State: {snapshot.get('state') or 'unknown'}",
            f"Team size: {snapshot.get('team_size') or 'unknown'}",
            f"FWA classification: {snapshot.get('fwa_classification') or 'pending'}",
            f"Plan: {snapshot.get('planned_result') or 'pending'}",
        ]
    )
    return embed


def members_page(members: list[dict[str, Any]], sort_key: str | None) -> list[str]:
    rows = list(members)
    if sort_key == "role":
        rows.sort(key=lambda row: (-row["role_weight"], row["name"].lower()))
    elif sort_key == "trophies":
        rows.sort(key=lambda row: (-row["trophies"], row["name"].lower()))
    elif sort_key == "name":
        rows.sort(key=lambda row: row["name"].lower())
    else:
        rows.sort(key=lambda row: (-row["th"], -row["trophies"], row["name"].lower()))
    return [
        "TH  ROLE  TROPHY  WAR  NAME        TAG",
        *[
            f"{row['th']:>2}  {row['role']:<4}  {row['trophies']:>6}  {row['war']:<3}  {row['name'][:10]:<10}  {row['tag']}"
            for row in rows
        ],
    ]


def donations_page(members: list[dict[str, Any]], sort_key: str | None) -> list[str]:
    rows = list(members)
    if sort_key == "received":
        rows.sort(key=lambda row: (-row["received"], row["name"].lower()))
    elif sort_key == "ratio":
        rows.sort(key=lambda row: (-row["ratio"], row["name"].lower()))
    elif sort_key == "name":
        rows.sort(key=lambda row: row["name"].lower())
    else:
        rows.sort(key=lambda row: (-row["donations"], row["name"].lower()))
    return [
        "DON   REC   RATIO  NAME",
        *[f"{row['donations']:>5} {row['received']:>5} {row['ratio']:>6.2f}  {row['name'][:22]}" for row in rows],
    ]


def capital_page(bot: PakFwaBot, members: list[dict[str, Any]], sort_key: str | None) -> list[str]:
    rows = list(members)
    if sort_key == "attacks":
        rows.sort(key=lambda row: (-row["capital_attacks"], row["name"].lower()))
    elif sort_key == "name":
        rows.sort(key=lambda row: row["name"].lower())
    else:
        rows.sort(key=lambda row: (-row["capital_looted"], row["name"].lower()))
    return [
        "LOOTED  HITS  NAME",
        *[f"{row['capital_looted']:>6}  {row['capital_attacks']:>4}  {row['name'][:24]}" for row in rows],
    ]


def table_embed(clan: Any, title: str, lines: list[str]) -> discord.Embed:
    description = code_block(lines)
    return discord.Embed(
        title=f"{title} - {getattr(clan, 'name', 'Clan')} ({getattr(clan, 'tag', '')})",
        description=description,
        color=discord.Color.teal(),
    )


def code_block(lines: list[str]) -> str:
    body = "\n".join(lines)
    if len(body) > TABLE_LIMIT:
        body = body[: TABLE_LIMIT - 25].rstrip() + "\n... trimmed"
    return f"```text\n{body}\n```"


async def poll_clan_activity(bot: PakFwaBot) -> None:
    if not bot.state.database.connected or not bot.state.coc_service.configured:
        return
    clans = await bot.state.database.list_activity_clans()
    if not clans:
        return
    await bot.state.database.cleanup_clan_activity(retention_days=bot.state.config.clan_activity_retention_days)
    client = await bot.state.coc_service.get_client()
    for row in clans:
        try:
            clan_tag = normalize_tag(row["clan_tag"])
            clan = await client.get_clan(clan_tag)
        except (ValueError, coc.NotFound):
            continue
        except Exception:
            LOGGER.exception("Could not poll activity for %s.", row.get("clan_tag"))
            continue
        members = member_rows(clan)
        await bot.state.database.mark_missing_clan_members_left(
            clan_tag=clan.tag,
            current_player_tags=[member["tag"] for member in members],
        )
        for member in members:
            enriched = dict(member)
            try:
                player = await client.get_player(member["tag"])
                enriched.update(player_activity_fields(player))
            except (coc.NotFound, discord.HTTPException):
                pass
            except Exception:
                LOGGER.debug("Could not enrich player %s during activity poll.", member["tag"], exc_info=True)
            await bot.state.database.record_clan_member_snapshot(clan_tag=clan.tag, member=enriched)


def member_rows(clan: Any) -> list[dict[str, Any]]:
    members = getattr(clan, "members", None) or getattr(clan, "member_list", None) or getattr(clan, "memberList", None) or []
    rows = []
    for member in members:
        donated = int(getattr(member, "donations", 0) or 0)
        received = int(getattr(member, "donations_received", getattr(member, "donationsReceived", 0)) or 0)
        role_key = role_value(getattr(member, "role", "member"))
        row = {
            "player_tag": getattr(member, "tag", ""),
            "tag": getattr(member, "tag", ""),
            "player_name": getattr(member, "name", "Unknown"),
            "name": getattr(member, "name", "Unknown"),
            "town_hall_level": int(getattr(member, "town_hall", getattr(member, "town_hall_level", getattr(member, "townHallLevel", 0))) or 0),
            "th": int(getattr(member, "town_hall", getattr(member, "town_hall_level", getattr(member, "townHallLevel", 0))) or 0),
            "clan_role": role_key,
            "role_key": role_key,
            "role": role_label(role_key),
            "role_weight": role_weight(role_key),
            "trophies": int(getattr(member, "trophies", 0) or 0),
            "builder_base_trophies": int(getattr(member, "builder_base_trophies", getattr(member, "versus_trophies", 0)) or 0),
            "donations": donated,
            "donations_received": received,
            "received": received,
            "ratio": donated / received if received else float(donated),
            "war_preference": str(getattr(member, "war_preference", getattr(member, "warPreference", "")) or ""),
            "war": war_label(getattr(member, "war_preference", getattr(member, "warPreference", ""))),
            "clan_games_value": 0,
            "capital_looted": 0,
            "capital_attacks": 0,
            "raw": {},
        }
        rows.append(row)
    return rows


def player_activity_fields(player: Any) -> dict[str, Any]:
    games = 0
    for achievement in getattr(player, "achievements", []) or []:
        if getattr(achievement, "name", "") == "Games Champion":
            games = int(getattr(achievement, "value", 0) or 0)
            break
    return {
        "war_preference": str(getattr(player, "war_preference", getattr(player, "warPreference", "")) or ""),
        "clan_games_value": games,
        "raw": {"source": "player_poll"},
    }


def normalize_tag_list(tags: list[Any]) -> list[str]:
    normalized = []
    for tag in tags:
        try:
            value = normalize_tag(str(tag))
        except ValueError:
            continue
        if value not in normalized:
            normalized.append(value)
    return normalized


def normalize_page(page: str | None) -> str:
    page = (page or DEFAULT_PAGE).strip().lower().replace("-", "_")
    return page if page in PAGE_LABELS else DEFAULT_PAGE


def normalize_dashboard_clan(tag: str, selected_clan_tags: list[str]) -> str:
    try:
        normalized = normalize_tag(tag)
    except ValueError:
        normalized = selected_clan_tags[0]
    return normalized if normalized in selected_clan_tags else selected_clan_tags[0]


def next_sort(page: str, current: str | None) -> str | None:
    sorts = PAGE_SORTS.get(page, ())
    if not sorts:
        return None
    if current not in sorts:
        return sorts[0]
    return sorts[(sorts.index(current) + 1) % len(sorts)]


def dashboard_state_for_refresh(dashboard: dict[str, Any]) -> tuple[str, str, str | None]:
    page = normalize_page(dashboard.get("current_page") or dashboard["default_page"])
    clan_tag = normalize_dashboard_clan(
        dashboard.get("current_clan_tag") or dashboard["default_clan_tag"],
        normalize_tag_list(dashboard.get("selected_clan_tags") or [dashboard["default_clan_tag"]]),
    )
    sort_key = dashboard.get("current_sort")
    last_interaction = dashboard.get("last_interaction_at")
    if isinstance(last_interaction, datetime):
        elapsed = (datetime.now(timezone.utc) - last_interaction.astimezone(timezone.utc)).total_seconds() / 60
        if elapsed >= int(dashboard.get("reset_minutes") or 20):
            return normalize_page(dashboard["default_page"]), normalize_tag(dashboard["default_clan_tag"]), None
    return page, clan_tag, sort_key


def role_value(value: Any) -> str:
    raw = str(getattr(value, "name", value) or "member").lower().replace("-", "_")
    if raw in {"admin", "elder"}:
        return "elder"
    if raw in {"coleader", "co_leader", "co leader"}:
        return "co_leader"
    if raw == "leader":
        return "leader"
    return "member"


def role_label(value: str) -> str:
    return {"leader": "Lead", "co_leader": "Co", "elder": "Eld", "member": "Mem"}.get(value, "Mem")


def role_weight(value: str) -> int:
    return {"leader": 4, "co_leader": 3, "elder": 2, "member": 1}.get(value, 1)


def war_label(value: Any) -> str:
    raw = str(getattr(value, "name", value) or "").lower()
    return "in" if raw == "in" else "out" if raw == "out" else "-"


def town_hall_counts(members: list[dict[str, Any]]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for member in members:
        counts[member["th"]] = counts.get(member["th"], 0) + 1
    return counts


def league_name(value: Any) -> str:
    return str(getattr(value, "name", value) or "Unranked")


def relative_time(value: Any) -> str:
    if not isinstance(value, datetime):
        return "never"
    elapsed = datetime.now(timezone.utc) - value.astimezone(timezone.utc)
    minutes = int(elapsed.total_seconds() // 60)
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"
