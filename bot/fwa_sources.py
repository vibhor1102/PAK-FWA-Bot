from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import re
from typing import Any

import aiohttp
from bs4 import BeautifulSoup


POINTS_BASE_URL = "https://points.fwafarm.com"
CC_BASE_URL = "https://cc.fwafarm.com"
FWA_STATS_BASE_URL = "https://fwastats.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(slots=True)
class FwaPointHistoryEntry:
    timestamp: str
    war_label: str | None
    points_delta: int | None
    note: str | None


@dataclass(slots=True)
class FwaPointsRecord:
    clan_tag: str
    clan_name: str | None
    point_balance: int | None
    active_fwa: bool | None
    current_war_id: str | None
    current_war_sync: str | None
    current_war_state: str | None
    current_opponent_tag: str | None
    current_opponent_name: str | None
    current_war_note: str | None
    recent_history: tuple[FwaPointHistoryEntry, ...]
    source_status: str
    source_url: str
    error: str | None = None


@dataclass(slots=True)
class CcClanStatus:
    clan_tag: str
    source_url: str
    source_status: str
    title: str | None
    labels: dict[str, str]
    evidence: tuple[str, ...]
    error: str | None = None


@dataclass(slots=True)
class FwaExternalIntel:
    primary_tag: str
    secondary_tag: str | None
    primary_points: FwaPointsRecord | None
    secondary_points: FwaPointsRecord | None
    primary_cc: CcClanStatus | None
    secondary_cc: CcClanStatus | None
    primary_stats: "FwaStatsClanRecord" | None
    secondary_stats: "FwaStatsClanRecord" | None


@dataclass(slots=True)
class FwaStatsMemberRecord:
    tag: str
    name: str
    role: str | None
    level: int | None
    donated: int | None
    received: int | None
    rank: int | None
    trophies: int | None
    league: str | None
    town_hall: int | None
    weight: int | None
    in_war: bool | None


@dataclass(slots=True)
class FwaStatsWarRecord:
    end_time: str | None
    search_time: str | None
    result: str | None
    team_size: int | None
    clan_tag: str | None
    clan_name: str | None
    clan_level: int | None
    clan_stars: int | None
    clan_destruction_percentage: float | None
    clan_attacks: int | None
    clan_exp_earned: int | None
    opponent_tag: str | None
    opponent_name: str | None
    opponent_level: int | None
    opponent_stars: int | None
    opponent_destruction_percentage: float | None
    opponent_info: str | None
    synced: bool | None
    matched: bool | None


@dataclass(slots=True)
class FwaStatsClanRecord:
    clan_tag: str
    summary_source_url: str
    members_source_url: str
    wars_source_url: str
    source_status: str
    summary_status: str
    members_status: str
    wars_status: str
    summary: dict[str, Any] | None
    members: tuple[FwaStatsMemberRecord, ...]
    wars: tuple[FwaStatsWarRecord, ...]
    error: str | None = None


@dataclass(slots=True)
class FwaFarmService:
    _session: aiohttp.ClientSession | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def start(self) -> None:
        if self._session is not None:
            return

        async with self._lock:
            if self._session is not None:
                return

            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )

    async def close(self) -> None:
        if self._session is None:
            return

        await self._session.close()
        self._session = None

    async def build_external_intel(
        self,
        primary_tag: str,
        secondary_tag: str | None = None,
        *,
        stats_service: "FwaStatsService" | None = None,
    ) -> FwaExternalIntel:
        primary_tag = normalize_clan_tag(primary_tag)
        secondary_tag = normalize_clan_tag(secondary_tag) if secondary_tag else None
        if secondary_tag == primary_tag:
            secondary_tag = None

        await self.start()
        assert self._session is not None

        primary_points_task = asyncio.create_task(self.lookup_points(primary_tag))
        primary_cc_task = asyncio.create_task(self.lookup_cc(primary_tag))
        primary_stats_task = (
            asyncio.create_task(stats_service.build_clan_record(primary_tag))
            if stats_service is not None
            else None
        )
        primary_points = await primary_points_task
        primary_cc = await primary_cc_task
        primary_stats = await primary_stats_task if primary_stats_task is not None else None

        if secondary_tag is None and primary_points.current_opponent_tag:
            fallback_secondary = normalize_clan_tag(primary_points.current_opponent_tag)
            if fallback_secondary != primary_tag:
                secondary_tag = fallback_secondary

        secondary_points: FwaPointsRecord | None = None
        secondary_cc: CcClanStatus | None = None
        secondary_stats: FwaStatsClanRecord | None = None
        if secondary_tag is not None:
            secondary_points_task = asyncio.create_task(self.lookup_points(secondary_tag))
            secondary_cc_task = asyncio.create_task(self.lookup_cc(secondary_tag))
            secondary_stats_task = (
                asyncio.create_task(stats_service.build_clan_record(secondary_tag))
                if stats_service is not None
                else None
            )
            secondary_points = await secondary_points_task
            secondary_cc = await secondary_cc_task
            secondary_stats = await secondary_stats_task if secondary_stats_task is not None else None

        return FwaExternalIntel(
            primary_tag=primary_tag,
            secondary_tag=secondary_tag,
            primary_points=primary_points,
            secondary_points=secondary_points,
            primary_cc=primary_cc,
            secondary_cc=secondary_cc,
            primary_stats=primary_stats,
            secondary_stats=secondary_stats,
        )

    async def lookup_points(self, clan_tag: str) -> FwaPointsRecord:
        clan_tag = normalize_clan_tag(clan_tag)
        source_url = f"{POINTS_BASE_URL}/clan?tag={clan_tag.lstrip('#')}"

        session = await self._require_session()
        try:
            async with session.get(source_url) as response:
                html = await response.text()
                if response.status != 200:
                    return FwaPointsRecord(
                        clan_tag=clan_tag,
                        clan_name=None,
                        point_balance=None,
                        active_fwa=None,
                        current_war_id=None,
                        current_war_sync=None,
                        current_war_state=None,
                        current_opponent_tag=None,
                        current_opponent_name=None,
                        current_war_note=None,
                        recent_history=(),
                        source_status=f"http_{response.status}",
                        source_url=source_url,
                        error=html[:240].strip() or None,
                    )
        except Exception as exc:
            return FwaPointsRecord(
                clan_tag=clan_tag,
                clan_name=None,
                point_balance=None,
                active_fwa=None,
                current_war_id=None,
                current_war_sync=None,
                current_war_state=None,
                current_opponent_tag=None,
                current_opponent_name=None,
                current_war_note=None,
                recent_history=(),
                source_status="request_failed",
                source_url=source_url,
                error=type(exc).__name__,
            )

        return parse_points_html(html, clan_tag=clan_tag, source_url=source_url)

    async def lookup_cc(self, clan_tag: str) -> CcClanStatus:
        clan_tag = normalize_clan_tag(clan_tag)
        source_url = f"{CC_BASE_URL}/cc_n/clan.php?tag={clan_tag.lstrip('#')}"

        session = await self._require_session()
        try:
            async with session.get(source_url, allow_redirects=True) as response:
                html = await response.text()
                blocked = response.status == 403 or _looks_like_cloudflare(html)
                if blocked:
                    return CcClanStatus(
                        clan_tag=clan_tag,
                        source_url=source_url,
                        source_status="blocked_by_cloudflare",
                        title=_extract_title(html),
                        labels={},
                        evidence=tuple(_cloudflare_evidence(html)),
                        error=f"http_{response.status}",
                    )
                if response.status != 200:
                    return CcClanStatus(
                        clan_tag=clan_tag,
                        source_url=source_url,
                        source_status=f"http_{response.status}",
                        title=_extract_title(html),
                        labels={},
                        evidence=tuple(),
                        error=html[:240].strip() or None,
                    )
        except Exception as exc:
            return CcClanStatus(
                clan_tag=clan_tag,
                source_url=source_url,
                source_status="request_failed",
                title=None,
                labels={},
                evidence=tuple(),
                error=type(exc).__name__,
            )

        return parse_cc_html(html, clan_tag=clan_tag, source_url=source_url)

    async def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            await self.start()
        assert self._session is not None
        return self._session


@dataclass(slots=True)
class FwaStatsService:
    _session: aiohttp.ClientSession | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _clans_cache: tuple[dict[str, Any], ...] | None = field(default=None, init=False, repr=False)
    _clans_cache_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def start(self) -> None:
        if self._session is not None:
            return

        async with self._lock:
            if self._session is not None:
                return

            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )

    async def close(self) -> None:
        if self._session is None:
            return

        await self._session.close()
        self._session = None

    async def build_clan_record(self, clan_tag: str) -> FwaStatsClanRecord:
        clan_tag = normalize_clan_tag(clan_tag)
        await self.start()
        assert self._session is not None

        summary_url = f"{FWA_STATS_BASE_URL}/Clans.json"
        members_url = f"{FWA_STATS_BASE_URL}/Clan/{clan_tag.lstrip('#')}/Members.json"
        wars_url = f"{FWA_STATS_BASE_URL}/Clan/{clan_tag.lstrip('#')}/Wars.json"

        summary_task = asyncio.create_task(self._lookup_summary(clan_tag))
        members_task = asyncio.create_task(self._lookup_members(clan_tag))
        wars_task = asyncio.create_task(self._lookup_wars(clan_tag))
        summary, summary_status, summary_error = await summary_task
        members, members_status, members_error = await members_task
        wars, wars_status, wars_error = await wars_task

        statuses = [summary_status, members_status, wars_status]
        if all(status == "ok" for status in statuses):
            source_status = "ok"
        elif any(status == "ok" for status in statuses):
            source_status = "partial"
        else:
            source_status = "request_failed"

        errors = [item for item in (summary_error, members_error, wars_error) if item]
        error = "; ".join(errors) if errors else None

        if summary is None and wars:
            summary = {
                "tag": wars[0].clan_tag,
                "name": wars[0].clan_name,
                "level": wars[0].clan_level,
            }

        return FwaStatsClanRecord(
            clan_tag=clan_tag,
            summary_source_url=summary_url,
            members_source_url=members_url,
            wars_source_url=wars_url,
            source_status=source_status,
            summary_status=summary_status,
            members_status=members_status,
            wars_status=wars_status,
            summary=summary,
            members=members,
            wars=wars,
            error=error,
        )

    async def _lookup_summary(self, clan_tag: str) -> tuple[dict[str, Any] | None, str, str | None]:
        try:
            clans = await self._load_clans()
        except Exception as exc:
            return None, "request_failed", type(exc).__name__

        for clan in clans:
            if normalize_clan_tag(str(clan.get("tag", ""))) == clan_tag:
                return clan, "ok", None

        return None, "not_found", "Clan not found in FWA Stats clan list."

    async def _lookup_members(self, clan_tag: str) -> tuple[tuple[FwaStatsMemberRecord, ...], str, str | None]:
        source_url = f"{FWA_STATS_BASE_URL}/Clan/{clan_tag.lstrip('#')}/Members.json"
        return await self._fetch_member_records(source_url)

    async def _lookup_wars(self, clan_tag: str) -> tuple[tuple[FwaStatsWarRecord, ...], str, str | None]:
        source_url = f"{FWA_STATS_BASE_URL}/Clan/{clan_tag.lstrip('#')}/Wars.json"
        return await self._fetch_war_records(source_url)

    async def _load_clans(self) -> tuple[dict[str, Any], ...]:
        if self._clans_cache is not None:
            return self._clans_cache

        async with self._clans_cache_lock:
            if self._clans_cache is not None:
                return self._clans_cache

            payload, status, error = await self._fetch_json_array(f"{FWA_STATS_BASE_URL}/Clans.json")
            if payload is None:
                raise RuntimeError(error or status)
            self._clans_cache = tuple(payload)
            return self._clans_cache

    async def _fetch_member_records(self, source_url: str) -> tuple[tuple[FwaStatsMemberRecord, ...], str, str | None]:
        payload, status, error = await self._fetch_json_array(source_url)
        if payload is None:
            return tuple(), status, error
        records = tuple(_parse_fwa_stats_member(item) for item in payload)
        return records, status, None

    async def _fetch_war_records(self, source_url: str) -> tuple[tuple[FwaStatsWarRecord, ...], str, str | None]:
        payload, status, error = await self._fetch_json_array(source_url)
        if payload is None:
            return tuple(), status, error
        records = tuple(_parse_fwa_stats_war(item) for item in payload)
        return records, status, None

    async def _fetch_json_array(self, source_url: str) -> tuple[list[dict[str, Any]] | None, str, str | None]:
        session = await self._require_session()
        try:
            async with session.get(source_url) as response:
                if response.status != 200:
                    body = await response.text()
                    return None, f"http_{response.status}", body[:240].strip() or None
                data = await response.json(content_type=None)
                if not isinstance(data, list):
                    return None, "invalid_payload", type(data).__name__
                normalized: list[dict[str, Any]] = []
                for item in data:
                    if isinstance(item, dict):
                        normalized.append(item)
                return normalized, "ok", None
        except Exception as exc:
            return None, "request_failed", type(exc).__name__

    async def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            await self.start()
        assert self._session is not None
        return self._session


def parse_points_html(html: str, *, clan_tag: str, source_url: str) -> FwaPointsRecord:
    soup = BeautifulSoup(html, "html.parser")
    title = _extract_title(html)
    body_text = normalize_whitespace(soup.get_text(" ", strip=True))

    if "Clan not found." in body_text or "Clan not found" in body_text:
        return FwaPointsRecord(
            clan_tag=clan_tag,
            clan_name=None,
            point_balance=None,
            active_fwa=None,
            current_war_id=None,
            current_war_sync=None,
            current_war_state=None,
            current_opponent_tag=None,
            current_opponent_name=None,
            current_war_note=None,
            recent_history=(),
            source_status="not_found",
            source_url=source_url,
            error="Clan not found.",
        )

    overview_text = _find_overview_text(soup)
    clan_name = _match_text(r"Clan Name\s*:\s*(.*?)\s+Clan Tag\s*:", overview_text)
    if clan_name is None:
        clan_name = _match_text(r"Viewing Clan\s+(.*?)(?:\s+Clan Tag\s*:|\s+Point Balance\s*:)", body_text)

    point_balance = _match_int(r"Point Balance\s*:\s*(-?\d+)", overview_text)
    active_fwa_raw = _match_text(r"Active FWA\s*:\s*(Yes|No)", overview_text)
    active_fwa = None
    if active_fwa_raw is not None:
        active_fwa = active_fwa_raw.lower() == "yes"

    winner_box = soup.select_one("p.winner-box")
    current_war_id = None
    current_war_sync = None
    current_opponent_name = None
    current_opponent_tag = None
    current_war_note = None
    if winner_box is not None:
        winner_text = normalize_whitespace(winner_box.get_text(" ", strip=True))
        winner_match = re.search(
            r"War\s+#(?P<war_id>\d+)\s+in\s+Sync\s+#(?P<sync>\d+)\s+"
            r"(?P<our_name>.+?)\s+\(\s*(?P<our_tag>[A-Z0-9]+)\s*\)\s+vs\.\s+"
            r"(?P<opp_name>.+?)\s+\(\s*(?P<opp_tag>[A-Z0-9]+)\s*\):\s*(?P<note>.+)",
            winner_text,
            flags=re.I,
        )
        if winner_match:
            current_war_id = winner_match.group("war_id")
            current_war_sync = winner_match.group("sync")
            current_opponent_name = winner_match.group("opp_name").strip()
            current_opponent_tag = winner_match.group("opp_tag").strip().upper()
            current_war_note = winner_match.group("note").strip().rstrip(".")
        else:
            current_war_note = winner_text or None

    current_box = soup.select_one("div.current-box")
    current_war_state = None
    if current_box is not None:
        current_war_state = _match_text(
            r"Last Known War State\s*:\s*([A-Za-z]+)",
            normalize_whitespace(current_box.get_text(" ", strip=True)),
        )

    recent_history = _parse_history_entries(soup)

    return FwaPointsRecord(
        clan_tag=clan_tag,
        clan_name=clan_name,
        point_balance=point_balance,
        active_fwa=active_fwa,
        current_war_id=current_war_id,
        current_war_sync=current_war_sync,
        current_war_state=current_war_state,
        current_opponent_tag=current_opponent_tag,
        current_opponent_name=current_opponent_name,
        current_war_note=current_war_note,
        recent_history=recent_history,
        source_status="ok",
        source_url=source_url,
    )


def parse_cc_html(html: str, *, clan_tag: str, source_url: str) -> CcClanStatus:
    soup = BeautifulSoup(html, "html.parser")
    title = _extract_title(html)
    body_text = normalize_whitespace(soup.get_text(" ", strip=True))

    labels: dict[str, str] = {}
    evidence: list[str] = []
    for label, value in _extract_label_values(soup):
        normalized = _normalize_cc_label(label)
        if normalized in {"seen_by_system", "blacklisted", "former_fwa", "mismatch"}:
            labels[normalized] = value
            evidence.append(f"{label}: {value}")

    if not labels:
        for key, pattern in {
            "seen_by_system": r"(seen by system|seen by the system)",
            "blacklisted": r"(blacklisted|\bbl\b)",
            "former_fwa": r"(former fwa|former fwa clan)",
            "mismatch": r"(mismatch)",
        }.items():
            match = re.search(
                rf"{pattern}\s*[:\-]?\s*(yes|no|true|false|present|absent|unknown|bl|former|mismatch|available|not available)",
                body_text,
                flags=re.I,
            )
            if match:
                labels[key] = match.group(2)
                evidence.append(f"{match.group(1)}: {match.group(2)}")

    source_status = "ok" if labels else "ok_no_flags_found"
    if _looks_like_cloudflare(html):
        source_status = "blocked_by_cloudflare"

    return CcClanStatus(
        clan_tag=clan_tag,
        source_url=source_url,
        source_status=source_status,
        title=title,
        labels=labels,
        evidence=tuple(evidence),
        error=None if source_status.startswith("ok") else "cloudflare_challenge",
    )


def compare_fwa_records(primary: FwaPointsRecord | None, secondary: FwaPointsRecord | None) -> list[str]:
    lines: list[str] = []
    if primary is None or secondary is None:
        lines.append("Points comparison unavailable because one side could not be loaded.")
        return lines

    if primary.point_balance is None or secondary.point_balance is None:
        lines.append("Points comparison unavailable because one side did not expose a point balance.")
        return lines

    delta = primary.point_balance - secondary.point_balance
    if delta > 0:
        leader = primary.clan_name or primary.clan_tag
        lines.append(f"Points leader: {leader} by {delta} point(s).")
    elif delta < 0:
        leader = secondary.clan_name or secondary.clan_tag
        lines.append(f"Points leader: {leader} by {abs(delta)} point(s).")
    else:
        lines.append("Points comparison: tied.")

    lines.append(f"Primary points: {primary.point_balance}")
    lines.append(f"Secondary points: {secondary.point_balance}")
    lines.append(f"Primary active FWA: {format_yes_no(primary.active_fwa)}")
    lines.append(f"Secondary active FWA: {format_yes_no(secondary.active_fwa)}")

    if primary.current_war_note:
        lines.append(f"Primary win-calculator note: {primary.current_war_note}")
    if secondary.current_war_note:
        lines.append(f"Secondary win-calculator note: {secondary.current_war_note}")

    return lines


def compare_fwa_stats_records(
    primary: FwaStatsClanRecord | None,
    secondary: FwaStatsClanRecord | None,
) -> list[str]:
    lines: list[str] = []
    if primary is None or secondary is None:
        lines.append("FWA Stats comparison unavailable because one side could not be loaded.")
        return lines

    primary_points = _stats_summary_int(primary.summary, "points")
    secondary_points = _stats_summary_int(secondary.summary, "points")
    if primary_points is not None and secondary_points is not None:
        delta = primary_points - secondary_points
        if delta > 0:
            leader = _stats_clan_name(primary)
            lines.append(f"FWA Stats clan points leader: {leader} by {delta} point(s).")
        elif delta < 0:
            leader = _stats_clan_name(secondary)
            lines.append(f"FWA Stats clan points leader: {leader} by {abs(delta)} point(s).")
        else:
            lines.append("FWA Stats clan points comparison: tied.")
        lines.append(f"Primary clan points: {primary_points}")
        lines.append(f"Secondary clan points: {secondary_points}")
    else:
        lines.append("FWA Stats clan points comparison unavailable because one side did not expose points.")

    primary_weight = _stats_summary_int(primary.summary, "estimatedWeight")
    secondary_weight = _stats_summary_int(secondary.summary, "estimatedWeight")
    if primary_weight is not None and secondary_weight is not None:
        delta = primary_weight - secondary_weight
        if delta > 0:
            leader = _stats_clan_name(primary)
            lines.append(f"Estimated weight leader: {leader} by {delta:,}.")
        elif delta < 0:
            leader = _stats_clan_name(secondary)
            lines.append(f"Estimated weight leader: {leader} by {abs(delta):,}.")
        else:
            lines.append("Estimated weight comparison: tied.")
        lines.append(f"Primary estimated weight: {primary_weight:,}")
        lines.append(f"Secondary estimated weight: {secondary_weight:,}")

    primary_latest = primary.wars[0] if primary.wars else None
    secondary_latest = secondary.wars[0] if secondary.wars else None
    if primary_latest is not None:
        lines.append(
            "Primary latest war: "
            + _format_stats_war_summary(primary_latest)
        )
    if secondary_latest is not None:
        lines.append(
            "Secondary latest war: "
            + _format_stats_war_summary(secondary_latest)
        )

    return lines


def render_fwa_stats_section(record: FwaStatsClanRecord, *, heading: str) -> list[str]:
    lines = [heading]
    lines.append(f"Summary source: {record.summary_source_url}")
    lines.append(f"Members source: {record.members_source_url}")
    lines.append(f"Wars source: {record.wars_source_url}")
    lines.append(f"Source status: {record.source_status}")
    lines.append(f"Summary status: {record.summary_status}")
    lines.append(f"Members status: {record.members_status}")
    lines.append(f"Wars status: {record.wars_status}")
    if record.error:
        lines.append(f"Error: {record.error}")

    lines.append(f"Clan name: {_stats_clan_name(record)}")
    lines.append(f"Clan tag: {record.clan_tag}")
    lines.append(f"Level: {format_optional_int(_stats_summary_int(record.summary, 'level'))}")
    lines.append(f"Type: {_stats_summary_text(record.summary, 'type')}")
    lines.append(f"Location: {_stats_summary_text(record.summary, 'location')}")
    lines.append(f"Clan points: {format_optional_int(_stats_summary_int(record.summary, 'points'))}")
    lines.append(f"Estimated weight: {format_optional_int(_stats_summary_int(record.summary, 'estimatedWeight'))}")
    lines.append(f"War frequency: {_stats_summary_text(record.summary, 'warFrequency')}")
    lines.append(f"War log: {format_yes_no(_stats_summary_bool(record.summary, 'isWarLogPublic'))}")
    lines.append(f"Wins: {format_optional_int(_stats_summary_int(record.summary, 'wins'))}")
    lines.append(f"Ties: {format_optional_int(_stats_summary_int(record.summary, 'ties'))}")
    lines.append(f"Losses: {format_optional_int(_stats_summary_int(record.summary, 'losses'))}")
    lines.append(f"Required trophies: {format_optional_int(_stats_summary_int(record.summary, 'requiredTrophies'))}")
    lines.append(f"Member export count: {len(record.members)}")

    composition = _stats_th_composition(record.summary)
    if composition:
        lines.append(f"TH composition: {' / '.join(str(value) for value in composition)}")

    if record.wars:
        latest = record.wars[0]
        lines.append("Latest war:")
        lines.append(f"  - {_format_stats_war_summary(latest)}")
        if latest.end_time:
            lines.append(f"  - End time: {latest.end_time}")

    if record.members:
        lines.append("Top member weights:")
        for member in sorted(
            record.members,
            key=lambda item: (
                -(item.weight if item.weight is not None else -1),
                -(item.town_hall if item.town_hall is not None else -1),
                item.name.lower(),
                item.tag,
            ),
        )[:8]:
            parts = [
                member.name,
                f"TH {format_optional_int(member.town_hall)}",
                f"weight {format_optional_int(member.weight)}",
                f"role {member.role or 'n/a'}",
                f"war {format_yes_no(member.in_war)}",
            ]
            if member.donated is not None or member.received is not None:
                parts.append(f"don/recv {format_optional_int(member.donated)}/{format_optional_int(member.received)}")
            lines.append(f"  - {' | '.join(parts)}")

    if record.wars:
        lines.append("Recent war history:")
        for war_record in record.wars[:5]:
            lines.append(f"  - {_format_stats_war_summary(war_record)}")

    return lines


def _format_stats_war_summary(record: FwaStatsWarRecord) -> str:
    opponent_bits = []
    if record.opponent_name:
        opponent_bits.append(record.opponent_name)
    if record.opponent_info:
        opponent_bits.append(f"({record.opponent_info})")
    opponent_text = " ".join(opponent_bits) if opponent_bits else "unknown opponent"

    result = record.result or "unknown"
    if result == "inWar":
        result = "in war"

    parts = [result, "vs", opponent_text]
    if record.team_size is not None:
        parts.append(f"team {record.team_size}")
    if record.clan_stars is not None or record.opponent_stars is not None:
        parts.append(f"stars {format_optional_int(record.clan_stars)}/{format_optional_int(record.opponent_stars)}")
    if record.clan_destruction_percentage is not None or record.opponent_destruction_percentage is not None:
        parts.append(
            f"destruction {format_optional_float(record.clan_destruction_percentage)}/{format_optional_float(record.opponent_destruction_percentage)}"
        )
    if record.synced is not None:
        parts.append(f"synced {format_yes_no(record.synced)}")
    if record.matched is not None:
        parts.append(f"matched {format_yes_no(record.matched)}")
    return " | ".join(parts)


def _stats_clan_name(record: FwaStatsClanRecord) -> str:
    if record.summary is not None:
        name = record.summary.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    if record.wars:
        first = record.wars[0]
        if first.clan_name:
            return first.clan_name.strip('"')
    return record.clan_tag


def _stats_summary_text(summary: dict[str, Any] | None, key: str) -> str:
    if summary is None:
        return "n/a"
    value = summary.get(key)
    if value in (None, ""):
        return "n/a"
    return str(value)


def _stats_summary_int(summary: dict[str, Any] | None, key: str) -> int | None:
    if summary is None:
        return None
    value = summary.get(key)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _stats_summary_float(summary: dict[str, Any] | None, key: str) -> float | None:
    if summary is None:
        return None
    value = summary.get(key)
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _stats_summary_bool(summary: dict[str, Any] | None, key: str) -> bool | None:
    if summary is None:
        return None
    value = summary.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _stats_th_composition(summary: dict[str, Any] | None) -> tuple[int, ...]:
    if summary is None:
        return tuple()

    keys = [f"th{level}Count" for level in range(18, 7, -1)] + ["thLowCount"]
    values: list[int] = []
    for key in keys:
        value = _stats_summary_int(summary, key)
        values.append(0 if value is None else value)
    return tuple(values)


def format_optional_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}"


def format_optional_int(value: int | None) -> str:
    if value is None or value < 0:
        return "n/a"
    return str(value)


def _parse_fwa_stats_member(item: dict[str, Any]) -> FwaStatsMemberRecord:
    return FwaStatsMemberRecord(
        tag=str(item.get("tag") or ""),
        name=str(item.get("name") or ""),
        role=str(item.get("role") or "") or None,
        level=_coerce_int(item.get("level")),
        donated=_coerce_int(item.get("donated")),
        received=_coerce_int(item.get("received")),
        rank=_coerce_int(item.get("rank")),
        trophies=_coerce_int(item.get("trophies")),
        league=_coerce_text(item.get("league")),
        town_hall=_coerce_int(item.get("townHall")),
        weight=_coerce_int(item.get("weight")),
        in_war=_coerce_bool(item.get("inWar")),
    )


def _parse_fwa_stats_war(item: dict[str, Any]) -> FwaStatsWarRecord:
    return FwaStatsWarRecord(
        end_time=_coerce_text(item.get("endTime")),
        search_time=_coerce_text(item.get("searchTime")),
        result=_coerce_text(item.get("result")),
        team_size=_coerce_int(item.get("teamSize")),
        clan_tag=_coerce_text(item.get("clanTag")),
        clan_name=_coerce_text(item.get("clanName")),
        clan_level=_coerce_int(item.get("clanLevel")),
        clan_stars=_coerce_int(item.get("clanStars")),
        clan_destruction_percentage=_coerce_float(item.get("clanDestructionPercentage")),
        clan_attacks=_coerce_int(item.get("clanAttacks")),
        clan_exp_earned=_coerce_int(item.get("clanExpEarned")),
        opponent_tag=_coerce_text(item.get("opponentTag")),
        opponent_name=_coerce_text(item.get("opponentName")),
        opponent_level=_coerce_int(item.get("opponentLevel")),
        opponent_stars=_coerce_int(item.get("opponentStars")),
        opponent_destruction_percentage=_coerce_float(item.get("opponentDestructionPercentage")),
        opponent_info=_coerce_text(item.get("opponentInfo")),
        synced=_coerce_bool(item.get("synced")),
        matched=_coerce_bool(item.get("matched")),
    )


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def render_points_section(record: FwaPointsRecord, *, heading: str) -> list[str]:
    lines = [heading]
    lines.append(f"Source: {record.source_url}")
    lines.append(f"Source status: {record.source_status}")
    if record.error:
        lines.append(f"Error: {record.error}")
    if record.clan_name:
        lines.append(f"Clan name: {record.clan_name}")
    lines.append(f"Clan tag: {record.clan_tag}")
    lines.append(f"Point balance: {record.point_balance if record.point_balance is not None else 'n/a'}")
    lines.append(f"Active FWA: {format_yes_no(record.active_fwa)}")
    lines.append(f"Last war state: {record.current_war_state or 'n/a'}")
    if record.current_war_id:
        lines.append(f"Current war id: {record.current_war_id}")
    if record.current_war_sync:
        lines.append(f"Current war sync: {record.current_war_sync}")
    if record.current_opponent_name or record.current_opponent_tag:
        opponent_bits = []
        if record.current_opponent_name:
            opponent_bits.append(record.current_opponent_name)
        if record.current_opponent_tag:
            opponent_bits.append(f"({record.current_opponent_tag})")
        lines.append(f"Current opponent: {' '.join(opponent_bits)}")
    if record.current_war_note:
        lines.append(f"Win-calculator note: {record.current_war_note}")

    if record.recent_history:
        lines.append("Recent point history:")
        for entry in record.recent_history[:8]:
            details = [entry.timestamp]
            if entry.war_label:
                details.append(entry.war_label)
            if entry.points_delta is not None:
                details.append(f"{entry.points_delta:+d}")
            if entry.note:
                details.append(entry.note)
            lines.append(f"  - {' | '.join(details)}")
    else:
        lines.append("Recent point history: none")

    return lines


def render_cc_section(record: CcClanStatus, *, heading: str) -> list[str]:
    lines = [heading]
    lines.append(f"Source: {record.source_url}")
    lines.append(f"Source status: {record.source_status}")
    if record.title:
        lines.append(f"Page title: {record.title}")
    if record.error:
        lines.append(f"Error: {record.error}")
    lines.append(f"Clan tag: {record.clan_tag}")
    if record.labels:
        lines.append("Status flags:")
        for key, value in record.labels.items():
            lines.append(f"  - {key.replace('_', ' ').title()}: {value}")
    else:
        lines.append("Status flags: none detected")
    if record.evidence:
        lines.append("Evidence:")
        for line in record.evidence[:5]:
            lines.append(f"  - {line}")
    return lines


def render_fwa_guide_section() -> list[str]:
    return [
        "FWA matchmaking notes",
        "Points decide the match: the clan with more points wins.",
        "Public guide scoring rules: win = -1, loss = +1, mismatch = 0, blacklist/no-reward wars = 0.",
        "Zero-win clans start at -100, so the point system is designed to keep matches balanced over time.",
        "Official FWA clans, blacklisted clans, former FWA clans, and mismatches are all treated as distinct states.",
        "Weights, clan composition, and sync timing are part of the matchmaking process, even though the exact hidden weight model is not exposed.",
        "The report treats the public guide as matchmaking context, not as a secret API contract.",
    ]


def format_yes_no(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def normalize_clan_tag(raw_tag: str) -> str:
    cleaned = raw_tag.strip().upper().replace(" ", "")
    if not cleaned:
        raise ValueError("Clan tag cannot be empty.")
    if not cleaned.startswith("#"):
        cleaned = f"#{cleaned}"
    return cleaned


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _extract_title(html: str) -> str | None:
    match = re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S)
    if match:
        return normalize_whitespace(match.group(1))
    return None


def _find_overview_text(soup: BeautifulSoup) -> str:
    for paragraph in soup.find_all("p"):
        text = normalize_whitespace(paragraph.get_text(" ", strip=True))
        if "Clan Name" in text and "Point Balance" in text:
            return text
    return normalize_whitespace(soup.get_text(" ", strip=True))


def _match_text(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.I | re.S)
    if not match:
        return None
    return normalize_whitespace(match.group(1))


def _match_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, flags=re.I | re.S)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _parse_history_entries(soup: BeautifulSoup) -> tuple[FwaPointHistoryEntry, ...]:
    history_heading = soup.find(string=re.compile(r"Clan Point History\s*:", re.I))
    if history_heading is None:
        return tuple()

    table = history_heading.find_parent().find_next("table") if history_heading.find_parent() else None
    if table is None:
        return tuple()

    entries: list[FwaPointHistoryEntry] = []
    for row in table.select("tbody tr"):
        cells = [normalize_whitespace(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
        if len(cells) < 4:
            continue
        timestamp, war_label, points, note = cells[:4]
        entries.append(
            FwaPointHistoryEntry(
                timestamp=timestamp,
                war_label=war_label or None,
                points_delta=_safe_int(points),
                note=note or None,
            )
        )
    return tuple(entries)


def _extract_label_values(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    for row in soup.find_all("tr"):
        cells = [normalize_whitespace(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
        if len(cells) >= 2 and len(cells) <= 4:
            for index in range(0, len(cells) - 1, 2):
                left = cells[index]
                right = cells[index + 1]
                if left and right:
                    pairs.append((left, right))

    for paragraph in soup.find_all(["p", "li"]):
        text = normalize_whitespace(paragraph.get_text(" ", strip=True))
        for label in ("Seen by System", "Blacklisted", "Former FWA", "Mismatch"):
            if label.lower() in text.lower():
                match = re.search(
                    rf"{re.escape(label)}\s*[:\-]?\s*([^\|;]+)",
                    text,
                    flags=re.I,
                )
                if match:
                    pairs.append((label, normalize_whitespace(match.group(1))))

    return pairs


def _normalize_cc_label(label: str) -> str:
    label = label.lower().strip()
    label = re.sub(r"[^a-z0-9]+", "_", label)
    if "seen_by_system" in label or label == "seen":
        return "seen_by_system"
    if "blacklisted" in label or label == "bl":
        return "blacklisted"
    if "former_fwa" in label or "former" in label:
        return "former_fwa"
    if "mismatch" in label:
        return "mismatch"
    return label


def _looks_like_cloudflare(html: str) -> bool:
    lowered = html.lower()
    return "performing security verification" in lowered or "just a moment" in lowered


def _cloudflare_evidence(html: str) -> list[str]:
    text = normalize_whitespace(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    snippets = []
    for needle in ("Performing security verification", "Just a moment", "Cloudflare", "security service"):
        if needle.lower() in text.lower():
            snippets.append(needle)
    return snippets


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
