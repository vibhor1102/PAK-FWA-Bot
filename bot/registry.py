from __future__ import annotations

from .commands import build_clan_report_command, build_help_command, build_settings_command
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
            name="clanreport",
            visibility=FeatureVisibility.all,
            command=build_clan_report_command(),
        ),
    )
