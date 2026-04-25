import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core import repositories as core_repo
from db_base import Base
from derby import repositories as repo
import economy.models  # noqa: F401 — register Wallet table


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        yield sess
    await engine.dispose()


@pytest.mark.asyncio
async def test_racer_crud(session: AsyncSession):
    racer = await repo.create_racer(
        session,
        name="Speedy",
        owner_id=1,
        speed=5,
        cornering=6,
        stamina=7,
        temperament="Agile",
        mood=2,
        injuries="sprained ankle",
    )
    assert racer.id is not None

    fetched = await repo.get_racer(session, racer.id)
    assert fetched.name == "Speedy"
    assert fetched.speed == 5
    assert fetched.cornering == 6
    assert fetched.stamina == 7
    assert fetched.temperament == "Agile"
    assert fetched.mood == 2
    assert fetched.injuries == "sprained ankle"

    updated = await repo.update_racer(session, racer.id, name="Zoom")
    assert updated.name == "Zoom"

    await repo.delete_racer(session, racer.id)
    assert await repo.get_racer(session, racer.id) is None


@pytest.mark.asyncio
async def test_race_crud(session: AsyncSession):
    race = await repo.create_race(session, guild_id=123)
    assert race.id is not None

    fetched = await repo.get_race(session, race.id)
    assert fetched.guild_id == 123

    updated = await repo.update_race(session, race.id, finished=True)
    assert updated.finished is True

    await repo.delete_race(session, race.id)
    assert await repo.get_race(session, race.id) is None


@pytest.mark.asyncio
async def test_bet_crud(session: AsyncSession):
    race = await repo.create_race(session, guild_id=1)
    racer = await repo.create_racer(session, name="A", owner_id=1)

    bet = await repo.create_bet(
        session, race_id=race.id, user_id=2, racer_id=racer.id, amount=50
    )
    assert bet.id is not None

    fetched = await repo.get_bet(session, bet.id)
    assert fetched.amount == 50

    updated = await repo.update_bet(session, bet.id, amount=75)
    assert updated.amount == 75

    await repo.delete_bet(session, bet.id)
    assert await repo.get_bet(session, bet.id) is None


@pytest.mark.asyncio
async def test_course_segment_crud(session: AsyncSession):
    race = await repo.create_race(session, guild_id=1)
    seg = await repo.create_course_segment(
        session, race_id=race.id, position=1, description="Start"
    )
    assert seg.id is not None

    fetched = await repo.get_course_segment(session, seg.id)
    assert fetched.position == 1

    updated = await repo.update_course_segment(session, seg.id, description="Mid")
    assert updated.description == "Mid"

    await repo.delete_course_segment(session, seg.id)
    assert await repo.get_course_segment(session, seg.id) is None


@pytest.mark.asyncio
async def test_guild_settings_crud(session: AsyncSession):
    settings = await core_repo.create_guild_settings(session, guild_id=1)
    assert settings.guild_id == 1

    fetched = await core_repo.get_guild_settings(session, 1)
    assert fetched.guild_id == 1

    updated = await core_repo.update_guild_settings(session, 1, bet_window=60)
    assert updated.bet_window == 60

    await core_repo.delete_guild_settings(session, 1)
    assert await core_repo.get_guild_settings(session, 1) is None


@pytest.mark.asyncio
async def test_get_race_history(session: AsyncSession):
    racer1 = await repo.create_racer(session, name="A", owner_id=1)
    racer2 = await repo.create_racer(session, name="B", owner_id=2)

    r1 = await repo.create_race(
        session, guild_id=1, finished=True, winner_id=racer2.id
    )
    r2 = await repo.create_race(
        session, guild_id=1, finished=True, winner_id=racer1.id
    )
    await repo.create_race(session, guild_id=1, finished=False)

    await repo.create_bet(
        session, race_id=r2.id, user_id=1, racer_id=racer1.id, amount=10
    )
    await repo.create_bet(
        session, race_id=r2.id, user_id=2, racer_id=racer1.id, amount=5
    )
    await repo.create_bet(
        session, race_id=r1.id, user_id=3, racer_id=racer2.id, amount=20
    )

    history = await repo.get_race_history(session, guild_id=1, limit=2)

    assert [h[0].id for h in history] == [r2.id, r1.id]
    assert history[0][1] == racer1.id and history[0][2] == 30
    assert history[1][1] == racer2.id and history[1][2] == 40


@pytest.mark.asyncio
async def test_get_unowned_guild_racers(session: AsyncSession):
    """Unowned racers (owner_id=0) are returned; owned ones are not."""
    await repo.create_racer(
        session, name="Pool1", owner_id=0, guild_id=1, speed=10
    )
    await repo.create_racer(
        session, name="Pool2", owner_id=0, guild_id=1, speed=5
    )
    await repo.create_racer(
        session, name="Owned", owner_id=42, guild_id=1, speed=15
    )
    # Retired unowned racer — should be excluded when eligible_only=True
    await repo.create_racer(
        session, name="Retired", owner_id=0, guild_id=1, retired=True
    )
    # Injured unowned racer — should be excluded when eligible_only=True
    r = await repo.create_racer(
        session, name="Injured", owner_id=0, guild_id=1
    )
    await repo.update_racer(session, r.id, injury_races_remaining=2)

    eligible = await repo.get_unowned_guild_racers(session, guild_id=1)
    assert len(eligible) == 2
    assert {r.name for r in eligible} == {"Pool1", "Pool2"}

    all_unowned = await repo.get_unowned_guild_racers(
        session, guild_id=1, eligible_only=False
    )
    assert len(all_unowned) == 4  # Pool1, Pool2, Retired, Injured


@pytest.mark.asyncio
async def test_get_owned_racers(session: AsyncSession):
    """Only non-retired racers owned by the user in the guild are returned."""
    await repo.create_racer(
        session, name="Mine", owner_id=5, guild_id=1
    )
    await repo.create_racer(
        session, name="AlsoMine", owner_id=5, guild_id=1
    )
    await repo.create_racer(
        session, name="NotMine", owner_id=99, guild_id=1
    )
    await repo.create_racer(
        session, name="OtherGuild", owner_id=5, guild_id=2
    )
    await repo.create_racer(
        session, name="RetiredMine", owner_id=5, guild_id=1, retired=True
    )

    owned = await repo.get_owned_racers(session, owner_id=5, guild_id=1)
    assert len(owned) == 2
    assert {r.name for r in owned} == {"Mine", "AlsoMine"}


@pytest.mark.asyncio
async def test_count_unowned_eligible_racers(session: AsyncSession):
    await repo.create_racer(session, name="A", owner_id=0, guild_id=1)
    await repo.create_racer(session, name="B", owner_id=0, guild_id=1)
    await repo.create_racer(session, name="C", owner_id=42, guild_id=1)
    await repo.create_racer(session, name="D", owner_id=0, guild_id=1, retired=True)

    count = await repo.count_unowned_eligible_racers(session, guild_id=1)
    assert count == 2


# ---------------------------------------------------------------------------
# Lineage + stable slot counting tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_racer_with_lineage(session: AsyncSession):
    """Sire/dam IDs and breeding fields are stored correctly."""
    sire = await repo.create_racer(
        session, name="Dad", owner_id=1, guild_id=1, gender="M",
    )
    dam = await repo.create_racer(
        session, name="Mom", owner_id=1, guild_id=1, gender="F",
    )
    foal = await repo.create_racer(
        session, name="Baby", owner_id=1, guild_id=1, gender="F",
        sire_id=sire.id, dam_id=dam.id,
    )

    fetched = await repo.get_racer(session, foal.id)
    assert fetched.sire_id == sire.id
    assert fetched.dam_id == dam.id
    assert fetched.gender == "F"
    assert fetched.foal_count == 0
    assert fetched.breed_cooldown == 0
    assert fetched.training_count == 0


@pytest.mark.asyncio
async def test_get_stable_racers_includes_retired(session: AsyncSession):
    """get_stable_racers returns ALL owned racers including retired."""
    await repo.create_racer(
        session, name="Active", owner_id=5, guild_id=1,
    )
    await repo.create_racer(
        session, name="Retired", owner_id=5, guild_id=1, retired=True,
    )
    await repo.create_racer(
        session, name="OtherOwner", owner_id=99, guild_id=1,
    )

    stable = await repo.get_stable_racers(session, owner_id=5, guild_id=1)
    assert len(stable) == 2
    assert {r.name for r in stable} == {"Active", "Retired"}

    # Compare with get_owned_racers which excludes retired
    active_only = await repo.get_owned_racers(session, owner_id=5, guild_id=1)
    assert len(active_only) == 1
    assert active_only[0].name == "Active"


# ---------------------------------------------------------------------------
# PlayerData tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_player_data_crud(session: AsyncSession):
    """Create and retrieve PlayerData for stable slot upgrades."""
    pd = await repo.create_player_data(
        session, user_id=42, guild_id=1, extra_slots=1
    )
    assert pd.user_id == 42
    assert pd.guild_id == 1
    assert pd.extra_slots == 1

    fetched = await repo.get_player_data(session, user_id=42, guild_id=1)
    assert fetched is not None
    assert fetched.extra_slots == 1

    # Different guild returns None
    assert await repo.get_player_data(session, user_id=42, guild_id=2) is None


# ---------------------------------------------------------------------------
# Training gate tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_foal_excluded_from_race_pool(session: AsyncSession):
    """A bred racer with training_count < min_training is excluded."""
    # Pool racer (no sire) — always eligible regardless of training_count
    await repo.create_racer(
        session, name="Pool", owner_id=0, guild_id=1, training_count=0,
    )
    # Bred foal — untrained, should be excluded
    await repo.create_racer(
        session, name="Foal", owner_id=0, guild_id=1,
        sire_id=99, dam_id=98, training_count=2,
    )
    # Bred foal — fully trained, should be included
    await repo.create_racer(
        session, name="TrainedFoal", owner_id=0, guild_id=1,
        sire_id=99, dam_id=98, training_count=5,
    )

    # Without training gate — all 3 returned
    all_racers = await repo.get_guild_racers(session, guild_id=1)
    assert len(all_racers) == 3

    # With training gate of 5
    gated = await repo.get_guild_racers(session, guild_id=1, min_training=5)
    assert len(gated) == 2
    assert {r.name for r in gated} == {"Pool", "TrainedFoal"}


@pytest.mark.asyncio
async def test_pool_racer_races_regardless_of_training(session: AsyncSession):
    """Pool-generated racers (no sire_id) race even with training_count=0."""
    await repo.create_racer(
        session, name="PoolRacer", owner_id=0, guild_id=1, training_count=0,
    )

    racers = await repo.get_guild_racers(session, guild_id=1, min_training=5)
    assert len(racers) == 1
    assert racers[0].name == "PoolRacer"


@pytest.mark.asyncio
async def test_create_racer_with_rank(session: AsyncSession):
    """Racer created with rank should store it."""
    racer = await repo.create_racer(
        session, name="Ranked", owner_id=0, guild_id=1,
        speed=20, cornering=20, stamina=20, rank="A",
    )
    assert racer.rank == "A"

    fetched = await repo.get_racer(session, racer.id)
    assert fetched.rank == "A"


@pytest.mark.asyncio
async def test_get_racers_by_rank(session: AsyncSession):
    """Should return only racers with the specified rank."""
    await repo.create_racer(
        session, name="D1", owner_id=0, guild_id=1, speed=5, cornering=5, stamina=5, rank="D",
    )
    await repo.create_racer(
        session, name="C1", owner_id=0, guild_id=1, speed=10, cornering=10, stamina=10, rank="C",
    )
    await repo.create_racer(
        session, name="C2", owner_id=1, guild_id=1, speed=12, cornering=12, stamina=12, rank="C",
    )
    await repo.create_racer(
        session, name="CRetired", owner_id=0, guild_id=1, speed=10, cornering=10, stamina=10,
        rank="C", retired=True,
    )

    d_racers = await repo.get_racers_by_rank(session, guild_id=1, rank="D")
    assert len(d_racers) == 1
    assert d_racers[0].name == "D1"

    c_racers = await repo.get_racers_by_rank(session, guild_id=1, rank="C")
    assert len(c_racers) == 2  # excludes retired

    c_unowned = await repo.get_racers_by_rank(session, guild_id=1, rank="C", unowned_only=True)
    assert len(c_unowned) == 1
    assert c_unowned[0].name == "C1"


# ---------------------------------------------------------------------------
# Ability analytics
# ---------------------------------------------------------------------------


async def _make_racer(session, name, guild_id, sig, quirk):
    """Helper: create a racer and directly assign ability fields the
    repo's create_racer doesn't expose in its signature."""
    r = await repo.create_racer(
        session, name=name, owner_id=0, guild_id=guild_id,
        speed=10, cornering=10, stamina=10, temperament="Bold",
    )
    await repo.update_racer(
        session, r.id, signature_ability=sig, quirk_ability=quirk,
    )
    return r


@pytest.mark.asyncio
async def test_get_ability_stats_basic(session: AsyncSession):
    """Basic smoke test: racers with abilities, a race, some procs land in stats."""
    from derby.models import AbilityProcLog, Race, RaceEntry

    # Two racers, one ability each
    r1 = await _make_racer(session, "A", 1, "closing_surge", "rival_hunter")
    r2 = await _make_racer(session, "B", 1, "front_runner", "slow_starter")

    # One finished race with both racers entered
    race = await repo.create_race(session, guild_id=1, finished=True)
    await repo.create_race_entries(session, race.id, [r1.id, r2.id])

    # A procs closing_surge twice (segments 0 and 2), r1 wins (finish=1)
    session.add(AbilityProcLog(
        race_id=race.id, racer_id=r1.id, guild_id=1,
        ability_key="closing_surge", segment_index=0, finish_position=1,
    ))
    session.add(AbilityProcLog(
        race_id=race.id, racer_id=r1.id, guild_id=1,
        ability_key="closing_surge", segment_index=2, finish_position=1,
    ))
    # B procs front_runner once, B finishes 2nd
    session.add(AbilityProcLog(
        race_id=race.id, racer_id=r2.id, guild_id=1,
        ability_key="front_runner", segment_index=1, finish_position=2,
    ))
    await session.commit()

    stats, races_analyzed = await repo.get_ability_stats(session, guild_id=1)

    assert races_analyzed == 1
    # closing_surge: 2 procs, both in same race, win (r1 finished 1st)
    cs = stats["closing_surge"]
    assert cs["procs"] == 2
    assert cs["races_procced"] == 1
    assert cs["races_entered"] == 1
    assert cs["wins"] == 2  # per-proc win count
    assert cs["top3"] == 2
    assert cs["avg_finish"] == 1.0

    # front_runner: 1 proc, r2 finished 2nd (top3 yes, win no)
    fr = stats["front_runner"]
    assert fr["procs"] == 1
    assert fr["wins"] == 0
    assert fr["top3"] == 1
    assert fr["avg_finish"] == 2.0

    # rival_hunter was entered (on r1) but never procced
    rh = stats["rival_hunter"]
    assert rh["procs"] == 0
    assert rh["races_entered"] == 1
    assert rh["avg_finish"] is None


@pytest.mark.asyncio
async def test_get_ability_stats_excludes_test_races(session: AsyncSession):
    """include_test=False filters out is_test=True races and their procs."""
    from derby.models import AbilityProcLog, Race

    r1 = await _make_racer(session, "A", 1, "closing_surge", None)

    # One real race, one test race
    real_race = await repo.create_race(session, guild_id=1, finished=True, is_test=False)
    test_race = await repo.create_race(session, guild_id=1, finished=True, is_test=True)
    await repo.create_race_entries(session, real_race.id, [r1.id])
    await repo.create_race_entries(session, test_race.id, [r1.id])

    session.add(AbilityProcLog(
        race_id=real_race.id, racer_id=r1.id, guild_id=1,
        ability_key="closing_surge", segment_index=0, finish_position=1,
    ))
    session.add(AbilityProcLog(
        race_id=test_race.id, racer_id=r1.id, guild_id=1,
        ability_key="closing_surge", segment_index=0, finish_position=1,
    ))
    await session.commit()

    # Default include_test=True: both races counted
    stats_all, races_all = await repo.get_ability_stats(session, guild_id=1)
    assert races_all == 2
    assert stats_all["closing_surge"]["procs"] == 2

    # include_test=False: only real race
    stats_real, races_real = await repo.get_ability_stats(
        session, guild_id=1, include_test=False,
    )
    assert races_real == 1
    assert stats_real["closing_surge"]["procs"] == 1


@pytest.mark.asyncio
async def test_get_ability_stats_last_n_races_limit(session: AsyncSession):
    """last_n_races restricts analysis to the N most recent finished races."""
    from derby.models import AbilityProcLog, Race

    r1 = await _make_racer(session, "A", 1, "closing_surge", None)

    # Create 5 finished races, each with a closing_surge proc
    race_ids = []
    for _ in range(5):
        race = await repo.create_race(session, guild_id=1, finished=True)
        await repo.create_race_entries(session, race.id, [r1.id])
        race_ids.append(race.id)
        session.add(AbilityProcLog(
            race_id=race.id, racer_id=r1.id, guild_id=1,
            ability_key="closing_surge", segment_index=0, finish_position=1,
        ))
    await session.commit()

    # last_n_races=3 → only 3 most recent counted
    stats, races_analyzed = await repo.get_ability_stats(
        session, guild_id=1, last_n_races=3,
    )
    assert races_analyzed == 3
    assert stats["closing_surge"]["procs"] == 3


@pytest.mark.asyncio
async def test_get_ability_stats_races_entered_when_racer_deleted(session: AsyncSession):
    """Procs can remain in the log after the racer is deleted. races_entered
    should still reflect those races so Proc% renders correctly (not —)."""
    from derby.models import AbilityProcLog

    r1 = await _make_racer(session, "Gone", 1, "closing_surge", None)
    race = await repo.create_race(session, guild_id=1, finished=True)
    await repo.create_race_entries(session, race.id, [r1.id])
    session.add(AbilityProcLog(
        race_id=race.id, racer_id=r1.id, guild_id=1,
        ability_key="closing_surge", segment_index=0, finish_position=1,
    ))
    await session.commit()

    # Now delete the racer — simulates the admin running /derby racer delete
    await repo.delete_racer(session, r1.id)

    stats, _ = await repo.get_ability_stats(session, guild_id=1)
    cs = stats["closing_surge"]
    assert cs["procs"] == 1  # proc still there
    # races_entered must be ≥ races_procced (the proc proves the race had
    # the ability in play, even if the current racer state can't tell us)
    assert cs["races_entered"] >= cs["races_procced"]
    assert cs["races_entered"] == 1


@pytest.mark.asyncio
async def test_get_ability_stats_empty_guild(session: AsyncSession):
    stats, races_analyzed = await repo.get_ability_stats(session, guild_id=999)
    assert stats == {}
    assert races_analyzed == 0
