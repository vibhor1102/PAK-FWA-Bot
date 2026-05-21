from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .coc_service import CocService
from .database import Database
from .features import FeatureSpec


@dataclass(slots=True)
class AppState:
    config: AppConfig
    database: Database
    coc_service: CocService
    feature_specs: tuple[FeatureSpec, ...]
