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
    database_pool_min_size: int
    database_pool_max_size: int
    coc_email: str | None
    coc_password: str | None
    coc_tokens: tuple[str, ...]
    coc_key_count: int
    coc_key_names: str
    coc_key_scopes: tuple[str, ...]
    coc_throttle_limit: int
    war_monitor_interval_seconds: int
    fwa_external_lookup_start_hours: int
    fwa_external_lookup_end_hours: int
    manual_external_lookup_tag_cooldown_seconds: int
    automatic_external_lookup_tag_cooldown_seconds: int
    external_lookup_user_burst_per_minute: int
    external_lookup_guild_burst_per_minute: int
    autorole_sync_interval_seconds: int
    autorole_retention_days: int
    clan_dashboard_refresh_seconds: int
    clan_dashboard_interaction_reset_minutes: int
    clan_activity_retention_days: int
    clan_activity_poll_seconds: int

    @classmethod
    def from_environment(cls) -> "AppConfig":
        dotenv_loaded = load_dotenv_file(DOTENV_PATH)

        runtime_mode = _read_runtime_mode()
        render_environment = bool(os.getenv("RENDER"))
        discord_token = _require_env("DISCORD_TOKEN")
        discord_guild_id = _read_optional_int("DISCORD_GUILD_ID")
        port = _read_int("PORT", DEFAULT_PORT)
        database_url = os.getenv("DATABASE_URL") or None
        database_pool_min_size = _read_non_negative_int("DATABASE_POOL_MIN_SIZE", 0)
        database_pool_max_size = _read_positive_int("DATABASE_POOL_MAX_SIZE", 3)
        if database_pool_min_size > database_pool_max_size:
            raise RuntimeError("DATABASE_POOL_MIN_SIZE must be less than or equal to DATABASE_POOL_MAX_SIZE")
        coc_email = os.getenv("COC_EMAIL") or None
        coc_password = os.getenv("COC_PASSWORD") or None
        coc_tokens = _read_csv("COC_TOKENS")
        coc_key_count = _read_positive_int("COC_KEY_COUNT", 1)
        coc_key_names = _read_first_env(
            "COC_KEY_NAME",
            "COC_KEY_NAMES",
            default="PAK FWA Bot",
        )
        coc_key_scopes = _read_csv("COC_KEY_SCOPES")
        coc_throttle_limit = _read_positive_int("COC_THROTTLE_LIMIT", 30)
        war_monitor_interval_seconds = _read_positive_int("WAR_MONITOR_INTERVAL_SECONDS", 300)
        fwa_external_lookup_start_hours = _read_positive_int("FWA_EXTERNAL_LOOKUP_START_HOURS", 2)
        fwa_external_lookup_end_hours = _read_positive_int("FWA_EXTERNAL_LOOKUP_END_HOURS", 4)
        manual_external_lookup_tag_cooldown_seconds = _read_positive_int(
            "MANUAL_EXTERNAL_LOOKUP_TAG_COOLDOWN_SECONDS",
            180,
        )
        automatic_external_lookup_tag_cooldown_seconds = _read_positive_int(
            "AUTOMATIC_EXTERNAL_LOOKUP_TAG_COOLDOWN_SECONDS",
            900,
        )
        external_lookup_user_burst_per_minute = _read_positive_int("EXTERNAL_LOOKUP_USER_BURST_PER_MINUTE", 3)
        external_lookup_guild_burst_per_minute = _read_positive_int("EXTERNAL_LOOKUP_GUILD_BURST_PER_MINUTE", 12)
        autorole_sync_interval_seconds = _read_positive_int("AUTOROLE_SYNC_INTERVAL_SECONDS", 1800)
        autorole_retention_days = _read_positive_int("AUTOROLE_RETENTION_DAYS", 3)
        clan_dashboard_refresh_seconds = _read_positive_int("CLAN_DASHBOARD_REFRESH_SECONDS", 300)
        clan_dashboard_interaction_reset_minutes = _read_positive_int(
            "CLAN_DASHBOARD_INTERACTION_RESET_MINUTES",
            20,
        )
        clan_activity_retention_days = _read_positive_int("CLAN_ACTIVITY_RETENTION_DAYS", 45)
        clan_activity_poll_seconds = _read_positive_int("CLAN_ACTIVITY_POLL_SECONDS", 900)
        if fwa_external_lookup_end_hours <= fwa_external_lookup_start_hours:
            raise RuntimeError("FWA_EXTERNAL_LOOKUP_END_HOURS must be greater than FWA_EXTERNAL_LOOKUP_START_HOURS")

        return cls(
            runtime_mode=runtime_mode,
            render_environment=render_environment,
            dotenv_loaded=dotenv_loaded,
            discord_token=discord_token,
            discord_guild_id=discord_guild_id,
            port=port,
            database_url=database_url,
            database_pool_min_size=database_pool_min_size,
            database_pool_max_size=database_pool_max_size,
            coc_email=coc_email,
            coc_password=coc_password,
            coc_tokens=coc_tokens,
            coc_key_count=coc_key_count,
            coc_key_names=coc_key_names,
            coc_key_scopes=coc_key_scopes,
            coc_throttle_limit=coc_throttle_limit,
            war_monitor_interval_seconds=war_monitor_interval_seconds,
            fwa_external_lookup_start_hours=fwa_external_lookup_start_hours,
            fwa_external_lookup_end_hours=fwa_external_lookup_end_hours,
            manual_external_lookup_tag_cooldown_seconds=manual_external_lookup_tag_cooldown_seconds,
            automatic_external_lookup_tag_cooldown_seconds=automatic_external_lookup_tag_cooldown_seconds,
            external_lookup_user_burst_per_minute=external_lookup_user_burst_per_minute,
            external_lookup_guild_burst_per_minute=external_lookup_guild_burst_per_minute,
            autorole_sync_interval_seconds=autorole_sync_interval_seconds,
            autorole_retention_days=autorole_retention_days,
            clan_dashboard_refresh_seconds=clan_dashboard_refresh_seconds,
            clan_dashboard_interaction_reset_minutes=clan_dashboard_interaction_reset_minutes,
            clan_activity_retention_days=clan_activity_retention_days,
            clan_activity_poll_seconds=clan_activity_poll_seconds,
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


def _read_positive_int(name: str, default: int) -> int:
    value = _read_int(name, default)
    if value < 1:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def _read_non_negative_int(name: str, default: int) -> int:
    value = _read_int(name, default)
    if value < 0:
        raise RuntimeError(f"{name} must be zero or greater")
    return value


def _read_csv(name: str) -> tuple[str, ...]:
    raw_value = os.getenv(name)
    if not raw_value:
        return ()

    return tuple(
        item.strip()
        for item in raw_value.split(",")
        if item.strip()
    )


def _read_first_env(*names: str, default: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


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
