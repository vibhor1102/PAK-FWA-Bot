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


def build_settings_snapshot(
    state: AppState,
    *,
    discord_ready: bool,
    latency_ms: int | None,
    linking_counts: dict[str, int] | None = None,
) -> dict[str, object]:
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
                ("Pool size", f"{config.database_pool_min_size}-{config.database_pool_max_size}"),
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
            "title": "Proactive War Monitor",
            "items": [
                ("Enabled", yes_no(state.database.connected and state.coc_service.configured)),
                ("Poll interval", f"{config.war_monitor_interval_seconds}s"),
                (
                    "FWA/points window",
                    f"{config.fwa_external_lookup_start_hours}-{config.fwa_external_lookup_end_hours}h after match",
                ),
                ("Manual same-clan cooldown", f"{config.manual_external_lookup_tag_cooldown_seconds}s"),
                ("Auto same-clan cooldown", f"{config.automatic_external_lookup_tag_cooldown_seconds}s"),
                ("Manual user burst", f"{config.external_lookup_user_burst_per_minute}/min"),
                ("Manual server burst", f"{config.external_lookup_guild_burst_per_minute}/min"),
                ("Announcement channels", str((linking_counts or {}).get("announcement_channels", 0))),
                ("War snapshots", str((linking_counts or {}).get("war_snapshots", 0))),
            ],
        },
        {
            "title": "Features",
            "items": [
                ("Enabled now", ", ".join(enabled_features) if enabled_features else "none"),
                ("Gate types", ", ".join(visibility.value for visibility in FeatureVisibility)),
            ],
        },
        {
            "title": "Autorole",
            "items": [
                ("Enabled", yes_no(state.database.connected and state.coc_service.configured)),
                ("Sync interval", f"{config.autorole_sync_interval_seconds}s"),
                ("Grace retention", f"{config.autorole_retention_days} days"),
                ("Configured clans", str((linking_counts or {}).get("autorole_configs", 0))),
            ],
        },
        {
            "title": "Clan Dashboard",
            "items": [
                ("Enabled", yes_no(state.database.connected and state.coc_service.configured)),
                ("Refresh interval", f"{config.clan_dashboard_refresh_seconds}s"),
                ("Activity poll", f"{config.clan_activity_poll_seconds}s"),
                ("Activity retention", f"{config.clan_activity_retention_days} days"),
                ("Default reset", f"{config.clan_dashboard_interaction_reset_minutes} minutes"),
                ("Dashboards", str((linking_counts or {}).get("clan_dashboards", 0))),
                ("Activity events", str((linking_counts or {}).get("clan_activity_events", 0))),
            ],
        },
        {
            "title": "Linking",
            "items": [
                ("Ready", yes_no(state.database.connected)),
                ("Server clans", str((linking_counts or {}).get("server_clans", 0))),
                ("User clans", str((linking_counts or {}).get("user_clans", 0))),
                ("Player links", str((linking_counts or {}).get("player_links", 0))),
                ("Verified players", str((linking_counts or {}).get("verified_players", 0))),
            ],
        },
    ]

    return {
        "title": "PAK FWA Bot Settings",
        "runtime_mode": config.runtime_mode.value,
        "sections": sections,
    }
