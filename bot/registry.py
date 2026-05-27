from __future__ import annotations

from .commands import (
    build_autorole_group,
    build_clan_command,
    build_clan_report_command,
    build_fwa_command,
    build_help_command,
    build_link_group,
    build_player_command,
    build_profile_command,
    build_settings_command,
    build_setup_group,
)
from .features import FeatureSpec, FeatureVisibility


def build_feature_specs() -> tuple[FeatureSpec, ...]:
    return (
        FeatureSpec(
            name="help",
            visibility=FeatureVisibility.all,
            command=build_help_command(),
        ),
        FeatureSpec(
            name="settings",
            visibility=FeatureVisibility.all,
            command=build_settings_command(),
        ),
        FeatureSpec(
            name="setup",
            visibility=FeatureVisibility.all,
            command=build_setup_group(),
        ),
        FeatureSpec(
            name="link",
            visibility=FeatureVisibility.all,
            command=build_link_group(),
        ),
        FeatureSpec(
            name="profile",
            visibility=FeatureVisibility.all,
            command=build_profile_command(),
        ),
        FeatureSpec(
            name="player",
            visibility=FeatureVisibility.all,
            command=build_player_command(),
        ),
        FeatureSpec(
            name="clan",
            visibility=FeatureVisibility.all,
            command=build_clan_command(),
        ),
        FeatureSpec(
            name="clanreport",
            visibility=FeatureVisibility.all,
            command=build_clan_report_command(),
        ),
        FeatureSpec(
            name="fwa",
            visibility=FeatureVisibility.all,
            command=build_fwa_command(),
        ),
        FeatureSpec(
            name="autorole",
            visibility=FeatureVisibility.all,
            command=build_autorole_group(),
        ),
    )
