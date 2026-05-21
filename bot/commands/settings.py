from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from ..settings_data import build_settings_snapshot


class SettingsPageButton(discord.ui.Button):
    def __init__(self, view: "SettingsDashboardView", label: str, page_index: int, *, row: int) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self._view_ref = view
        self._page_index = page_index

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.switch_page(interaction, self._page_index)


class SettingsRefreshButton(discord.ui.Button):
    def __init__(self, view: "SettingsDashboardView", *, row: int) -> None:
        super().__init__(
            label="Refresh",
            style=discord.ButtonStyle.success,
            row=row,
        )
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.refresh(interaction)


class SettingsDashboardView(discord.ui.View):
    def __init__(self, bot: Any, *, page_index: int = 0) -> None:
        super().__init__(timeout=600)
        self._bot = bot
        self._settings_snapshot = self._build_snapshot()
        self._page_titles = ["Overview"] + [section["title"] for section in self._settings_snapshot["sections"]]
        self._page_index = max(0, min(page_index, len(self._page_titles) - 1))
        self._build_buttons()

    def _build_snapshot(self) -> dict[str, object]:
        return build_settings_snapshot(
            self._bot.state,
            discord_ready=self._bot.is_ready(),
            latency_ms=None if self._bot.latency is None else round(self._bot.latency * 1000),
        )

    def _build_buttons(self) -> None:
        for index, title in enumerate(self._page_titles):
            row = index // 5
            button = SettingsPageButton(self, self._button_label(title), index, row=row)
            if index == self._page_index:
                button.style = discord.ButtonStyle.primary
            self.add_item(button)

        refresh_row = len(self._page_titles) // 5
        self.add_item(SettingsRefreshButton(self, row=refresh_row))

    async def switch_page(self, interaction: discord.Interaction, page_index: int) -> None:
        new_view = SettingsDashboardView(self._bot, page_index=page_index)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

    async def refresh(self, interaction: discord.Interaction) -> None:
        new_view = SettingsDashboardView(self._bot, page_index=self._page_index)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

    def build_embed(self) -> discord.Embed:
        runtime_mode = str(self._settings_snapshot["runtime_mode"])
        section_titles = [section["title"] for section in self._settings_snapshot["sections"]]
        current_title = self._page_titles[self._page_index]

        embed = discord.Embed(
            title="PAK FWA Bot Settings",
            description=(
                "Interactive configuration dashboard. Use the buttons below to switch sections and refresh live state."
            ),
            color=discord.Color.teal(),
        )
        embed.set_author(name=f"Page {self._page_index + 1}/{len(self._page_titles)}")
        embed.set_footer(text=f"Runtime: {runtime_mode} • Sections: {len(section_titles)}")

        if self._page_index == 0:
            embed.add_field(
                name="Overview",
                value="\n".join(self._overview_lines()),
                inline=False,
            )
            for section in self._settings_snapshot["sections"]:
                embed.add_field(
                    name=section["title"],
                    value=self._section_summary(section),
                    inline=False,
                )
        else:
            section = self._settings_snapshot["sections"][self._page_index - 1]
            embed.description = f"Live details for the `{current_title}` section."
            for label, value in section["items"]:
                embed.add_field(name=label, value=value, inline=False)

        return embed

    def _overview_lines(self) -> list[str]:
        sections = self._settings_snapshot["sections"]
        lines = [
            "This dashboard is designed to grow with the bot.",
            "Each section below opens its own live card, and Refresh pulls the latest runtime state again.",
        ]
        for section in sections:
            lines.append(f"{section['title']}: {self._section_summary(section)}")
        return lines

    def _section_summary(self, section: dict[str, object]) -> str:
        items = section["items"]
        if not isinstance(items, list) or not items:
            return "No values available."
        preview: list[str] = []
        for label, value in items[:2]:
            preview.append(f"{label}: {value}")
        return " | ".join(preview)

    @staticmethod
    def _button_label(title: str) -> str:
        return title if len(title) <= 18 else f"{title[:15]}..."


def build_settings_command() -> app_commands.Command[Any, ..., None]:
    async def settings_callback(interaction: discord.Interaction) -> None:
        bot = interaction.client
        if bot is None or not hasattr(bot, "state"):
            await interaction.response.send_message(
                "Settings are unavailable right now.",
                ephemeral=False,
            )
            return

        view = SettingsDashboardView(bot)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=False)

    return app_commands.Command(
        name="settings",
        description="Show live runtime settings for this bot.",
        callback=settings_callback,
    )
