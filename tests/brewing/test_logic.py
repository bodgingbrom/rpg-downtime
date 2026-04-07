import pytest

from brewing.logic import (
    calculate_instability,
    calculate_payout,
    calculate_potency,
    check_explosion,
    collect_cauldron_tags,
    get_cashout_text,
    get_explosion_text,
    get_flavor_text,
    get_instability_color,
    COLOR_CONCERNING,
    COLOR_DANGEROUS,
    COLOR_SAFE,
    COLOR_UNEASY,
    CASHOUT_HIGH,
    CASHOUT_LEGENDARY,
    CASHOUT_LOW,
    CASHOUT_MEDIUM,
    FLAVOR_CONCERNING,
    FLAVOR_DANGEROUS,
    FLAVOR_SAFE,
    FLAVOR_UNEASY,
)
from brewing.models import DangerousTriple, Ingredient


def _make_ingredient(name: str, tag_1: str, tag_2: str) -> Ingredient:
    """Create an Ingredient instance without persisting to DB."""
    return Ingredient(
        name=name, rarity="free", base_cost=0,
        tag_1=tag_1, tag_2=tag_2, flavor_text="",
    )


def _make_triple(tag_1: str, tag_2: str, tag_3: str, value: int = 50) -> DangerousTriple:
    return DangerousTriple(
        tag_1=tag_1, tag_2=tag_2, tag_3=tag_3, instability_value=value,
    )


# ---------------------------------------------------------------------------
# calculate_potency
# ---------------------------------------------------------------------------


class TestCalculatePotency:
    def test_no_cauldron_returns_min(self):
        ing = _make_ingredient("A", "Thermal", "Volatile")
        assert calculate_potency(ing, [], base_potency=10, min_no_match=2) == 2

    def test_no_tag_match_returns_min(self):
        ing = _make_ingredient("A", "Thermal", "Volatile")
        existing = [_make_ingredient("B", "Luminous", "Celestial")]
        assert calculate_potency(ing, existing, base_potency=10, min_no_match=2) == 2

    def test_single_tag_match(self):
        ing = _make_ingredient("A", "Thermal", "Volatile")
        existing = [_make_ingredient("B", "Thermal", "Luminous")]
        # Thermal matches 1 ingredient, Volatile matches 0
        assert calculate_potency(ing, existing) == 10

    def test_double_tag_match_same_ingredient(self):
        ing = _make_ingredient("A", "Thermal", "Volatile")
        existing = [_make_ingredient("B", "Thermal", "Volatile")]
        # Both tags match the same ingredient: 2 matches
        assert calculate_potency(ing, existing) == 20

    def test_tag_match_across_multiple(self):
        # Ember Salt [Thermal, Volatile] + Brimstone Dust [Thermal, Corrosive]
        # Adding Scorchcap Spore [Thermal, Mutagenic]
        # Thermal matches 2 ingredients, Mutagenic matches 0
        new = _make_ingredient("Scorchcap Spore", "Thermal", "Mutagenic")
        cauldron = [
            _make_ingredient("Ember Salt", "Thermal", "Volatile"),
            _make_ingredient("Brimstone Dust", "Thermal", "Corrosive"),
        ]
        assert calculate_potency(new, cauldron) == 20

    def test_design_doc_example_gloomcap(self):
        # After adding Scorchcap Spore, add Gloomcap [Abyssal, Mutagenic]
        # Mutagenic matches Scorchcap Spore only -> 1 match -> 10
        new = _make_ingredient("Gloomcap", "Abyssal", "Mutagenic")
        cauldron = [
            _make_ingredient("Ember Salt", "Thermal", "Volatile"),
            _make_ingredient("Brimstone Dust", "Thermal", "Corrosive"),
            _make_ingredient("Scorchcap Spore", "Thermal", "Mutagenic"),
        ]
        assert calculate_potency(new, cauldron) == 10

    def test_custom_base_potency(self):
        ing = _make_ingredient("A", "Thermal", "Volatile")
        existing = [_make_ingredient("B", "Thermal", "Luminous")]
        assert calculate_potency(ing, existing, base_potency=20) == 20


# ---------------------------------------------------------------------------
# calculate_instability
# ---------------------------------------------------------------------------


class TestCalculateInstability:
    def test_no_triples_present(self):
        tags = {"Thermal", "Volatile"}
        triples = [_make_triple("Volatile", "Thermal", "Corrosive")]
        assert calculate_instability(tags, triples) == 0

    def test_one_triple_present(self):
        tags = {"Volatile", "Thermal", "Corrosive"}
        triples = [_make_triple("Volatile", "Thermal", "Corrosive")]
        assert calculate_instability(tags, triples) == 50

    def test_two_triples_stack(self):
        tags = {"Volatile", "Thermal", "Corrosive", "Mutagenic", "Celestial"}
        triples = [
            _make_triple("Volatile", "Thermal", "Corrosive"),
            _make_triple("Mutagenic", "Volatile", "Celestial"),
        ]
        assert calculate_instability(tags, triples) == 100

    def test_partial_triple_no_instability(self):
        tags = {"Volatile", "Thermal"}  # Missing Corrosive
        triples = [_make_triple("Volatile", "Thermal", "Corrosive")]
        assert calculate_instability(tags, triples) == 0

    def test_custom_instability_value(self):
        tags = {"Volatile", "Thermal", "Corrosive"}
        triples = [_make_triple("Volatile", "Thermal", "Corrosive", value=75)]
        assert calculate_instability(tags, triples) == 75


# ---------------------------------------------------------------------------
# check_explosion
# ---------------------------------------------------------------------------


class TestCheckExplosion:
    def test_below_threshold(self):
        assert check_explosion(49, 50) is False

    def test_at_threshold(self):
        assert check_explosion(50, 50) is True

    def test_above_threshold(self):
        assert check_explosion(100, 70) is True

    def test_zero_instability(self):
        assert check_explosion(0, 70) is False


# ---------------------------------------------------------------------------
# calculate_payout
# ---------------------------------------------------------------------------


class TestCalculatePayout:
    def test_tier_0_to_10(self):
        assert calculate_payout(0) == 0
        assert calculate_payout(10) == 5  # 10 * 0.5

    def test_tier_11_to_30(self):
        assert calculate_payout(11) == 11  # 11 * 1.0
        assert calculate_payout(30) == 30

    def test_tier_31_to_60(self):
        assert calculate_payout(31) == 46  # 31 * 1.5 = 46.5 -> 46
        assert calculate_payout(60) == 90

    def test_tier_61_to_100(self):
        assert calculate_payout(61) == 152  # 61 * 2.5 = 152.5 -> 152
        assert calculate_payout(100) == 250

    def test_tier_101_to_150(self):
        assert calculate_payout(101) == 404  # 101 * 4.0
        assert calculate_payout(150) == 600

    def test_tier_151_to_200(self):
        assert calculate_payout(151) == 906  # 151 * 6.0
        assert calculate_payout(200) == 1200

    def test_tier_legendary(self):
        assert calculate_payout(201) == 1608  # 201 * 8.0
        assert calculate_payout(250) == 2000


# ---------------------------------------------------------------------------
# Color and flavor text
# ---------------------------------------------------------------------------


class TestInstabilityColor:
    def test_safe(self):
        assert get_instability_color(0) == COLOR_SAFE

    def test_uneasy(self):
        assert get_instability_color(1) == COLOR_UNEASY
        assert get_instability_color(30) == COLOR_UNEASY

    def test_concerning(self):
        assert get_instability_color(31) == COLOR_CONCERNING
        assert get_instability_color(60) == COLOR_CONCERNING

    def test_dangerous(self):
        assert get_instability_color(61) == COLOR_DANGEROUS
        assert get_instability_color(99) == COLOR_DANGEROUS


class TestFlavorText:
    def test_safe_text(self):
        assert get_flavor_text(0) in FLAVOR_SAFE

    def test_uneasy_text(self):
        assert get_flavor_text(15) in FLAVOR_UNEASY

    def test_concerning_text(self):
        assert get_flavor_text(45) in FLAVOR_CONCERNING

    def test_dangerous_text(self):
        assert get_flavor_text(80) in FLAVOR_DANGEROUS

    def test_explosion_text_not_empty(self):
        text = get_explosion_text()
        assert len(text) > 0
        assert "BOOM" in text or "crack" in text or "shatters" in text

    def test_cashout_tiers(self):
        assert get_cashout_text(10) == CASHOUT_LOW
        assert get_cashout_text(50) == CASHOUT_MEDIUM
        assert get_cashout_text(150) == CASHOUT_HIGH
        assert get_cashout_text(250) == CASHOUT_LEGENDARY


# ---------------------------------------------------------------------------
# collect_cauldron_tags
# ---------------------------------------------------------------------------


class TestCollectCauldronTags:
    def test_empty(self):
        assert collect_cauldron_tags([]) == set()

    def test_single(self):
        ing = _make_ingredient("A", "Thermal", "Volatile")
        assert collect_cauldron_tags([ing]) == {"Thermal", "Volatile"}

    def test_deduplicates(self):
        ings = [
            _make_ingredient("A", "Thermal", "Volatile"),
            _make_ingredient("B", "Thermal", "Luminous"),
        ]
        assert collect_cauldron_tags(ings) == {"Thermal", "Volatile", "Luminous"}
