from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.resolver import LinkResolutionError, LinkResolver, normalize_tag


class NormalizeTagTests(unittest.TestCase):
    def test_normalize_tag_adds_hash_and_uppercases(self) -> None:
        self.assertEqual(normalize_tag("  pyl "), "#PYL")

    def test_normalize_tag_replaces_letter_o(self) -> None:
        self.assertEqual(normalize_tag("po9"), "#P09")

    def test_normalize_tag_rejects_invalid_tags(self) -> None:
        with self.assertRaises(ValueError):
            normalize_tag("bad!")


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_clan_tag_wins_before_database(self) -> None:
        database = SimpleNamespace(
            find_server_clan=AsyncMock(return_value=None),
            get_user_profile=AsyncMock(),
        )
        state = SimpleNamespace(database=database)
        interaction = SimpleNamespace(guild_id=1, channel_id=2, user=SimpleNamespace(id=3))

        resolved = await LinkResolver(state).resolve_clan_tag(interaction, "pyl")

        self.assertEqual(resolved.tag, "#PYL")
        self.assertEqual(resolved.source, "explicit tag")
        database.find_server_clan.assert_not_awaited()
        database.get_user_profile.assert_not_awaited()

    async def test_channel_clan_precedes_user_clan(self) -> None:
        database = SimpleNamespace(
            find_server_clan=AsyncMock(
                return_value={
                    "clan_tag": "#CHAN",
                    "clan_name": "Channel Clan",
                    "nickname": None,
                }
            ),
            get_user_profile=AsyncMock(
                return_value={
                    "clan_tag": "#USER",
                    "clan_name": "User Clan",
                }
            ),
        )
        state = SimpleNamespace(database=database)
        interaction = SimpleNamespace(guild_id=1, channel_id=2, user=SimpleNamespace(id=3))

        resolved = await LinkResolver(state).resolve_clan_tag(interaction, None)

        self.assertEqual(resolved.tag, "#CHAN")
        self.assertEqual(resolved.source, "channel default")
        database.get_user_profile.assert_not_awaited()

    async def test_user_clan_precedes_server_default(self) -> None:
        async def find_server_clan(**kwargs):
            if kwargs.get("channel_id") is not None:
                return None
            return {"clan_tag": "#SERVER", "clan_name": "Server Clan", "nickname": None}

        database = SimpleNamespace(
            find_server_clan=AsyncMock(side_effect=find_server_clan),
            get_user_profile=AsyncMock(return_value={"clan_tag": "#USER", "clan_name": "User Clan"}),
        )
        state = SimpleNamespace(database=database)
        interaction = SimpleNamespace(guild_id=1, channel_id=2, user=SimpleNamespace(id=3))

        resolved = await LinkResolver(state).resolve_clan_tag(interaction, None)

        self.assertEqual(resolved.tag, "#USER")
        self.assertEqual(resolved.source, "linked user clan")

    async def test_missing_clan_has_friendly_error(self) -> None:
        database = SimpleNamespace(
            find_server_clan=AsyncMock(return_value=None),
            get_user_profile=AsyncMock(return_value=None),
        )
        state = SimpleNamespace(database=database)
        interaction = SimpleNamespace(guild_id=1, channel_id=2, user=SimpleNamespace(id=3))

        with self.assertRaisesRegex(LinkResolutionError, "/setup clan"):
            await LinkResolver(state).resolve_clan_tag(interaction, None)

    async def test_default_player_precedes_last_player(self) -> None:
        database = SimpleNamespace(
            get_default_player_link=AsyncMock(
                return_value={"player_tag": "#PLAYER", "player_name": "Linked Player"}
            ),
            get_user_profile=AsyncMock(return_value={"last_player_tag": "#LAST"}),
        )
        state = SimpleNamespace(database=database)
        interaction = SimpleNamespace(user=SimpleNamespace(id=3))

        resolved = await LinkResolver(state).resolve_player_tag(interaction, None)

        self.assertEqual(resolved.tag, "#PLAYER")
        self.assertEqual(resolved.source, "default linked player")
        database.get_user_profile.assert_not_awaited()


class DatabaseHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_user_uses_stable_query_parameters(self) -> None:
        from bot.database import Database

        pool = SimpleNamespace(execute=AsyncMock())
        database = Database("postgres://user:pass@example/db")
        database.pool = pool

        await database.upsert_user(user_id=123, username="v", display_name="Vibhor")

        pool.execute.assert_awaited_once()
        _, user_id, username, display_name = pool.execute.await_args.args
        self.assertEqual((user_id, username, display_name), ("123", "v", "Vibhor"))


class ConfigTests(unittest.TestCase):
    def test_database_pool_defaults_are_small_and_lazy(self) -> None:
        from bot.config import AppConfig

        with (
            patch.dict("os.environ", {"DISCORD_TOKEN": "token"}, clear=True),
            patch("bot.config.DOTENV_PATH", SimpleNamespace(is_file=lambda: False)),
        ):
            config = AppConfig.from_environment()

        self.assertEqual(config.database_pool_min_size, 0)
        self.assertEqual(config.database_pool_max_size, 3)
        self.assertEqual(config.clan_dashboard_refresh_seconds, 300)
        self.assertEqual(config.clan_dashboard_interaction_reset_minutes, 20)
        self.assertEqual(config.clan_activity_retention_days, 45)
        self.assertEqual(config.clan_activity_poll_seconds, 900)

    def test_database_pool_min_cannot_exceed_max(self) -> None:
        from bot.config import AppConfig

        with (
            patch.dict(
                "os.environ",
                {
                    "DISCORD_TOKEN": "token",
                    "DATABASE_POOL_MIN_SIZE": "4",
                    "DATABASE_POOL_MAX_SIZE": "3",
                },
                clear=True,
            ),
            patch("bot.config.DOTENV_PATH", SimpleNamespace(is_file=lambda: False)),
        ):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_POOL_MIN_SIZE"):
                AppConfig.from_environment()


class CommandMentionTests(unittest.TestCase):
    def test_builds_subcommand_mentions(self) -> None:
        from bot.command_mentions import build_command_mentions, command_mention

        command = SimpleNamespace(
            id=42,
            name="link",
            options=[
                SimpleNamespace(name="create", type="subcommand", options=[]),
                SimpleNamespace(name="verify", type="subcommand", options=[]),
            ],
        )
        source = SimpleNamespace(command_mentions=build_command_mentions([command]))

        self.assertEqual(command_mention(source, "/link create"), "</link create:42>")
        self.assertEqual(command_mention(source, "link verify"), "</link verify:42>")

    def test_command_mention_falls_back_before_sync(self) -> None:
        from bot.command_mentions import command_mention

        self.assertEqual(command_mention(SimpleNamespace(), "/setup clan"), "/setup clan")


class ClanAutocompleteTests(unittest.TestCase):
    def test_clan_choices_match_alias_name_and_tag(self) -> None:
        from bot.autocomplete import _choices_from_clans

        choices = _choices_from_clans(
            [
                {
                    "clan_name": "PAK Originals",
                    "clan_tag": "#ABC",
                    "alias": "pak",
                    "nickname": "Main",
                }
            ],
            "pak",
        )

        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0].value, "#ABC")
        self.assertIn("alias pak", choices[0].name)

    def test_csv_autocomplete_preserves_prior_values(self) -> None:
        from bot.autocomplete import _split_csv_current

        self.assertEqual(_split_csv_current("#AAA, pa"), ("#AAA, ", "pa"))


class ClanDashboardTests(unittest.TestCase):
    def test_page_and_sort_helpers_are_stable(self) -> None:
        from bot.clan_dashboard import next_sort, normalize_page

        self.assertEqual(normalize_page("Clan-Games"), "clan_games")
        self.assertEqual(normalize_page("bad"), "overview")
        self.assertEqual(next_sort("donations", None), "donated")
        self.assertEqual(next_sort("donations", "donated"), "received")
        self.assertIsNone(next_sort("overview", None))

    def test_code_block_trims_to_embed_safe_size(self) -> None:
        from bot.clan_dashboard import code_block

        text = code_block(["x" * 5000])

        self.assertLess(len(text), 4096)
        self.assertIn("trimmed", text)

    def test_member_rows_normalize_columns(self) -> None:
        from bot.clan_dashboard import member_rows

        clan = SimpleNamespace(
            members=[
                SimpleNamespace(
                    tag="#AAA",
                    name="Leader",
                    town_hall_level=16,
                    role="coLeader",
                    trophies=5000,
                    donations=10,
                    donations_received=5,
                    war_preference="in",
                )
            ]
        )

        rows = member_rows(clan)

        self.assertEqual(rows[0]["role"], "Co")
        self.assertEqual(rows[0]["ratio"], 2)
        self.assertEqual(rows[0]["war"], "in")


class DashboardDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_dashboard_encodes_selected_tags(self) -> None:
        from bot.database import Database

        pool = SimpleNamespace(
            fetchrow=AsyncMock(
                return_value={
                    "id": 1,
                    "guild_id": "1",
                    "channel_id": "2",
                    "default_clan_tag": "#AAA",
                    "default_page": "overview",
                    "selected_clan_tags": '["#AAA", "#BBB"]',
                }
            )
        )
        database = Database("postgres://user:pass@example/db")
        database.pool = pool

        row = await database.upsert_clan_dashboard(
            guild_id=1,
            channel_id=2,
            message_id=None,
            default_clan_tag="#AAA",
            default_page="overview",
            selected_clan_tags=["#AAA", "#BBB"],
            reset_minutes=20,
            show_public_controls=True,
        )

        self.assertEqual(row["selected_clan_tags"], ["#AAA", "#BBB"])
        self.assertIn('["#AAA", "#BBB"]', pool.fetchrow.await_args.args)


class FwaCommandTests(unittest.TestCase):
    def test_copy_block_uses_actual_emoji(self) -> None:
        from bot.commands.fwa import FwaEvent, _copy_block

        event = FwaEvent(kind="blacklist", opponent_name="OZ Taskforce", war_record=SimpleNamespace())
        text = _copy_block(event)

        self.assertIn("🟥Blacklist War against OZ Taskforce 🟥", text)
        self.assertIn("📌ACTIVATE WAR BASES", text)
        self.assertNotIn(":red_square:", text)

    def test_event_mapping_detects_mismatch(self) -> None:
        from bot.commands.fwa import _event_from_record

        record = SimpleNamespace(result="inWar", opponent_info="Mismatch", opponent_name="Juicer Esports", matched=False)
        war = SimpleNamespace(opponent=SimpleNamespace(name="Juicer Esports"))

        event = _event_from_record(record, war)

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "mismatch")
        self.assertEqual(event.classification, "mismatch")

    def test_event_mapping_treats_unknown_unmatched_as_mismatch(self) -> None:
        from bot.commands.fwa import _event_from_record

        record = SimpleNamespace(result="preparation", opponent_info="Unknown", opponent_name="StrawHats", matched=False)
        war = SimpleNamespace(opponent=SimpleNamespace(name="StrawHats"))

        event = _event_from_record(record, war)

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "mismatch")

    def test_event_mapping_detects_blacklist(self) -> None:
        from bot.commands.fwa import _event_from_record

        record = SimpleNamespace(result="preparation", opponent_info="Blacklisted", opponent_name="OZ Taskforce", matched=False)
        war = SimpleNamespace(opponent=SimpleNamespace(name="OZ Taskforce"))

        event = _event_from_record(record, war)

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "blacklist")

    def test_preparation_fwa_event_uses_points_farm_to_choose_win(self) -> None:
        from bot.commands.fwa import _event_from_record

        record = SimpleNamespace(
            result="preparation",
            opponent_info="FWA",
            opponent_name="Vixen Raiders",
            matched=True,
        )
        war = SimpleNamespace(opponent=SimpleNamespace(name="Vixen Raiders"))
        primary_points = SimpleNamespace(point_balance=9)
        opponent_points = SimpleNamespace(point_balance=8)

        event = _event_from_record(record, war, primary_points, opponent_points)

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "win")
        self.assertEqual(event.primary_points, 9)
        self.assertEqual(event.opponent_points, 8)

    def test_preparation_fwa_event_uses_points_farm_to_choose_loss(self) -> None:
        from bot.commands.fwa import _event_from_record

        record = SimpleNamespace(
            result="preparation",
            opponent_info="FWA",
            opponent_name="Adult Clan",
            matched=True,
        )
        war = SimpleNamespace(opponent=SimpleNamespace(name="Adult Clan"))
        primary_points = SimpleNamespace(point_balance=2)
        opponent_points = SimpleNamespace(point_balance=5)

        event = _event_from_record(record, war, primary_points, opponent_points)

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "lose")

    def test_fallback_main_message_removes_code_fence(self) -> None:
        from bot.commands.fwa import FwaEvent, _fallback_main_message

        event = FwaEvent(kind="win", opponent_name="NO WAR", war_record=SimpleNamespace())
        text = _fallback_main_message(event)

        self.assertTrue(text.startswith("🟩WIN WAR vs NO WAR 🟩"))
        self.assertNotIn("```", text)
        self.assertNotIn("FWA points", text)


class WarMonitorTests(unittest.TestCase):
    def test_external_lookup_only_inside_configured_window(self) -> None:
        from bot.war_monitor import _external_lookup_allowed

        now = datetime.now(timezone.utc)
        bot = SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    fwa_external_lookup_start_hours=2,
                    fwa_external_lookup_end_hours=4,
                )
            )
        )
        inside = SimpleNamespace(preparation_start_time=SimpleNamespace(time=now - timedelta(hours=3)))
        too_early = SimpleNamespace(preparation_start_time=SimpleNamespace(time=now - timedelta(hours=1)))
        too_late = SimpleNamespace(preparation_start_time=SimpleNamespace(time=now - timedelta(hours=5)))

        self.assertTrue(_external_lookup_allowed(bot, inside))
        self.assertFalse(_external_lookup_allowed(bot, too_early))
        self.assertFalse(_external_lookup_allowed(bot, too_late))

    def test_war_key_includes_opponent_and_start_time(self) -> None:
        from bot.war_monitor import _war_key

        started = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(
            _war_key("#AAA", "#BBB", started, None),
            "#AAA:#BBB:2026-05-27T12:00:00+00:00",
        )

    def test_monitor_rehydrates_known_fwa_instruction_from_snapshot(self) -> None:
        from bot.war_monitor import MonitoredWar, _hydrate_from_snapshot

        war = MonitoredWar(
            clan_tag="#AAA",
            clan_name="Our Clan",
            opponent_tag="#BBB",
            opponent_name="Enemy Clan",
            state="preparation",
            war_key="#AAA:#BBB:2026-05-27T12:00:00+00:00",
            preparation_start=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
            battle_start=None,
            battle_end=None,
            team_size=50,
        )

        _hydrate_from_snapshot(
            war,
            {
                "war_key": "#AAA:#BBB:2026-05-27T12:00:00+00:00",
                "planned_result": "win",
                "opponent_name": "Enemy Clan",
                "fwa_classification": "fwa",
            },
        )

        self.assertEqual(war.event_kind, "win")
        self.assertIn("🟩WIN WAR vs Enemy Clan 🟩", war.event_message or "")

    def test_monitor_ignores_snapshot_from_previous_war(self) -> None:
        from bot.war_monitor import MonitoredWar, _hydrate_from_snapshot

        war = MonitoredWar(
            clan_tag="#AAA",
            clan_name="Our Clan",
            opponent_tag="#BBB",
            opponent_name="Enemy Clan",
            state="preparation",
            war_key="#AAA:#BBB:new",
            preparation_start=None,
            battle_start=None,
            battle_end=None,
            team_size=50,
        )

        _hydrate_from_snapshot(war, {"war_key": "#AAA:#BBB:old", "planned_result": "win"})

        self.assertIsNone(war.event_message)


class ExternalLookupGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_gate_allows_small_different_clan_burst(self) -> None:
        from bot.external_lookup_gate import ExternalLookupGate

        config = SimpleNamespace(
            manual_external_lookup_tag_cooldown_seconds=180,
            automatic_external_lookup_tag_cooldown_seconds=900,
            external_lookup_user_burst_per_minute=3,
            external_lookup_guild_burst_per_minute=12,
        )
        gate = ExternalLookupGate(config)

        decisions = [
            await gate.acquire_manual(clan_tag=tag, user_id=1, guild_id=2, last_checked_at=None)
            for tag in ("#AAA", "#BBB", "#CCC")
        ]

        self.assertTrue(all(decision.allowed for decision in decisions))

    async def test_manual_gate_cools_down_same_clan(self) -> None:
        from bot.external_lookup_gate import ExternalLookupGate

        config = SimpleNamespace(
            manual_external_lookup_tag_cooldown_seconds=180,
            automatic_external_lookup_tag_cooldown_seconds=900,
            external_lookup_user_burst_per_minute=3,
            external_lookup_guild_burst_per_minute=12,
        )
        gate = ExternalLookupGate(config)

        first = await gate.acquire_manual(clan_tag="#AAA", user_id=1, guild_id=2, last_checked_at=None)
        second = await gate.acquire_manual(clan_tag="#AAA", user_id=1, guild_id=2, last_checked_at=None)

        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertGreater(second.retry_after_seconds, 0)

    def test_manual_wait_message_hides_technical_details(self) -> None:
        from bot.commands.fwa import _manual_lookup_wait_message

        text = _manual_lookup_wait_message(180)

        self.assertIn("Try again", text)
        self.assertNotIn("cache", text.lower())
        self.assertNotIn("rate", text.lower())


class AutoroleTests(unittest.TestCase):
    def test_rank_normalization_and_weight(self) -> None:
        from bot.autorole import normalize_clan_role, rank_weight_for_role

        self.assertEqual(normalize_clan_role("coLeader"), "co_leader")
        self.assertEqual(normalize_clan_role("admin"), "elder")
        self.assertGreater(rank_weight_for_role("leader"), rank_weight_for_role("elder"))

    def test_desired_roles_union_multiple_accounts(self) -> None:
        from bot.autorole import desired_roles_by_user

        config = {
            "general_role_id": "10",
            "leader_role_id": "11",
            "co_leader_role_id": "12",
            "elder_role_id": "13",
            "member_role_id": "14",
        }
        rows = [
            {"user_id": "1", "clan_role": "leader"},
            {"user_id": "1", "clan_role": "member"},
        ]

        desired = desired_roles_by_user(config, rows)

        self.assertEqual(desired["1"], {"10", "11", "14"})

    def test_desired_roles_uses_highest_rank_rows_from_database(self) -> None:
        from bot.autorole import desired_roles_by_user

        config = {
            "general_role_id": "10",
            "leader_role_id": "11",
            "co_leader_role_id": "12",
            "elder_role_id": "13",
            "member_role_id": "14",
        }
        rows = [{"user_id": "1", "clan_role": "co_leader"}]

        self.assertEqual(desired_roles_by_user(config, rows)["1"], {"10", "12"})

    def test_autorole_permission_prompt_mentions_manage_roles(self) -> None:
        from bot.commands.autorole import _bot_role_setup_issue

        me = SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=False))
        interaction = SimpleNamespace(guild=SimpleNamespace(me=me))

        self.assertIn("Manage Roles", _bot_role_setup_issue(interaction) or "")

    def test_autorole_hierarchy_prompt_names_blocked_role(self) -> None:
        from bot.commands.autorole import _role_hierarchy_issue

        class Role:
            def __init__(self, name: str, position: int) -> None:
                self.name = name
                self.position = position
                self.mention = f"@{name}"

            def __ge__(self, other: "Role") -> bool:
                return self.position >= other.position

        me = SimpleNamespace(top_role=Role("Bot", 5))
        interaction = SimpleNamespace(guild=SimpleNamespace(me=me))

        self.assertIn("@Leader", _role_hierarchy_issue(interaction, Role("Leader", 6)) or "")


class SettingsHubTests(unittest.TestCase):
    def test_regular_user_sees_only_user_page(self) -> None:
        from bot.commands.settings import available_pages

        interaction = SimpleNamespace(permissions=SimpleNamespace(manage_guild=False, manage_roles=False))

        self.assertEqual(available_pages(interaction), [("user", "My Setup")])

    def test_manager_pages_follow_permissions(self) -> None:
        from bot.commands.settings import available_pages

        interaction = SimpleNamespace(permissions=SimpleNamespace(manage_guild=True, manage_roles=True))

        self.assertEqual(
            available_pages(interaction),
            [
                ("user", "My Setup"),
                ("clans", "Clans"),
                ("feeds", "Feeds"),
                ("autoroles", "Autoroles"),
                ("system", "System"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
