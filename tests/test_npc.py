"""Tests for NPC rival trainer system."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from config import Settings
from derby import repositories as repo
from derby.models import NPC, Race, RaceEntry, Racer
from derby.npc_quips import parse_quips, parse_used, pick_quip, should_regenerate
from derby.npc_generation import generate_racer_stats_for_rank, RANK_STAT_RANGES
from derby.scheduler import DerbyScheduler


GUILD_ID = 1
USER_ID = 100


class DummyChannel:
    def __init__(self, name: str = "general") -> None:
        self.name = name
        self.messages: list = []

    async def send(self, content=None, *, embed=None) -> None:
        if embed is not None:
            self.messages.append(embed)
        elif content is not None:
            self.messages.append(content)


class DummyGuild:
    def __init__(self, gid: int) -> None:
        self.id = gid
        self.system_channel = DummyChannel()
        self.text_channels = [self.system_channel]


class DummyBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.guilds: list[DummyGuild] = []
        self.users: dict = {}
        self.loop = asyncio.get_event_loop()
        self.logger = logging.getLogger("test")

    def get_guild(self, gid: int):
        for g in self.guilds:
            if g.id == gid:
                return g
        return None

    def get_user(self, uid: int):
        return self.users.get(uid)


def _make_bot() -> DummyBot:
    settings = Settings(
        race_times=["09:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=0,
        daily_min=15,
        daily_max=30,
    )
    bot = DummyBot(settings)
    bot.guilds.append(DummyGuild(GUILD_ID))
    return bot


async def _make_scheduler(bot: DummyBot, tmp_path: Path) -> DerbyScheduler:
    sched = DerbyScheduler(bot, db_path=str(tmp_path / "db.sqlite"))
    await sched._init_db()
    bot.scheduler = sched
    return sched


# ---------------------------------------------------------------------------
# Quip rotation unit tests
# ---------------------------------------------------------------------------


def test_pick_quip_selects_unused():
    """Picks from unused quips only."""
    quips = ["a", "b", "c", "d"]
    used = [0, 1]
    quip, new_used = pick_quip(quips, used)
    assert quip in ("c", "d")
    assert len(new_used) == 3


def test_pick_quip_wraps_when_exhausted():
    """Resets used list when all quips have been used."""
    quips = ["a", "b"]
    used = [0, 1]
    quip, new_used = pick_quip(quips, used)
    assert quip in ("a", "b")
    assert len(new_used) == 1  # reset + 1 new pick


def test_pick_quip_empty_pool():
    """Returns empty string for empty quip pool."""
    quip, used = pick_quip([], [])
    assert quip == ""


def test_should_regenerate_at_70_percent():
    """Threshold triggers at >= 70%."""
    quips = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    assert not should_regenerate(quips, [0, 1, 2, 3, 4, 5])  # 60%
    assert should_regenerate(quips, [0, 1, 2, 3, 4, 5, 6])  # 70%
    assert should_regenerate(quips, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])  # 100%


def test_should_regenerate_empty():
    """Empty pool never triggers regeneration."""
    assert not should_regenerate([], [])


def test_parse_quips():
    """JSON parsing of quip lists."""
    assert parse_quips('["hello", "world"]') == ["hello", "world"]
    assert parse_quips("invalid") == []
    assert parse_quips("") == []


def test_parse_used():
    """JSON parsing of used index lists."""
    assert parse_used("[0, 2, 5]") == [0, 2, 5]
    assert parse_used("invalid") == []


# ---------------------------------------------------------------------------
# Stat generation tests
# ---------------------------------------------------------------------------


def test_generate_racer_stats_within_rank():
    """Generated stats fall within the rank's total range."""
    for rank, (low, high) in RANK_STAT_RANGES.items():
        for _ in range(20):  # Run multiple times for randomness
            stats = generate_racer_stats_for_rank(rank)
            total = stats["speed"] + stats["cornering"] + stats["stamina"]
            assert low <= total <= high, (
                f"Rank {rank}: total {total} not in [{low}, {high}]"
            )
            assert 0 <= stats["speed"] <= 31
            assert 0 <= stats["cornering"] <= 31
            assert 0 <= stats["stamina"] <= 31


# ---------------------------------------------------------------------------
# NPC repository tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npc_crud(tmp_path: Path) -> None:
    """Create, read, update, delete NPC."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        npc = await repo.create_npc(
            session,
            guild_id=GUILD_ID,
            name="Lucky Finn",
            personality="Cocky Gambler",
            personality_desc="A brash gambler who bets on his own racers.",
            rank_min="B",
            rank_max="A",
            win_quips='["Called it!"]',
            loss_quips='["The sun was in their eyes!"]',
            emoji="🎰",
            catchphrase="Double or nothing!",
        )
        assert npc.id is not None
        assert npc.name == "Lucky Finn"

        # Read
        fetched = await repo.get_npc(session, npc.id)
        assert fetched is not None
        assert fetched.personality == "Cocky Gambler"

        # Update
        updated = await repo.update_npc(session, npc.id, catchphrase="All in!")
        assert updated.catchphrase == "All in!"

        # Delete
        await repo.delete_npc(session, npc.id)
        assert await repo.get_npc(session, npc.id) is None


@pytest.mark.asyncio
async def test_get_guild_npcs(tmp_path: Path) -> None:
    """Returns only NPCs for the correct guild."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        await repo.create_npc(
            session, guild_id=GUILD_ID, name="NPC1",
            personality="A", personality_desc="A", rank_min="D", rank_max="C",
        )
        await repo.create_npc(
            session, guild_id=999, name="NPC2",
            personality="B", personality_desc="B", rank_min="D", rank_max="C",
        )
        npcs = await repo.get_guild_npcs(session, GUILD_ID)
        assert len(npcs) == 1
        assert npcs[0].name == "NPC1"


@pytest.mark.asyncio
async def test_get_npc_racers(tmp_path: Path) -> None:
    """Returns racers belonging to a specific NPC."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        npc = await repo.create_npc(
            session, guild_id=GUILD_ID, name="Trainer",
            personality="A", personality_desc="A", rank_min="D", rank_max="C",
        )
        # NPC racer
        await repo.create_racer(
            session, name="NPC Horse", owner_id=0, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=5, npc_id=npc.id, rank="D",
        )
        # Regular pool racer (no npc_id)
        await repo.create_racer(
            session, name="Pool Horse", owner_id=0, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=5, rank="D",
        )
        racers = await repo.get_npc_racers(session, npc.id)
        assert len(racers) == 1
        assert racers[0].name == "NPC Horse"


# ---------------------------------------------------------------------------
# NPC reaction tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npc_win_reaction(tmp_path: Path) -> None:
    """NPC quip appears in results when their racer wins."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        npc = await repo.create_npc(
            session, guild_id=GUILD_ID, name="Lucky Finn",
            personality="Gambler", personality_desc="Cocky gambler",
            rank_min="D", rank_max="C",
            win_quips='["Called it!", "Easy money!"]',
            loss_quips='["Bad luck!"]',
            emoji="🎰",
        )
        winner = await repo.create_racer(
            session, name="Double Down", owner_id=0, guild_id=GUILD_ID,
            speed=15, cornering=10, stamina=10, npc_id=npc.id, rank="D",
        )
        loser = await repo.create_racer(
            session, name="Pool Runner", owner_id=0, guild_id=GUILD_ID,
            speed=5, cornering=5, stamina=5, rank="D",
        )

    reactions = await sched._get_npc_reactions([winner.id, loser.id])
    assert len(reactions) == 1
    assert "Lucky Finn" in reactions[0]
    assert any(q in reactions[0] for q in ["Called it!", "Easy money!"])


@pytest.mark.asyncio
async def test_npc_last_place_40pct(tmp_path: Path) -> None:
    """NPC loss quip fires approximately 40% of the time."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        npc = await repo.create_npc(
            session, guild_id=GUILD_ID, name="Marge",
            personality="Hobbyist", personality_desc="Bumbling hobbyist",
            rank_min="D", rank_max="C",
            win_quips='["Wow!"]',
            loss_quips='["Oh dear!"]',
        )
        pool_winner = await repo.create_racer(
            session, name="Winner", owner_id=0, guild_id=GUILD_ID,
            speed=20, cornering=20, stamina=20, rank="D",
        )
        npc_loser = await repo.create_racer(
            session, name="Mr. Wobbles", owner_id=0, guild_id=GUILD_ID,
            speed=5, cornering=5, stamina=5, npc_id=npc.id, rank="D",
        )

    # Run many trials to check ~40% rate
    reaction_count = 0
    trials = 200
    for _ in range(trials):
        reactions = await sched._get_npc_reactions(
            [pool_winner.id, npc_loser.id]
        )
        if reactions:
            reaction_count += 1

    # Should be roughly 40% (allow wide margin for randomness)
    rate = reaction_count / trials
    assert 0.2 < rate < 0.6, f"Loss reaction rate {rate:.2%} not near 40%"


@pytest.mark.asyncio
async def test_npc_retirement_replacement(tmp_path: Path) -> None:
    """NPC racer retirement creates a replacement and posts announcement."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        # Create guild settings with flavor
        await repo.create_guild_settings(
            session, guild_id=GUILD_ID, racer_flavor="magical horses",
        )
        npc = await repo.create_npc(
            session, guild_id=GUILD_ID, name="Baron Ashwick",
            personality="Noble", personality_desc="Dignified noble trainer",
            rank_min="B", rank_max="A",
            emoji="🏰",
        )
        racer = await repo.create_racer(
            session, name="Iron Mandate", owner_id=0, guild_id=GUILD_ID,
            speed=20, cornering=18, stamina=15, npc_id=npc.id, rank="B",
            career_length=30,
        )
        await repo.update_racer(
            session, racer.id, races_completed=30, retired=True,
        )

    channel = bot.guilds[0].system_channel

    # Mock LLM name generation to return a predictable name
    with patch(
        "derby.npc_generation.generate_npc_racer_name",
        return_value="Blacksteel",
    ):
        await sched._handle_npc_retirement(GUILD_ID, racer, channel)

    # Check announcement was posted
    assert len(channel.messages) == 1
    embed = channel.messages[0]
    assert "Baron Ashwick" in embed.description
    assert "Iron Mandate" in embed.description
    assert "Blacksteel" in embed.description

    # Check replacement racer was created
    async with sched.sessionmaker() as session:
        npc_racers = await repo.get_npc_racers(session, npc.id)
        assert len(npc_racers) == 1
        assert npc_racers[0].name == "Blacksteel"
        assert npc_racers[0].npc_id == npc.id
        assert npc_racers[0].rank == "B"


@pytest.mark.asyncio
async def test_ensure_guild_npcs_creates(tmp_path: Path) -> None:
    """Generates NPCs when guild has racer_flavor and no NPCs exist."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        await repo.create_guild_settings(
            session, guild_id=GUILD_ID, racer_flavor="fire drakes",
        )

    mock_npcs = [
        {
            "name": f"NPC {i}",
            "emoji": "🔥",
            "personality": f"Type {i}",
            "personality_desc": f"Description {i}",
            "catchphrase": f"Phrase {i}",
            "rank_min": slot["rank_min"],
            "rank_max": slot["rank_max"],
            "racer1_name": f"Racer {i}A",
            "racer2_name": f"Racer {i}B",
        }
        for i, slot in enumerate(
            [
                {"rank_min": "D", "rank_max": "C"},
                {"rank_min": "C", "rank_max": "B"},
                {"rank_min": "B", "rank_max": "A"},
                {"rank_min": "A", "rank_max": "S"},
                {"rank_min": "S", "rank_max": "S"},
            ]
        )
    ]

    with patch(
        "derby.npc_generation.generate_guild_npcs",
        return_value=mock_npcs,
    ), patch(
        "derby.npc_generation.generate_npc_quips",
        return_value=["quip1", "quip2"],
    ), patch(
        "derby.descriptions.generate_description",
        return_value="A cool racer.",
    ):
        await sched._ensure_guild_npcs(GUILD_ID)

    async with sched.sessionmaker() as session:
        npcs = await repo.get_guild_npcs(session, GUILD_ID)
        assert len(npcs) == 5

        # Each NPC should have 2 racers
        for npc in npcs:
            racers = await repo.get_npc_racers(session, npc.id)
            assert len(racers) == 2


@pytest.mark.asyncio
async def test_ensure_guild_npcs_skips_existing(tmp_path: Path) -> None:
    """No-op when NPCs already exist for the guild."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        await repo.create_guild_settings(
            session, guild_id=GUILD_ID, racer_flavor="horses",
        )
        # Pre-create an NPC
        await repo.create_npc(
            session, guild_id=GUILD_ID, name="Existing NPC",
            personality="A", personality_desc="A",
            rank_min="D", rank_max="C",
        )

    with patch(
        "derby.npc_generation.generate_guild_npcs"
    ) as mock_gen:
        await sched._ensure_guild_npcs(GUILD_ID)
        mock_gen.assert_not_called()
