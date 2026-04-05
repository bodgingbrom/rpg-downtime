"""Tests for the breeding logic functions."""

import random

import pytest

from derby.logic import breed_racer, check_lineage, validate_breeding
from derby.models import Racer


def _racer(**kwargs) -> Racer:
    """Helper to build a Racer with sane defaults."""
    defaults = dict(
        id=1, name="Test", owner_id=42, guild_id=1,
        speed=15, cornering=15, stamina=15,
        gender="M", temperament="Quirky", mood=3,
        races_completed=10, career_length=30, peak_end=18,
        retired=False, sire_id=None, dam_id=None,
        foal_count=0, breed_cooldown=0, training_count=5,
        injuries="", injury_races_remaining=0,
    )
    defaults.update(kwargs)
    return Racer(**defaults)


# ---------------------------------------------------------------------------
# check_lineage
# ---------------------------------------------------------------------------


def test_lineage_unrelated_ok():
    a = _racer(id=1, sire_id=None, dam_id=None)
    b = _racer(id=2, gender="F", sire_id=None, dam_id=None)
    assert check_lineage(a, b) is None


def test_lineage_parent_child_rejected():
    parent = _racer(id=1)
    child = _racer(id=2, gender="F", sire_id=1)
    assert check_lineage(parent, child) is not None
    assert check_lineage(child, parent) is not None

    # dam_id link
    child2 = _racer(id=3, gender="F", dam_id=1)
    assert check_lineage(parent, child2) is not None


def test_lineage_half_siblings_shared_sire():
    a = _racer(id=1, sire_id=10, dam_id=20)
    b = _racer(id=2, gender="F", sire_id=10, dam_id=30)
    err = check_lineage(a, b)
    assert err is not None
    assert "sire" in err.lower()


def test_lineage_half_siblings_shared_dam():
    a = _racer(id=1, sire_id=10, dam_id=20)
    b = _racer(id=2, gender="F", sire_id=30, dam_id=20)
    err = check_lineage(a, b)
    assert err is not None
    assert "dam" in err.lower()


def test_lineage_different_parents_ok():
    a = _racer(id=1, sire_id=10, dam_id=20)
    b = _racer(id=2, gender="F", sire_id=30, dam_id=40)
    assert check_lineage(a, b) is None


# ---------------------------------------------------------------------------
# validate_breeding
# ---------------------------------------------------------------------------


def test_validate_same_gender_rejected():
    sire = _racer(id=1, gender="M")
    dam = _racer(id=2, gender="M")
    err = validate_breeding(sire, dam, 42, 2, 6)
    assert err is not None
    assert "not female" in err.lower()


def test_validate_reversed_gender_rejected():
    sire = _racer(id=1, gender="F")
    dam = _racer(id=2, gender="M")
    err = validate_breeding(sire, dam, 42, 2, 6)
    assert err is not None
    assert "not male" in err.lower()


def test_validate_insufficient_races():
    sire = _racer(id=1, gender="M", races_completed=3)
    dam = _racer(id=2, gender="F", races_completed=10)
    err = validate_breeding(sire, dam, 42, 2, 6, min_races=5)
    assert err is not None
    assert "races" in err.lower()


def test_validate_on_cooldown():
    sire = _racer(id=1, gender="M", breed_cooldown=3)
    dam = _racer(id=2, gender="F")
    err = validate_breeding(sire, dam, 42, 2, 6)
    assert err is not None
    assert "cooldown" in err.lower()


def test_validate_max_foals():
    sire = _racer(id=1, gender="M")
    dam = _racer(id=2, gender="F", foal_count=3)
    err = validate_breeding(sire, dam, 42, 2, 6, max_foals=3)
    assert err is not None
    assert "maximum" in err.lower()


def test_validate_no_slot():
    sire = _racer(id=1, gender="M")
    dam = _racer(id=2, gender="F")
    err = validate_breeding(sire, dam, 42, 6, 6)  # stable_count == max_slots
    assert err is not None
    assert "full" in err.lower()


def test_validate_not_owner():
    sire = _racer(id=1, gender="M", owner_id=42)
    dam = _racer(id=2, gender="F", owner_id=99)
    err = validate_breeding(sire, dam, 42, 2, 6)
    assert err is not None
    assert "own" in err.lower()


def test_validate_retired_can_breed():
    """Retired racers with enough races can still breed."""
    sire = _racer(id=1, gender="M", retired=True, races_completed=10)
    dam = _racer(id=2, gender="F", retired=True, races_completed=10)
    err = validate_breeding(sire, dam, 42, 2, 6)
    assert err is None


def test_validate_success():
    sire = _racer(id=1, gender="M", races_completed=10)
    dam = _racer(id=2, gender="F", races_completed=10)
    err = validate_breeding(sire, dam, 42, 2, 6)
    assert err is None


# ---------------------------------------------------------------------------
# breed_racer
# ---------------------------------------------------------------------------


def test_breed_one_stat_inherits():
    """Exactly 1 stat should be in parent range; other 2 are 0-31."""
    sire = _racer(id=1, gender="M", speed=20, cornering=20, stamina=20)
    dam = _racer(id=2, gender="F", speed=25, cornering=25, stamina=25)

    rng = random.Random(42)
    kwargs = breed_racer(sire, dam, guild_id=1, rng=rng)

    stats = {"speed": kwargs["speed"], "cornering": kwargs["cornering"], "stamina": kwargs["stamina"]}
    # One stat should be between 20 and 25 (the parent range)
    in_range = [s for s, v in stats.items() if 20 <= v <= 25]
    assert len(in_range) >= 1  # at least the inherited stat


def test_breed_identical_stats():
    """Two parents with identical stats → inherited stat gets that value."""
    sire = _racer(id=1, gender="M", speed=31, cornering=31, stamina=31)
    dam = _racer(id=2, gender="F", speed=31, cornering=31, stamina=31)

    rng = random.Random(123)
    kwargs = breed_racer(sire, dam, guild_id=1, rng=rng)

    # Whichever stat was inherited, it must be 31 (randint(31, 31) = 31)
    stats = [kwargs["speed"], kwargs["cornering"], kwargs["stamina"]]
    assert 31 in stats


def test_breed_temperament_sire_dominant():
    """Over many trials, sire temperament should appear ~75% of non-mutation cases."""
    sire = _racer(id=1, gender="M", temperament="Agile")
    dam = _racer(id=2, gender="F", temperament="Burly")

    sire_count = 0
    trials = 500
    for i in range(trials):
        rng = random.Random(i)
        kwargs = breed_racer(sire, dam, guild_id=1, rng=rng)
        if kwargs["temperament"] == "Agile":
            sire_count += 1

    # Expect ~67.5% sire (75% of 90% non-mutation) but allow wide tolerance
    assert sire_count > 200, f"Sire temperament only appeared {sire_count}/{trials} times"


def test_breed_temperament_mutation():
    """Over many trials, mutation (random temperament) should occur ~10%."""
    sire = _racer(id=1, gender="M", temperament="Agile")
    dam = _racer(id=2, gender="F", temperament="Burly")

    neither_count = 0
    trials = 500
    for i in range(trials):
        rng = random.Random(i)
        kwargs = breed_racer(sire, dam, guild_id=1, rng=rng)
        if kwargs["temperament"] not in ("Agile", "Burly"):
            neither_count += 1

    # Mutation rate ~10%, but mutated value could still be Agile/Burly
    # Just check that some mutations occurred
    assert neither_count > 10, f"Only {neither_count} mutations in {trials} trials"


def test_breed_career_averaged():
    """Career length should be close to parent average ±5, clamped 25-40."""
    sire = _racer(id=1, gender="M", career_length=30)
    dam = _racer(id=2, gender="F", career_length=36)

    careers = []
    for i in range(200):
        rng = random.Random(i)
        kwargs = breed_racer(sire, dam, guild_id=1, rng=rng)
        careers.append(kwargs["career_length"])
        assert 25 <= kwargs["career_length"] <= 40

    avg = sum(careers) / len(careers)
    # Parent average is 33, offspring should be within 33±5 on average
    assert 28 <= avg <= 38


def test_breed_naming():
    sire = _racer(id=1, gender="M", name="Thunder")
    dam = _racer(id=2, gender="F", name="Lightning")
    kwargs = breed_racer(sire, dam, guild_id=1)
    assert kwargs["name"] == "Lightning's Foal"


def test_breed_gender_random():
    """Over many trials, gender should be roughly 50/50."""
    sire = _racer(id=1, gender="M")
    dam = _racer(id=2, gender="F")
    genders = []
    for i in range(200):
        rng = random.Random(i)
        kwargs = breed_racer(sire, dam, guild_id=1, rng=rng)
        genders.append(kwargs["gender"])
    m = genders.count("M")
    f = genders.count("F")
    assert m > 50
    assert f > 50


def test_breed_lineage_set():
    """Offspring should have sire_id and dam_id set."""
    sire = _racer(id=10, gender="M")
    dam = _racer(id=20, gender="F")
    kwargs = breed_racer(sire, dam, guild_id=1)
    assert kwargs["sire_id"] == 10
    assert kwargs["dam_id"] == 20
    assert kwargs["training_count"] == 0
    assert kwargs["mood"] == 3
