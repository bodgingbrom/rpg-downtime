"""Tests for the daily digest channel message."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from config import Settings
from derby import logic, repositories as repo
from derby.models import (
    GuildSettings,
    Race,
    RaceEntry,
    Racer,
    Tournament,
    TournamentEntry,
)
from derby.scheduler import DerbyScheduler
from economy import repositories as wallet_repo
from economy.models import Wallet


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
# Digest embed tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_daily_section(tmp_path: Path) -> None:
    """Digest always includes the daily reward reminder."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    embed = await sched._build_digest_embed(GUILD_ID)
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert "\U0001f381 Daily Reward" in field_names
    daily_field = next(f for f in embed.fields if "Daily Reward" in f.name)
    assert "/daily" in daily_field.value


@pytest.mark.asyncio
async def test_digest_no_races_yesterday(tmp_path: Path) -> None:
    """When no races ran yesterday, payout and longshot sections are absent."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    embed = await sched._build_digest_embed(GUILD_ID)
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert not any("Payout" in n for n in field_names)
    assert not any("Longshot" in n for n in field_names)


@pytest.mark.asyncio
async def test_digest_biggest_payout(tmp_path: Path) -> None:
    """Digest shows the biggest payout from yesterday's races."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)

    async with sched.sessionmaker() as session:
        racer = Racer(
            guild_id=GUILD_ID, name="Thunderhooves", owner_id=USER_ID,
            speed=10, cornering=8, stamina=5, rank="D",
        )
        session.add(racer)
        await session.commit()
        await session.refresh(racer)

        race = Race(
            guild_id=GUILD_ID, finished=True, winner_id=racer.id,
            started_at=yesterday,
            placements=json.dumps([racer.id]),
            biggest_payout=500,
            biggest_payout_user_id=USER_ID,
            biggest_payout_racer_id=racer.id,
        )
        session.add(race)
        await session.commit()

    embed = await sched._build_digest_embed(GUILD_ID)
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert any("Payout" in n for n in field_names)
    payout_field = next(f for f in embed.fields if "Payout" in f.name)
    assert "500 coins" in payout_field.value
    assert "Thunderhooves" in payout_field.value


@pytest.mark.asyncio
async def test_digest_longshot_winner(tmp_path: Path) -> None:
    """Digest identifies the highest-odds winner as the longshot."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)

    async with sched.sessionmaker() as session:
        # Create two racers: one strong, one weak (longshot)
        strong = Racer(
            guild_id=GUILD_ID, name="Favorite", owner_id=0,
            speed=25, cornering=25, stamina=25, rank="A",
        )
        weak = Racer(
            guild_id=GUILD_ID, name="Underdog", owner_id=0,
            speed=5, cornering=5, stamina=5, rank="D",
        )
        session.add_all([strong, weak])
        await session.commit()
        await session.refresh(strong)
        await session.refresh(weak)

        # Weak racer wins — they're the longshot
        race = Race(
            guild_id=GUILD_ID, finished=True, winner_id=weak.id,
            started_at=yesterday,
            placements=json.dumps([weak.id, strong.id]),
        )
        session.add(race)
        await session.commit()
        await session.refresh(race)

        # Add race entries so get_race_participants works
        session.add(RaceEntry(race_id=race.id, racer_id=strong.id))
        session.add(RaceEntry(race_id=race.id, racer_id=weak.id))
        await session.commit()

    embed = await sched._build_digest_embed(GUILD_ID)
    assert embed is not None
    longshot_field = next(
        (f for f in embed.fields if "Longshot" in f.name), None
    )
    assert longshot_field is not None
    assert "Underdog" in longshot_field.value
    assert "defied the odds" in longshot_field.value


@pytest.mark.asyncio
async def test_digest_friday_tournaments(tmp_path: Path) -> None:
    """Friday digest includes tournament registration reminder."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    # Build the tournament section for Friday (weekday=4)
    text = await sched._build_tournament_digest(GUILD_ID, 4)
    assert text is not None
    assert "tomorrow" in text.lower()
    assert "/tournament register" in text
    assert "D & C" in text
    assert "B & A" in text
    assert "S rank" in text


@pytest.mark.asyncio
async def test_digest_saturday_tournaments(tmp_path: Path) -> None:
    """Saturday digest shows D/C registration counts and B/A reminder."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    # Create a pending D tournament with 2 real entries
    async with sched.sessionmaker() as session:
        r1 = Racer(
            guild_id=GUILD_ID, name="R1", owner_id=USER_ID,
            speed=5, cornering=5, stamina=5, rank="D",
        )
        r2 = Racer(
            guild_id=GUILD_ID, name="R2", owner_id=USER_ID + 1,
            speed=6, cornering=6, stamina=6, rank="D",
        )
        session.add_all([r1, r2])
        await session.commit()
        await session.refresh(r1)
        await session.refresh(r2)

        tournament = Tournament(guild_id=GUILD_ID, rank="D", status="pending")
        session.add(tournament)
        await session.commit()
        await session.refresh(tournament)

        e1 = TournamentEntry(
            tournament_id=tournament.id, racer_id=r1.id,
            owner_id=USER_ID, is_pool_filler=False,
        )
        e2 = TournamentEntry(
            tournament_id=tournament.id, racer_id=r2.id,
            owner_id=USER_ID + 1, is_pool_filler=False,
        )
        session.add_all([e1, e2])
        await session.commit()

    text = await sched._build_tournament_digest(GUILD_ID, 5)
    assert text is not None
    assert "D & C" in text
    assert "2" in text  # 2 registered in D
    assert "B & A" in text


@pytest.mark.asyncio
async def test_digest_midweek_no_tournaments(tmp_path: Path) -> None:
    """Tuesday digest has no tournament section."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    # Tuesday = weekday 1
    text = await sched._build_tournament_digest(GUILD_ID, 1)
    assert text is None


@pytest.mark.asyncio
async def test_resolve_payouts_stores_biggest(tmp_path: Path) -> None:
    """resolve_payouts populates biggest_payout fields on Race."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        racer = Racer(
            guild_id=GUILD_ID, name="Winner", owner_id=0,
            speed=10, cornering=10, stamina=10, rank="C",
        )
        session.add(racer)
        await session.commit()
        await session.refresh(racer)

        race = Race(guild_id=GUILD_ID, finished=False, winner_id=None)
        session.add(race)
        await session.commit()
        await session.refresh(race)

        # Create wallet and bet
        await wallet_repo.create_wallet(
            session, user_id=USER_ID, guild_id=GUILD_ID, balance=1000,
        )
        from derby.models import Bet
        bet = Bet(
            race_id=race.id, user_id=USER_ID, racer_id=racer.id,
            amount=100, payout_multiplier=3.0, bet_type="win",
        )
        session.add(bet)
        await session.commit()

        # Resolve payouts — racer wins (1st in placements)
        results = await logic.resolve_payouts(
            session, race.id, [racer.id], guild_id=GUILD_ID,
        )

        # Check results
        assert len(results) == 1
        assert results[0]["won"] is True
        assert results[0]["payout"] == 300

        # Check that Race record got updated
        await session.refresh(race)
        assert race.biggest_payout == 300
        assert race.biggest_payout_user_id == USER_ID
        assert race.biggest_payout_racer_id == racer.id
