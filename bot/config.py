from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import os


REPO_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = REPO_ROOT / ".env"
DEFAULT_PORT = 3000


class RuntimeMode(StrEnum):
    local = "local"
    testing = "testing"
    production = "production"


@dataclass(frozen=True, slots=True)
class AppConfig:
    runtime_mode: RuntimeMode
    render_environment: bool
    dotenv_loaded: bool
    discord_token: str
    discord_guild_id: int | None
    port: int
    database_url: str | None

    @classmethod
    def from_environment(cls) -> "AppConfig":
        dotenv_loaded = load_dotenv_file(DOTENV_PATH)

        runtime_mode = _read_runtime_mode()
        render_environment = bool(os.getenv("RENDER"))
        discord_token = _require_env("DISCORD_TOKEN")
        discord_guild_id = _read_optional_int("DISCORD_GUILD_ID")
        port = _read_int("PORT", DEFAULT_PORT)
        database_url = os.getenv("DATABASE_URL") or None

        return cls(
            runtime_mode=runtime_mode,
            render_environment=render_environment,
            dotenv_loaded=dotenv_loaded,
            discord_token=discord_token,
            discord_guild_id=discord_guild_id,
            port=port,
            database_url=database_url,
        )


def load_dotenv_file(path: Path) -> bool:
    if not path.is_file():
        return False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue

        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = _unquote(raw_value.strip())
        if key and key not in os.environ:
            os.environ[key] = value

    return True


def _read_runtime_mode() -> RuntimeMode:
    raw_mode = os.getenv("BOT_RUNTIME")
    if raw_mode:
        try:
            return RuntimeMode(raw_mode.lower())
        except ValueError as exc:
            valid = ", ".join(mode.value for mode in RuntimeMode)
            raise RuntimeError(f"BOT_RUNTIME must be one of: {valid}") from exc

    if os.getenv("RENDER"):
        return RuntimeMode.production

    return RuntimeMode.local


def _read_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a valid integer") from exc


def _read_optional_int(name: str) -> int | None:
    raw_value = os.getenv(name)
    if raw_value in (None, ""):
        return None
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a valid integer") from exc


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value
