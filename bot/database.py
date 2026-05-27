from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import asyncpg


@dataclass(slots=True)
class Database:
    dsn: str | None
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
        self.pool = await asyncpg.create_pool(self.dsn)
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

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Database is not configured. Set DATABASE_URL before using linking commands.")
        return self.pool

    async def count_linking_rows(self) -> dict[str, int]:
        if self.pool is None:
            return {"server_clans": 0, "user_clans": 0, "player_links": 0, "verified_players": 0}

        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select
                    (select count(*) from linked_server_clans where active) as server_clans,
                    (select count(*) from linked_users where clan_tag is not null) as user_clans,
                    (select count(*) from linked_players) as player_links,
                    (select count(*) from linked_players where verified) as verified_players
                """
            )
        return dict(row) if row is not None else {}

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
