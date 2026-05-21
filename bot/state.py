from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .coc_service import CocService
from .database import Database
from .fwa_sources import FwaFarmService, FwaStatsService
from .features import FeatureSpec


@dataclass(slots=True)
class AppState:
    config: AppConfig
    database: Database
    coc_service: CocService
    fwa_service: FwaFarmService
    fwa_stats_service: FwaStatsService
    feature_specs: tuple[FeatureSpec, ...]
