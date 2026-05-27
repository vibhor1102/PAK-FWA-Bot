from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


if __name__ == "__main__":
    unittest.main()
