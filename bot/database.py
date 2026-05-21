from __future__ import annotations

from dataclasses import dataclass, field

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

        self.pool = await asyncpg.create_pool(self.dsn)

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
