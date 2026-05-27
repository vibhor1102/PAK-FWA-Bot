from .autorole import build_autorole_group
from .clan import build_clan_command
from .fwa import build_fwa_command
from .help import build_help_command
from .link import build_link_group
from .player import build_player_command
from .profile import build_profile_command
from .settings import build_settings_command
from .setup import build_setup_group

__all__ = [
    "build_clan_command",
    "build_autorole_group",
    "build_fwa_command",
    "build_help_command",
    "build_link_group",
    "build_player_command",
    "build_profile_command",
    "build_settings_command",
    "build_setup_group",
]
