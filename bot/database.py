from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any
from urllib.parse import urlsplit

import asyncpg


@dataclass(slots=True)
class Database:
    dsn: str | None
    pool_min_size: int = 0
    pool_max_size: int = 3
    pool: asyncpg.Pool | None = field(default=None, init=False, repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.dsn)

    @property
    def connected(self) -> bool:
        return self.pool is not None

    async def connect(self) -> None:
        if not self.dsn:
            return

        _validate_dsn(self.dsn)
        self.pool = await asyncpg.create_pool(
            self.dsn,
            min_size=self.pool_min_size,
            max_size=self.pool_max_size,
        )
        await self.ensure_schema()

    async def close(self) -> None:
        if self.pool is None:
            return

        await self.pool.close()
        self.pool = None

    async def health(self) -> dict[str, object]:
        if self.pool is None:
            return {
                "configured": self.configured,
                "connected": False,
            }

        async with self.pool.acquire() as connection:
            version = await connection.fetchval("select version()")

        return {
            "configured": True,
            "connected": True,
            "version": version,
        }

    async def ensure_schema(self) -> None:
        if self.pool is None:
            return

        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                create table if not exists linked_server_clans (
                    id bigserial primary key,
                    guild_id text not null,
                    clan_tag text not null,
                    clan_name text not null,
                    alias text,
                    nickname text,
                    channel_id text,
                    active boolean not null default true,
                    created_at timestamptz not null default now(),
                    updated_at timestamptz not null default now(),
                    unique (guild_id, clan_tag)
                )
                """
            )
            await connection.execute(
                """
                create unique index if not exists linked_server_clans_channel_idx
                on linked_server_clans (guild_id, channel_id)
                where channel_id is not null and active
                """
            )
            await connection.execute(
                """
                create table if not exists linked_users (
                    user_id text primary key,
                    username text not null,
                    display_name text not null,
                    clan_tag text,
                    clan_name text,
                    last_player_tag text,
                    created_at timestamptz not null default now(),
                    updated_at timestamptz not null default now()
                )
                """
            )
            await connection.execute(
                """
                create table if not exists linked_players (
                    player_tag text primary key,
                    player_name text not null,
                    user_id text not null references linked_users(user_id) on delete cascade,
                    username text not null,
                    display_name text not null,
                    is_default boolean not null default false,
                    verified boolean not null default false,
                    linked_by text not null,
                    created_at timestamptz not null default now(),
                    updated_at timestamptz not null default now()
                )
                """
            )
            await connection.execute(
                """
                create index if not exists linked_players_user_idx
                on linked_players (user_id, is_default desc, created_at)
                """
            )
            await connection.execute(
                """
                create table if not exists announcement_channels (
                    guild_id text primary key,
                    channel_id text not null,
                    war_found boolean not null default true,
                    fwa_ready boolean not null default true,
                    war_ended boolean not null default true,
                    active boolean not null default true,
                    created_at timestamptz not null default now(),
                    updated_at timestamptz not null default now()
                )
                """
            )
            await connection.execute(
                """
                create table if not exists clan_war_snapshots (
                    clan_tag text primary key,
                    clan_name text,
                    opponent_tag text,
                    opponent_name text,
                    state text,
                    preparation_start timestamptz,
                    battle_start timestamptz,
                    battle_end timestamptz,
                    team_size integer,
                    war_key text,
                    fwa_classification text,
                    planned_result text,
                    external_checked_at timestamptz,
                    raw jsonb not null default '{}'::jsonb,
                    updated_at timestamptz not null default now()
                )
                """
            )
            await connection.execute(
                """
                create table if not exists clan_war_announcements (
                    id bigserial primary key,
                    guild_id text not null,
                    clan_tag text not null,
                    event_key text not null,
                    channel_id text not null,
                    created_at timestamptz not null default now(),
                    unique (guild_id, clan_tag, event_key)
                )
                """
            )
            await connection.execute(
                """
                create table if not exists autorole_configs (
                    guild_id text not null,
                    clan_tag text not null,
                    clan_name text not null,
                    general_role_id text,
                    leader_role_id text,
                    co_leader_role_id text,
                    elder_role_id text,
                    member_role_id text,
                    grace_enabled boolean not null default true,
                    active boolean not null default true,
                    last_synced_at timestamptz,
                    created_at timestamptz not null default now(),
                    updated_at timestamptz not null default now(),
                    primary key (guild_id, clan_tag)
                )
                """
            )
            await connection.execute(
                """
                create table if not exists autorole_observations (
                    id bigserial primary key,
                    guild_id text not null,
                    clan_tag text not null,
                    player_tag text not null,
                    user_id text not null,
                    player_name text not null,
                    clan_role text not null,
                    rank_weight integer not null,
                    observed_at timestamptz not null default now()
                )
                """
            )
            await connection.execute(
                """
                create index if not exists autorole_observations_lookup_idx
                on autorole_observations (guild_id, clan_tag, player_tag, observed_at desc)
                """
            )
            await connection.execute(
                """
                create table if not exists autorole_assignments (
                    guild_id text not null,
                    clan_tag text not null,
                    user_id text not null,
                    role_id text not null,
                    desired boolean not null default true,
                    last_applied_at timestamptz not null default now(),
                    primary key (guild_id, clan_tag, user_id, role_id)
                )
                """
            )
            await connection.execute(
                """
                delete from autorole_observations
                where observed_at < now() - interval '3 days'
                """
            )

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Database is not configured. Set DATABASE_URL before using linking commands.")
        return self.pool

    async def count_linking_rows(self) -> dict[str, int]:
        if self.pool is None:
            return {
                "server_clans": 0,
                "user_clans": 0,
                "player_links": 0,
                "verified_players": 0,
                "announcement_channels": 0,
                "war_snapshots": 0,
                "autorole_configs": 0,
            }

        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select
                    (select count(*) from linked_server_clans where active) as server_clans,
                    (select count(*) from linked_users where clan_tag is not null) as user_clans,
                    (select count(*) from linked_players) as player_links,
                    (select count(*) from linked_players where verified) as verified_players,
                    (select count(*) from announcement_channels where active) as announcement_channels,
                    (select count(*) from clan_war_snapshots) as war_snapshots,
                    (select count(*) from autorole_configs where active) as autorole_configs
                """
            )
        return dict(row) if row is not None else {}

    async def upsert_autorole_config(
        self,
        *,
        guild_id: int | str,
        clan_tag: str,
        clan_name: str,
        general_role_id: int | str | None,
        leader_role_id: int | str | None,
        co_leader_role_id: int | str | None,
        elder_role_id: int | str | None,
        member_role_id: int | str | None,
        grace_enabled: bool,
    ) -> dict[str, Any]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            insert into autorole_configs
                (
                    guild_id, clan_tag, clan_name, general_role_id, leader_role_id,
                    co_leader_role_id, elder_role_id, member_role_id, grace_enabled, active
                )
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, true)
            on conflict (guild_id, clan_tag) do update set
                clan_name = excluded.clan_name,
                general_role_id = excluded.general_role_id,
                leader_role_id = excluded.leader_role_id,
                co_leader_role_id = excluded.co_leader_role_id,
                elder_role_id = excluded.elder_role_id,
                member_role_id = excluded.member_role_id,
                grace_enabled = excluded.grace_enabled,
                active = true,
                updated_at = now()
            returning *
            """,
            str(guild_id),
            clan_tag,
            clan_name,
            None if general_role_id is None else str(general_role_id),
            None if leader_role_id is None else str(leader_role_id),
            None if co_leader_role_id is None else str(co_leader_role_id),
            None if elder_role_id is None else str(elder_role_id),
            None if member_role_id is None else str(member_role_id),
            grace_enabled,
        )
        return dict(row)

    async def list_autorole_configs(
        self,
        *,
        guild_id: int | str | None = None,
        clan_tag: str | None = None,
    ) -> list[dict[str, Any]]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            select *
            from autorole_configs
            where active
                and ($1::text is null or guild_id = $1)
                and ($2::text is null or clan_tag = $2)
            order by guild_id, clan_name, clan_tag
            """,
            None if guild_id is None else str(guild_id),
            clan_tag,
        )
        return [dict(row) for row in rows]

    async def remove_autorole_config(self, *, guild_id: int | str, clan_tag: str) -> dict[str, Any] | None:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            update autorole_configs
            set active = false, updated_at = now()
            where guild_id = $1 and clan_tag = $2 and active
            returning *
            """,
            str(guild_id),
            clan_tag,
        )
        return dict(row) if row is not None else None

    async def list_autorole_subjects(
        self,
        *,
        guild_id: int | str,
        clan_tag: str,
        retention_days: int,
    ) -> list[dict[str, Any]]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            select player_tag, player_name, user_id
            from linked_players
            union
            select distinct player_tag, player_name, user_id
            from autorole_observations
            where guild_id = $1
                and clan_tag = $2
                and observed_at >= now() - ($3::int * interval '1 day')
            order by user_id, player_tag
            """,
            str(guild_id),
            clan_tag,
            retention_days,
        )
        return [dict(row) for row in rows]

    async def insert_autorole_observation(
        self,
        *,
        guild_id: int | str,
        clan_tag: str,
        player_tag: str,
        user_id: int | str,
        player_name: str,
        clan_role: str,
        rank_weight: int,
    ) -> None:
        pool = self._require_pool()
        await pool.execute(
            """
            insert into autorole_observations
                (guild_id, clan_tag, player_tag, user_id, player_name, clan_role, rank_weight)
            values ($1, $2, $3, $4, $5, $6, $7)
            """,
            str(guild_id),
            clan_tag,
            player_tag,
            str(user_id),
            player_name,
            clan_role,
            rank_weight,
        )

    async def get_autorole_effective_roles(
        self,
        *,
        guild_id: int | str,
        clan_tag: str,
        retention_days: int,
    ) -> list[dict[str, Any]]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            select distinct on (player_tag)
                player_tag,
                user_id,
                player_name,
                clan_role,
                rank_weight,
                observed_at
            from autorole_observations
            where guild_id = $1
                and clan_tag = $2
                and observed_at >= now() - ($3::int * interval '1 day')
            order by player_tag, rank_weight desc, observed_at desc
            """,
            str(guild_id),
            clan_tag,
            retention_days,
        )
        return [dict(row) for row in rows]

    async def mark_autorole_assignment(
        self,
        *,
        guild_id: int | str,
        clan_tag: str,
        user_id: int | str,
        role_id: int | str,
        desired: bool,
    ) -> None:
        pool = self._require_pool()
        await pool.execute(
            """
            insert into autorole_assignments (guild_id, clan_tag, user_id, role_id, desired)
            values ($1, $2, $3, $4, $5)
            on conflict (guild_id, clan_tag, user_id, role_id) do update set
                desired = excluded.desired,
                last_applied_at = now()
            """,
            str(guild_id),
            clan_tag,
            str(user_id),
            str(role_id),
            desired,
        )

    async def update_autorole_synced_at(self, *, guild_id: int | str, clan_tag: str) -> None:
        pool = self._require_pool()
        await pool.execute(
            """
            update autorole_configs
            set last_synced_at = now(), updated_at = now()
            where guild_id = $1 and clan_tag = $2
            """,
            str(guild_id),
            clan_tag,
        )

    async def cleanup_autorole_observations(self, *, retention_days: int) -> None:
        pool = self._require_pool()
        await pool.execute(
            """
            delete from autorole_observations
            where observed_at < now() - ($1::int * interval '1 day')
            """,
            retention_days,
        )

    async def upsert_announcement_channel(
        self,
        *,
        guild_id: int | str,
        channel_id: int | str,
        war_found: bool = True,
        fwa_ready: bool = True,
        war_ended: bool = True,
    ) -> dict[str, Any]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            insert into announcement_channels
                (guild_id, channel_id, war_found, fwa_ready, war_ended, active)
            values ($1, $2, $3, $4, $5, true)
            on conflict (guild_id) do update set
                channel_id = excluded.channel_id,
                war_found = excluded.war_found,
                fwa_ready = excluded.fwa_ready,
                war_ended = excluded.war_ended,
                active = true,
                updated_at = now()
            returning *
            """,
            str(guild_id),
            str(channel_id),
            war_found,
            fwa_ready,
            war_ended,
        )
        return dict(row)

    async def get_announcement_channel(self, guild_id: int | str) -> dict[str, Any] | None:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "select * from announcement_channels where guild_id = $1 and active",
            str(guild_id),
        )
        return dict(row) if row is not None else None

    async def list_monitored_clans(self) -> list[dict[str, Any]]:
        if self.pool is None:
            return []

        rows = await self.pool.fetch(
            """
            select
                l.guild_id,
                l.clan_tag,
                l.clan_name,
                l.channel_id as clan_channel_id,
                a.channel_id as announcement_channel_id,
                a.war_found,
                a.fwa_ready,
                a.war_ended
            from linked_server_clans l
            left join announcement_channels a
                on a.guild_id = l.guild_id and a.active
            where l.active
            order by l.clan_tag, l.guild_id
            """
        )
        return [dict(row) for row in rows]

    async def get_war_snapshot(self, clan_tag: str) -> dict[str, Any] | None:
        pool = self._require_pool()
        row = await pool.fetchrow("select * from clan_war_snapshots where clan_tag = $1", clan_tag)
        return dict(row) if row is not None else None

    async def upsert_war_snapshot(
        self,
        *,
        clan_tag: str,
        clan_name: str | None,
        opponent_tag: str | None,
        opponent_name: str | None,
        state: str,
        preparation_start: Any,
        battle_start: Any,
        battle_end: Any,
        team_size: int | None,
        war_key: str,
        fwa_classification: str | None = None,
        planned_result: str | None = None,
        external_checked_at: Any = None,
        raw: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            insert into clan_war_snapshots
                (
                    clan_tag, clan_name, opponent_tag, opponent_name, state,
                    preparation_start, battle_start, battle_end, team_size, war_key,
                    fwa_classification, planned_result, external_checked_at, raw
                )
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb)
            on conflict (clan_tag) do update set
                clan_name = excluded.clan_name,
                opponent_tag = excluded.opponent_tag,
                opponent_name = excluded.opponent_name,
                state = excluded.state,
                preparation_start = excluded.preparation_start,
                battle_start = excluded.battle_start,
                battle_end = excluded.battle_end,
                team_size = excluded.team_size,
                war_key = excluded.war_key,
                fwa_classification = case
                    when clan_war_snapshots.war_key = excluded.war_key
                    then coalesce(excluded.fwa_classification, clan_war_snapshots.fwa_classification)
                    else excluded.fwa_classification
                end,
                planned_result = case
                    when clan_war_snapshots.war_key = excluded.war_key
                    then coalesce(excluded.planned_result, clan_war_snapshots.planned_result)
                    else excluded.planned_result
                end,
                external_checked_at = case
                    when clan_war_snapshots.war_key = excluded.war_key
                    then coalesce(excluded.external_checked_at, clan_war_snapshots.external_checked_at)
                    else excluded.external_checked_at
                end,
                raw = excluded.raw,
                updated_at = now()
            returning *
            """,
            clan_tag,
            clan_name,
            opponent_tag,
            opponent_name,
            state,
            preparation_start,
            battle_start,
            battle_end,
            team_size,
            war_key,
            fwa_classification,
            planned_result,
            external_checked_at,
            json.dumps(raw or {}),
        )
        return dict(row)

    async def mark_announcement_sent(
        self,
        *,
        guild_id: int | str,
        clan_tag: str,
        event_key: str,
        channel_id: int | str,
    ) -> bool:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            insert into clan_war_announcements (guild_id, clan_tag, event_key, channel_id)
            values ($1, $2, $3, $4)
            on conflict (guild_id, clan_tag, event_key) do nothing
            returning id
            """,
            str(guild_id),
            clan_tag,
            event_key,
            str(channel_id),
        )
        return row is not None

    async def upsert_user(self, *, user_id: int | str, username: str, display_name: str) -> None:
        pool = self._require_pool()
        await pool.execute(
            """
            insert into linked_users (user_id, username, display_name)
            values ($1, $2, $3)
            on conflict (user_id) do update set
                username = excluded.username,
                display_name = excluded.display_name,
                updated_at = now()
            """,
            str(user_id),
            username,
            display_name,
        )

    async def upsert_server_clan(
        self,
        *,
        guild_id: int | str,
        clan_tag: str,
        clan_name: str,
        alias: str | None,
        nickname: str | None,
        channel_id: int | str | None,
    ) -> dict[str, Any]:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                if channel_id is not None:
                    await connection.execute(
                        """
                        update linked_server_clans
                        set channel_id = null, updated_at = now()
                        where guild_id = $1 and channel_id = $2 and clan_tag <> $3
                        """,
                        str(guild_id),
                        str(channel_id),
                        clan_tag,
                    )
                row = await connection.fetchrow(
                    """
                    insert into linked_server_clans
                        (guild_id, clan_tag, clan_name, alias, nickname, channel_id, active)
                    values ($1, $2, $3, $4, $5, $6, true)
                    on conflict (guild_id, clan_tag) do update set
                        clan_name = excluded.clan_name,
                        alias = excluded.alias,
                        nickname = excluded.nickname,
                        channel_id = excluded.channel_id,
                        active = true,
                        updated_at = now()
                    returning *
                    """,
                    str(guild_id),
                    clan_tag,
                    clan_name,
                    alias,
                    nickname,
                    None if channel_id is None else str(channel_id),
                )
        return dict(row)

    async def list_server_clans(self, guild_id: int | str) -> list[dict[str, Any]]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            select *
            from linked_server_clans
            where guild_id = $1 and active
            order by coalesce(nickname, clan_name), clan_tag
            """,
            str(guild_id),
        )
        return [dict(row) for row in rows]

    async def remove_server_clan(
        self,
        *,
        guild_id: int | str,
        clan_tag: str | None,
        channel_id: int | str | None,
    ) -> dict[str, Any] | None:
        pool = self._require_pool()
        if channel_id is not None:
            row = await pool.fetchrow(
                """
                update linked_server_clans
                set channel_id = null, updated_at = now()
                where guild_id = $1 and channel_id = $2 and active
                returning *
                """,
                str(guild_id),
                str(channel_id),
            )
            return dict(row) if row is not None else None

        if clan_tag is None:
            return None

        row = await pool.fetchrow(
            """
            update linked_server_clans
            set active = false, channel_id = null, updated_at = now()
            where guild_id = $1 and clan_tag = $2 and active
            returning *
            """,
            str(guild_id),
            clan_tag,
        )
        return dict(row) if row is not None else None

    async def find_server_clan(
        self,
        *,
        guild_id: int | str,
        channel_id: int | str | None = None,
        query: str | None = None,
    ) -> dict[str, Any] | None:
        pool = self._require_pool()
        if channel_id is not None:
            row = await pool.fetchrow(
                """
                select *
                from linked_server_clans
                where guild_id = $1 and channel_id = $2 and active
                """,
                str(guild_id),
                str(channel_id),
            )
            if row is not None:
                return dict(row)

        if query:
            row = await pool.fetchrow(
                """
                select *
                from linked_server_clans
                where guild_id = $1
                    and active
                    and (
                        clan_tag = $2
                        or lower(alias) = lower($3)
                        or lower(nickname) = lower($3)
                        or lower(clan_name) = lower($3)
                    )
                order by channel_id is null, coalesce(nickname, clan_name)
                limit 1
                """,
                str(guild_id),
                query,
                query.lstrip("#"),
            )
            if row is not None:
                return dict(row)

        row = await pool.fetchrow(
            """
            select *
            from linked_server_clans
            where guild_id = $1 and active
            order by channel_id is null, coalesce(nickname, clan_name)
            limit 1
            """,
            str(guild_id),
        )
        return dict(row) if row is not None else None

    async def upsert_user_clan(
        self,
        *,
        user_id: int | str,
        username: str,
        display_name: str,
        clan_tag: str,
        clan_name: str,
    ) -> None:
        await self.upsert_user(user_id=user_id, username=username, display_name=display_name)
        pool = self._require_pool()
        await pool.execute(
            """
            update linked_users
            set clan_tag = $2, clan_name = $3, updated_at = now()
            where user_id = $1
            """,
            str(user_id),
            clan_tag,
            clan_name,
        )

    async def remove_user_clan(self, user_id: int | str, clan_tag: str | None = None) -> bool:
        pool = self._require_pool()
        if clan_tag is None:
            result = await pool.execute(
                """
                update linked_users
                set clan_tag = null, clan_name = null, updated_at = now()
                where user_id = $1 and clan_tag is not null
                """,
                str(user_id),
            )
        else:
            result = await pool.execute(
                """
                update linked_users
                set clan_tag = null, clan_name = null, updated_at = now()
                where user_id = $1 and clan_tag = $2
                """,
                str(user_id),
                clan_tag,
            )
        return result.endswith("1")

    async def get_user_profile(self, user_id: int | str) -> dict[str, Any] | None:
        pool = self._require_pool()
        row = await pool.fetchrow("select * from linked_users where user_id = $1", str(user_id))
        return dict(row) if row is not None else None

    async def link_player(
        self,
        *,
        user_id: int | str,
        username: str,
        display_name: str,
        player_tag: str,
        player_name: str,
        linked_by: int | str,
        is_default: bool,
        verified: bool = False,
    ) -> dict[str, Any]:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    insert into linked_users (user_id, username, display_name, last_player_tag)
                    values ($1, $2, $3, $4)
                    on conflict (user_id) do update set
                        username = excluded.username,
                        display_name = excluded.display_name,
                        last_player_tag = excluded.last_player_tag,
                        updated_at = now()
                    """,
                    str(user_id),
                    username,
                    display_name,
                    player_tag,
                )
                existing_default = await connection.fetchval(
                    "select exists(select 1 from linked_players where user_id = $1 and is_default)",
                    str(user_id),
                )
                should_default = is_default or not existing_default
                if should_default:
                    await connection.execute(
                        "update linked_players set is_default = false, updated_at = now() where user_id = $1",
                        str(user_id),
                    )
                row = await connection.fetchrow(
                    """
                    insert into linked_players
                        (player_tag, player_name, user_id, username, display_name, is_default, verified, linked_by)
                    values ($1, $2, $3, $4, $5, $6, $7, $8)
                    on conflict (player_tag) do update set
                        player_name = excluded.player_name,
                        user_id = excluded.user_id,
                        username = excluded.username,
                        display_name = excluded.display_name,
                        is_default = excluded.is_default,
                        verified = linked_players.verified or excluded.verified,
                        linked_by = excluded.linked_by,
                        updated_at = now()
                    returning *
                    """,
                    player_tag,
                    player_name,
                    str(user_id),
                    username,
                    display_name,
                    should_default,
                    verified,
                    str(linked_by),
                )
        return dict(row)

    async def verify_player_link(
        self,
        *,
        user_id: int | str,
        username: str,
        display_name: str,
        player_tag: str,
        player_name: str,
    ) -> dict[str, Any]:
        return await self.link_player(
            user_id=user_id,
            username=username,
            display_name=display_name,
            player_tag=player_tag,
            player_name=player_name,
            linked_by=user_id,
            is_default=False,
            verified=True,
        )

    async def list_player_links(self, user_id: int | str) -> list[dict[str, Any]]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            select *
            from linked_players
            where user_id = $1
            order by is_default desc, verified desc, player_name, player_tag
            """,
            str(user_id),
        )
        return [dict(row) for row in rows]

    async def get_default_player_link(self, user_id: int | str) -> dict[str, Any] | None:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            select *
            from linked_players
            where user_id = $1
            order by is_default desc, created_at
            limit 1
            """,
            str(user_id),
        )
        return dict(row) if row is not None else None

    async def delete_player_link(self, *, user_id: int | str, player_tag: str) -> dict[str, Any] | None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    delete from linked_players
                    where user_id = $1 and player_tag = $2
                    returning *
                    """,
                    str(user_id),
                    player_tag,
                )
                if row is None:
                    return None
                if row["is_default"]:
                    replacement = await connection.fetchval(
                        """
                        select player_tag
                        from linked_players
                        where user_id = $1
                        order by verified desc, created_at
                        limit 1
                        """,
                        str(user_id),
                    )
                    if replacement:
                        await connection.execute(
                            "update linked_players set is_default = true, updated_at = now() where player_tag = $1",
                            replacement,
                        )
        return dict(row)


def _validate_dsn(dsn: str) -> None:
    parsed = urlsplit(dsn)
    if parsed.netloc.count("@") > 1:
        raise RuntimeError(
            "DATABASE_URL contains an unencoded @ in the username or password. "
            "Percent-encode it as %40 before deploying."
        )
