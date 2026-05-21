from __future__ import annotations

from dataclasses import dataclass, field
import asyncio

import coc

from .config import AppConfig


class CocConfigurationError(RuntimeError):
    pass


@dataclass(slots=True)
class CocService:
    config: AppConfig
    _client: coc.Client | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def configured(self) -> bool:
        return bool((self.config.coc_email and self.config.coc_password) or self.config.coc_tokens)

    @property
    def auth_mode(self) -> str:
        if self.config.coc_email and self.config.coc_password:
            return "email/password"
        if self.config.coc_tokens:
            return "manual tokens"
        return "unset"

    async def get_client(self) -> coc.Client:
        if self._client is not None:
            return self._client

        async with self._lock:
            if self._client is not None:
                return self._client

            if not self.configured:
                raise CocConfigurationError(
                    "Clash of Clans access is not configured. Set COC_EMAIL + COC_PASSWORD or COC_TOKENS."
                )

            client = coc.Client(
                key_count=self.config.coc_key_count,
                key_names=self.config.coc_key_names,
                throttle_limit=self.config.coc_throttle_limit,
            )

            try:
                if self.config.coc_email and self.config.coc_password:
                    await client.login(self.config.coc_email, self.config.coc_password)
                else:
                    await client.login_with_tokens(*self.config.coc_tokens)
            except Exception:
                await client.close()
                raise

            self._client = client
            return client

    async def close(self) -> None:
        if self._client is None:
            return

        await self._client.close()
        self._client = None
