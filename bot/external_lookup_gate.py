from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Deque

from .config import AppConfig


@dataclass(frozen=True, slots=True)
class ExternalLookupDecision:
    allowed: bool
    reason: str
    retry_after_seconds: int = 0


class ExternalLookupGate:
    """Small in-process guard for community FWA endpoint lookups."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(2)
        self._manual_tag_checks: dict[str, float] = {}
        self._auto_tag_checks: dict[str, float] = {}
        self._user_checks: dict[str, Deque[float]] = defaultdict(deque)
        self._guild_checks: dict[str, Deque[float]] = defaultdict(deque)

    async def acquire_manual(
        self,
        *,
        clan_tag: str,
        user_id: int | str,
        guild_id: int | str | None,
        last_checked_at: datetime | None,
    ) -> ExternalLookupDecision:
        now = monotonic()
        async with self._lock:
            cooldown = self._config.manual_external_lookup_tag_cooldown_seconds
            remaining = _remaining_from_timestamp(last_checked_at, cooldown)
            if remaining > 0:
                return ExternalLookupDecision(False, "recent cached external check", remaining)

            remaining = _remaining_from_monotonic(self._manual_tag_checks.get(clan_tag), cooldown, now)
            if remaining > 0:
                return ExternalLookupDecision(False, "same clan was checked recently", remaining)

            user_key = str(user_id)
            user_remaining = _check_burst(
                self._user_checks[user_key],
                limit=self._config.external_lookup_user_burst_per_minute,
                now=now,
            )
            if user_remaining > 0:
                return ExternalLookupDecision(False, "manual lookup burst limit", user_remaining)

            if guild_id is not None:
                guild_remaining = _check_burst(
                    self._guild_checks[str(guild_id)],
                    limit=self._config.external_lookup_guild_burst_per_minute,
                    now=now,
                )
                if guild_remaining > 0:
                    return ExternalLookupDecision(False, "server lookup burst limit", guild_remaining)

            self._manual_tag_checks[clan_tag] = now
            self._user_checks[user_key].append(now)
            if guild_id is not None:
                self._guild_checks[str(guild_id)].append(now)

        return ExternalLookupDecision(True, "allowed")

    async def acquire_automatic(self, *, clan_tag: str, last_checked_at: datetime | None) -> ExternalLookupDecision:
        now = monotonic()
        async with self._lock:
            cooldown = self._config.automatic_external_lookup_tag_cooldown_seconds
            remaining = _remaining_from_timestamp(last_checked_at, cooldown)
            if remaining > 0:
                return ExternalLookupDecision(False, "recent automatic external check", remaining)

            remaining = _remaining_from_monotonic(self._auto_tag_checks.get(clan_tag), cooldown, now)
            if remaining > 0:
                return ExternalLookupDecision(False, "automatic clan cooldown", remaining)

            self._auto_tag_checks[clan_tag] = now

        return ExternalLookupDecision(True, "allowed")

    def endpoint_slot(self) -> asyncio.Semaphore:
        return self._semaphore


def _remaining_from_timestamp(value: datetime | None, cooldown_seconds: int) -> int:
    if value is None:
        return 0
    age = (datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds()
    return max(0, int(cooldown_seconds - age))


def _remaining_from_monotonic(previous: float | None, cooldown_seconds: int, now: float) -> int:
    if previous is None:
        return 0
    return max(0, int(cooldown_seconds - (now - previous)))


def _check_burst(bucket: Deque[float], *, limit: int, now: float) -> int:
    window = 60.0
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) < limit:
        return 0
    return max(1, int(window - (now - bucket[0])))
