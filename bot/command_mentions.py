from __future__ import annotations

from typing import Any


def build_command_mentions(commands: list[Any]) -> dict[str, str]:
    mentions: dict[str, str] = {}
    for command in commands:
        command_id = getattr(command, "id", None)
        name = getattr(command, "name", None)
        if command_id is None or not name:
            continue

        options = list(getattr(command, "options", []) or [])
        subcommands = _subcommand_paths(name, options)
        if subcommands:
            for path in subcommands:
                mentions[f"/{path}"] = f"</{path}:{command_id}>"
        else:
            mentions[f"/{name}"] = f"</{name}:{command_id}>"
    return mentions


def command_mention(source: Any, command_path: str) -> str:
    path = command_path if command_path.startswith("/") else f"/{command_path}"
    mentions = getattr(source, "command_mentions", None)
    if isinstance(mentions, dict):
        return mentions.get(path, path)
    return path


def _subcommand_paths(root: str, options: list[Any]) -> list[str]:
    paths: list[str] = []
    for option in options:
        option_name = getattr(option, "name", None)
        if not option_name:
            continue
        child_options = list(getattr(option, "options", []) or [])
        option_type = str(getattr(option, "type", "")).lower()
        is_group = "subcommand_group" in option_type or "sub_command_group" in option_type
        is_subcommand = "subcommand" in option_type or "sub_command" in option_type

        if is_group and child_options:
            for child in child_options:
                child_name = getattr(child, "name", None)
                if child_name:
                    paths.append(f"{root} {option_name} {child_name}")
        elif is_subcommand or child_options:
            paths.append(f"{root} {option_name}")
    return paths
