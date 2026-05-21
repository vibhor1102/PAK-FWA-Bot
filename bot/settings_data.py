from __future__ import annotations

from .features import FeatureVisibility
from .state import AppState


def mask_value(value: str | None, *, visible_prefix: int = 4, visible_suffix: int = 4) -> str:
    if not value:
        return "missing"
    if len(value) <= visible_prefix + visible_suffix:
        return "*" * len(value)
    return f"{value[:visible_prefix]}…{value[-visible_suffix:]}"


def yes_no(flag: bool) -> str:
    return "enabled" if flag else "disabled"


def build_settings_snapshot(state: AppState, *, discord_ready: bool, latency_ms: int | None) -> dict[str, object]:
    config = state.config
    enabled_features = [
        spec.name
        for spec in state.feature_specs
        if spec.is_enabled(config.runtime_mode)
    ]

    sections = [
        {
            "title": "Runtime",
            "items": [
                ("Mode", config.runtime_mode.value),
                (".env loaded", yes_no(config.dotenv_loaded)),
                ("Render detected", yes_no(config.render_environment)),
                ("Port", str(config.port)),
            ],
        },
        {
            "title": "Discord",
            "items": [
                ("Token", "set" if config.discord_token else "missing"),
                ("Guild sync", "guild" if config.discord_guild_id else "global"),
                ("Guild ID", str(config.discord_guild_id) if config.discord_guild_id else "unset"),
                ("Client ready", yes_no(discord_ready)),
                ("Latency", "unknown" if latency_ms is None else f"{latency_ms} ms"),
            ],
        },
        {
            "title": "Database",
            "items": [
                ("Configured", yes_no(state.database.configured)),
                ("Connected", yes_no(state.database.connected)),
                ("Connection string", mask_value(state.database.dsn)),
            ],
        },
        {
            "title": "Clash of Clans",
            "items": [
                ("Configured", yes_no(state.coc_service.configured)),
                ("Auth mode", state.coc_service.auth_mode),
                ("Key count", str(config.coc_key_count)),
                ("Key names", config.coc_key_names),
                ("Key scopes", ", ".join(config.coc_key_scopes) if config.coc_key_scopes else "unset"),
                ("Throttle limit", str(config.coc_throttle_limit)),
            ],
        },
        {
            "title": "Features",
            "items": [
                ("Enabled now", ", ".join(enabled_features) if enabled_features else "none"),
                ("Gate types", ", ".join(visibility.value for visibility in FeatureVisibility)),
            ],
        },
    ]

    return {
        "title": "PAK FWA Bot Settings",
        "runtime_mode": config.runtime_mode.value,
        "sections": sections,
    }
