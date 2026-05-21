from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from discord import app_commands

from .config import RuntimeMode


class FeatureVisibility(StrEnum):
    all = "all"
    local_only = "local_only"
    testing_only = "testing_only"
    production_only = "production_only"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    visibility: FeatureVisibility
    command: app_commands.Command[Any, ..., None]

    def is_enabled(self, runtime_mode: RuntimeMode) -> bool:
        return visibility_allows(self.visibility, runtime_mode)


def visibility_allows(visibility: FeatureVisibility, runtime_mode: RuntimeMode) -> bool:
    if visibility == FeatureVisibility.all:
        return True
    if visibility == FeatureVisibility.local_only:
        return runtime_mode == RuntimeMode.local
    if visibility == FeatureVisibility.testing_only:
        return runtime_mode == RuntimeMode.testing
    if visibility == FeatureVisibility.production_only:
        return runtime_mode == RuntimeMode.production
    return False
