import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from brewing.models import Ingredient
from brewing.potions import (
    POTION_MIN_POTENCY,
    REVELATION_POTENCY,
    calculate_dominant_tag,
    determine_potion,
)
from brewing import repositories as brew_repo
from brewing.seed_data import seed_if_empty
from db_base import Base
import brewing.models  # noqa: F401
import economy.models  # noqa: F401


def _ing(name: str, tag_1: str, tag_2: str) -> Ingredient:
    return Ingredient(
        name=name, rarity="free", base_cost=0,
        tag_1=tag_1, tag_2=tag_2, flavor_text="",
    )


# ---------------------------------------------------------------------------
# calculate_dominant_tag
# ---------------------------------------------------------------------------


class TestCalculateDominantTag:
    def test_empty(self):
        assert calculate_dominant_tag([]) is None

    def test_single_ingredient(self):
        ings = [_ing("A", "Thermal", "Volatile")]
        # Both tags get 3x: Thermal=3, Volatile=3. Alphabetical: Thermal wins
        assert calculate_dominant_tag(ings) == "Thermal"

    def test_single_ingredient_alpha_tiebreak(self):
        ings = [_ing("A", "Volatile", "Abyssal")]
        # Abyssal=3, Volatile=3. Alphabetical: Abyssal wins
        assert calculate_dominant_tag(ings) == "Abyssal"

    def test_two_ingredients_shared_tag(self):
        ings = [
            _ing("A", "Thermal", "Volatile"),   # Thermal=3, Volatile=3
            _ing("B", "Thermal", "Corrosive"),   # Thermal+=2, Corrosive=2
        ]
        # Thermal=5, Volatile=3, Corrosive=2
        assert calculate_dominant_tag(ings) == "Thermal"

    def test_two_ingredients_no_shared_tag(self):
        ings = [
            _ing("A", "Thermal", "Volatile"),    # Thermal=3, Volatile=3
            _ing("B", "Luminous", "Celestial"),   # Luminous=2, Celestial=2
        ]
        # Thermal=3, Volatile=3 → Thermal wins (alphabetical)
        assert calculate_dominant_tag(ings) == "Thermal"

    def test_three_ingredients(self):
        ings = [
            _ing("A", "Corrosive", "Thermal"),    # Corrosive=3, Thermal=3
            _ing("B", "Corrosive", "Verdant"),     # Corrosive+=2, Verdant=2
            _ing("C", "Corrosive", "Abyssal"),     # Corrosive+=1, Abyssal=1
        ]
        # Corrosive=6, Thermal=3, Verdant=2, Abyssal=1
        assert calculate_dominant_tag(ings) == "Corrosive"

    def test_filler_doesnt_overtake(self):
        ings = [
            _ing("A", "Corrosive", "Thermal"),    # Corrosive=3, Thermal=3
            _ing("B", "Corrosive", "Verdant"),     # Corrosive+=2, Verdant=2
            _ing("C", "Thermal", "Verdant"),       # Thermal+=1=4, Verdant+=1=3
        ]
        # Corrosive=5, Thermal=4, Verdant=3
        assert calculate_dominant_tag(ings) == "Corrosive"

    def test_deterministic(self):
        ings = [
            _ing("A", "Thermal", "Volatile"),
            _ing("B", "Luminous", "Celestial"),
            _ing("C", "Thermal", "Luminous"),
        ]
        result1 = calculate_dominant_tag(ings)
        result2 = calculate_dominant_tag(ings)
        assert result1 == result2


# ---------------------------------------------------------------------------
# determine_potion
# ---------------------------------------------------------------------------


class TestDeterminePotion:
    def test_below_min_returns_none(self):
        assert determine_potion("Thermal", 99) is None
        assert determine_potion("Thermal", 0) is None

    def test_exactly_100(self):
        result = determine_potion("Thermal", 100)
        assert result is not None
        ptype, val, name = result
        assert ptype == "swiftness"
        assert val == 1
        assert "+1" in name

    def test_swiftness_scaling(self):
        _, val, _ = determine_potion("Thermal", 100)
        assert val == 1
        _, val, _ = determine_potion("Thermal", 110)
        assert val == 2
        _, val, _ = determine_potion("Thermal", 150)
        assert val == 6
        _, val, _ = determine_potion("Thermal", 200)
        assert val == 11

    def test_dexterity(self):
        ptype, val, name = determine_potion("Volatile", 120)
        assert ptype == "dexterity"
        assert val == 3
        assert "Dexterity" in name

    def test_giants_strength(self):
        ptype, val, name = determine_potion("Calcified", 130)
        assert ptype == "giants_strength"
        assert val == 4
        assert "Giant's Strength" in name

    def test_clarity_scaling(self):
        _, val, _ = determine_potion("Stabilizing", 100)
        assert val == 1
        _, val, _ = determine_potion("Stabilizing", 130)
        assert val == 2
        _, val, _ = determine_potion("Stabilizing", 190)
        assert val == 4

    def test_harmony_scaling(self):
        _, val, _ = determine_potion("Resonant", 100)
        assert val == 1
        _, val, _ = determine_potion("Resonant", 120)
        assert val == 2
        _, val, _ = determine_potion("Resonant", 200)
        assert val == 6

    def test_fertility_tiers(self):
        _, val, name = determine_potion("Celestial", 100)
        assert val == 1
        assert "Minor" in name
        _, val, _ = determine_potion("Celestial", 150)
        assert val == 2
        _, val, name = determine_potion("Celestial", 200)
        assert val == 3
        assert "Superior" in name

    def test_longevity_scaling(self):
        _, val, _ = determine_potion("Spectral", 100)
        assert val == 2
        _, val, _ = determine_potion("Spectral", 120)
        assert val == 3
        _, val, _ = determine_potion("Spectral", 200)
        assert val == 7

    def test_stripping_scaling(self):
        _, val, name = determine_potion("Corrosive", 100)
        assert val == 2
        assert "Minor" in name
        _, val, _ = determine_potion("Corrosive", 125)
        assert val == 3
        _, val, name = determine_potion("Corrosive", 200)
        assert val == 6
        assert "Superior" in name

    def test_healing_scaling(self):
        _, val, name = determine_potion("Verdant", 100)
        assert val == 1
        assert "Minor" in name
        _, val, _ = determine_potion("Verdant", 120)
        assert val == 2
        _, val, _ = determine_potion("Verdant", 200)
        assert val == 6

    def test_mutation_scaling(self):
        _, val, name = determine_potion("Mutagenic", 100)
        assert val == 0
        assert "Minor" in name
        _, val, _ = determine_potion("Mutagenic", 115)
        assert val == 3
        _, val, _ = determine_potion("Mutagenic", 130)
        assert val == 6
        _, val, _ = determine_potion("Mutagenic", 200)
        assert val == 15

    def test_fortification_scaling(self):
        _, val, _ = determine_potion("Abyssal", 100)
        assert val == 70
        _, val, _ = determine_potion("Abyssal", 200)
        assert val == 130

    def test_foresight_below_200(self):
        ptype, val, name = determine_potion("Luminous", 150)
        assert ptype == "foresight"
        assert val == 0
        assert "Foresight" in name

    def test_revelation_at_200(self):
        ptype, val, name = determine_potion("Luminous", 200)
        assert ptype == "revelation"
        assert "Revelation" in name

    def test_revelation_above_200(self):
        ptype, _, _ = determine_potion("Luminous", 250)
        assert ptype == "revelation"

    def test_unknown_tag(self):
        assert determine_potion("Nonexistent", 150) is None

    def test_all_tags_produce_potions_at_100(self):
        tags = [
            "Thermal", "Volatile", "Calcified", "Stabilizing",
            "Resonant", "Celestial", "Spectral", "Corrosive",
            "Verdant", "Mutagenic", "Abyssal", "Luminous",
        ]
        for tag in tags:
            result = determine_potion(tag, 100)
            assert result is not None, f"Tag {tag} returned None at potency 100"


# ---------------------------------------------------------------------------
# Potion repository CRUD
# ---------------------------------------------------------------------------

GUILD_ID = 100
USER_ID = 1


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        await seed_if_empty(sess)
        yield sess
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_get_potions(session: AsyncSession):
    potion = await brew_repo.create_player_potion(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        potion_type="swiftness",
        effect_value=3,
        potion_name="Potion of Swiftness +3",
    )
    assert potion.id is not None
    assert potion.potion_type == "swiftness"
    assert potion.effect_value == 3

    potions = await brew_repo.get_player_potions(session, USER_ID, GUILD_ID)
    assert len(potions) == 1
    assert potions[0].id == potion.id


@pytest.mark.asyncio
async def test_get_potion_by_id(session: AsyncSession):
    potion = await brew_repo.create_player_potion(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        potion_type="healing",
        effect_value=2,
        potion_name="Lesser Potion of Healing",
    )
    fetched = await brew_repo.get_player_potion(session, potion.id)
    assert fetched is not None
    assert fetched.potion_name == "Lesser Potion of Healing"

    assert await brew_repo.get_player_potion(session, 9999) is None


@pytest.mark.asyncio
async def test_delete_potion(session: AsyncSession):
    potion = await brew_repo.create_player_potion(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        potion_type="foresight",
        effect_value=0,
        potion_name="Potion of Foresight",
    )
    await brew_repo.delete_player_potion(session, potion.id)
    assert await brew_repo.get_player_potion(session, potion.id) is None


@pytest.mark.asyncio
async def test_potions_guild_isolation(session: AsyncSession):
    await brew_repo.create_player_potion(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        potion_type="swiftness",
        effect_value=1,
        potion_name="Potion of Swiftness +1",
    )
    # Different guild should have no potions
    potions = await brew_repo.get_player_potions(session, USER_ID, 999)
    assert potions == []


@pytest.mark.asyncio
async def test_multiple_potions_same_type(session: AsyncSession):
    await brew_repo.create_player_potion(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        potion_type="swiftness",
        effect_value=1,
        potion_name="Potion of Swiftness +1",
    )
    await brew_repo.create_player_potion(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        potion_type="swiftness",
        effect_value=3,
        potion_name="Potion of Swiftness +3",
    )
    potions = await brew_repo.get_player_potions(session, USER_ID, GUILD_ID)
    assert len(potions) == 2
    values = {p.effect_value for p in potions}
    assert values == {1, 3}
