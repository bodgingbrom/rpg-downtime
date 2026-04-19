"""Unit tests for the racer abilities system."""

import random
from types import SimpleNamespace

import pytest

from derby import abilities


@pytest.fixture(autouse=True)
def _reset_pool():
    abilities.reload_abilities()
    yield
    abilities.reload_abilities()


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def test_load_abilities_returns_nonempty_dict():
    pool = abilities.load_abilities()
    assert pool
    # Every key in the dict should map to an Ability instance with the same key
    for key, ab in pool.items():
        assert isinstance(ab, abilities.Ability)
        assert ab.key == key


def test_yaml_pools_all_present():
    pool = abilities._load_ability_pool()
    for section in ("speed_pool", "cornering_pool", "stamina_pool", "quirk_pool"):
        assert section in pool, f"Missing YAML section: {section}"
        assert pool[section], f"Empty section: {section}"


# ---------------------------------------------------------------------------
# Rolling
# ---------------------------------------------------------------------------


def test_roll_abilities_signature_matches_highest_stat_pool():
    """High speed racer should roll signature from speed_pool."""
    racer = SimpleNamespace(
        speed=30, cornering=5, stamina=5, temperament="Bold",
    )
    pool = abilities._load_ability_pool()
    speed_keys = {e["key"] for e in pool["speed_pool"]}

    rng = random.Random(0)
    for _ in range(20):
        sig, _ = abilities.roll_abilities(racer, rng=rng)
        assert sig in speed_keys, f"{sig} should be a speed pool ability"


def test_roll_abilities_quirk_from_quirk_pool():
    racer = SimpleNamespace(
        speed=30, cornering=5, stamina=5, temperament="Bold",
    )
    pool = abilities._load_ability_pool()
    quirk_keys = {e["key"] for e in pool["quirk_pool"]}

    rng = random.Random(0)
    for _ in range(20):
        _, quirk = abilities.roll_abilities(racer, rng=rng)
        assert quirk in quirk_keys


def test_roll_abilities_respects_temperament_weight():
    """Quirky racers should roll Quirky-weighted abilities more often."""
    quirky_racer = SimpleNamespace(
        speed=10, cornering=10, stamina=10, temperament="Quirky",
    )
    rng = random.Random(42)
    # Count how many Quirky-weighted abilities we get over many trials
    quirky_weighted_keys = {"slow_starter", "dramatic_finisher", "glass_cannon",
                            "lucky_break", "wild_card", "sibling_rivalry"}
    quirky_hits = 0
    trials = 200
    for _ in range(trials):
        _, quirk = abilities.roll_abilities(quirky_racer, rng=rng)
        if quirk in quirky_weighted_keys:
            quirky_hits += 1
    # With 2x-3x weights on many quirky abilities, expect at least 40% hits
    assert quirky_hits > trials * 0.35, (
        f"Quirky temperament weighting too weak: {quirky_hits}/{trials}"
    )


def test_roll_abilities_returns_empty_on_missing_yaml(monkeypatch):
    monkeypatch.setattr(abilities, "_load_ability_pool", lambda: {})
    racer = SimpleNamespace(speed=10, cornering=10, stamina=10, temperament="Bold")
    sig, quirk = abilities.roll_abilities(racer)
    assert sig == ""
    assert quirk == ""


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


def test_inherit_abilities_at_least_one_slot_can_match_parent():
    """Across many foals, some slots should match a parent's ability."""
    foal = SimpleNamespace(
        speed=15, cornering=15, stamina=15, temperament="Bold",
    )
    parent_sig_a = "closing_surge"
    parent_quirk_a = "rival_hunter"
    parent_sig_b = "second_wind"
    parent_quirk_b = "slow_starter"
    matches = 0
    trials = 100
    for i in range(trials):
        rng = random.Random(i)
        foal_sig, foal_quirk = abilities.inherit_abilities(
            parent_sig_a, parent_quirk_a,
            parent_sig_b, parent_quirk_b,
            foal, rng=rng, inherit_chance=0.45,
        )
        if foal_sig in (parent_sig_a, parent_sig_b):
            matches += 1
        if foal_quirk in (parent_quirk_a, parent_quirk_b):
            matches += 1
    # Over 200 slot samples, inherit_chance=0.45 each parent = ~90% should match
    assert matches > 100, f"Inheritance too weak: {matches}/200"


def test_inherit_abilities_handles_legacy_parent():
    """NULL parent abilities should fall back to fresh roll without crashing."""
    foal = SimpleNamespace(
        speed=15, cornering=15, stamina=15, temperament="Bold",
    )
    # Both parents legacy (all None) → both slots fresh-rolled
    sig, quirk = abilities.inherit_abilities(None, None, None, None, foal)
    assert sig  # should have rolled fresh
    assert quirk


def test_inherit_abilities_no_duplicate_keys():
    """Foal should not have the same ability in both slots."""
    foal = SimpleNamespace(
        speed=15, cornering=15, stamina=15, temperament="Bold",
    )
    # Pathological case: both parents have the same key in both slots
    for i in range(50):
        rng = random.Random(i)
        sig, quirk = abilities.inherit_abilities(
            "closing_surge", "closing_surge",
            "closing_surge", "closing_surge",
            foal, rng=rng, inherit_chance=1.0,  # force inheritance
        )
        assert sig != quirk or quirk == ""


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------


def _make_ctx(**overrides):
    """Build a default SegmentContext with sensible defaults."""
    defaults = dict(
        racer_id=1,
        segment_index=0,
        total_segments=4,
        segment_type="straight",
        position=1,
        field_size=4,
        is_stumbling=False,
        surged=False,
        gained_position_last_segment=False,
        lost_position_last_segment=False,
        stumbled_last_segment=False,
        rival_ranks=["D", "D", "D"],
        own_rank="D",
        sibling_ids_in_field=set(),
        is_offspring_of_winner=False,
        once_per_race_fired=set(),
    )
    defaults.update(overrides)
    return abilities.SegmentContext(**defaults)


def test_evaluate_final_stretch_trailing_trigger():
    """closing_surge fires when segment=final_stretch and position=trailing."""
    ctx = _make_ctx(
        segment_index=3, total_segments=4, position=4, field_size=4,
    )
    assert ctx.segment_phase == "final_stretch"
    assert ctx.position_band == "trailing"
    procs = abilities.evaluate("closing_surge", None, "Thunderhoof", ctx, random.Random(0))
    assert len(procs) == 1
    assert procs[0].ability.key == "closing_surge"


def test_evaluate_respects_once_per_race():
    """Once-per-race abilities fire exactly once per SegmentContext state."""
    ctx = _make_ctx(once_per_race_fired={"flash_start"})
    procs = abilities.evaluate("flash_start", None, "x", ctx, random.Random(0))
    assert len(procs) == 0


def test_evaluate_stumble_event():
    """sure_footed fires when the racer is stumbling this segment."""
    ctx = _make_ctx(is_stumbling=True)
    procs = abilities.evaluate("sure_footed", None, "x", ctx, random.Random(0))
    assert len(procs) == 1
    assert procs[0].ability.key == "sure_footed"
    assert abilities.is_stumble_save(procs[0].effect)


def test_evaluate_rival_higher_rank():
    """rival_hunter fires only when a higher-ranked racer is in the field."""
    # Equal ranks — should not fire
    ctx_equal = _make_ctx(
        segment_index=3, total_segments=4, own_rank="A",
        rival_ranks=["A", "A", "A"],
    )
    procs = abilities.evaluate(None, "rival_hunter", "x", ctx_equal, random.Random(0))
    assert len(procs) == 0
    # Higher rank present — should fire
    ctx_rival = _make_ctx(
        segment_index=3, total_segments=4, own_rank="B",
        rival_ranks=["A", "B", "C"],
    )
    procs = abilities.evaluate(None, "rival_hunter", "x", ctx_rival, random.Random(0))
    assert len(procs) == 1


def test_evaluate_sibling_in_field():
    ctx_no_sib = _make_ctx(sibling_ids_in_field=set())
    procs = abilities.evaluate(None, "sibling_rivalry", "x", ctx_no_sib, random.Random(0))
    assert len(procs) == 0
    ctx_sib = _make_ctx(sibling_ids_in_field={5})
    procs = abilities.evaluate(None, "sibling_rivalry", "x", ctx_sib, random.Random(0))
    assert len(procs) == 1


def test_evaluate_segment_phase_computation():
    """segment_phase should return opening, mid, or final_stretch correctly."""
    assert _make_ctx(segment_index=0, total_segments=4).segment_phase == "opening"
    assert _make_ctx(segment_index=1, total_segments=4).segment_phase == "mid"
    assert _make_ctx(segment_index=2, total_segments=4).segment_phase == "mid"
    assert _make_ctx(segment_index=3, total_segments=4).segment_phase == "final_stretch"
    # Edge: single-segment race → always final stretch
    assert _make_ctx(segment_index=0, total_segments=1).segment_phase == "final_stretch"


def test_evaluate_position_band():
    """position_band splits into leading/mid_pack/trailing by position."""
    assert _make_ctx(position=1, field_size=6).position_band == "leading"
    assert _make_ctx(position=3, field_size=6).position_band == "mid_pack"
    assert _make_ctx(position=6, field_size=6).position_band == "trailing"


def test_evaluate_segment_type_trigger():
    """cornering_expert needs segment_type=corner + position=leading."""
    ctx = _make_ctx(segment_type="corner", position=1, field_size=4)
    procs = abilities.evaluate("cornering_expert", None, "x", ctx, random.Random(0))
    assert len(procs) == 1
    # On a straight, it should NOT fire
    ctx2 = _make_ctx(segment_type="straight", position=1, field_size=4)
    procs2 = abilities.evaluate("cornering_expert", None, "x", ctx2, random.Random(0))
    assert len(procs2) == 0


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------


def test_apply_score_effect_score_bonus():
    assert abilities.apply_score_effect(
        {"kind": "score_bonus", "value": 3}, 10.0, "final_stretch"
    ) == 13.0


def test_apply_score_effect_segment_ramp():
    effect = {"kind": "segment_ramp", "values": {"opening": -1, "mid": 0, "final_stretch": 2}}
    assert abilities.apply_score_effect(effect, 10.0, "opening") == 9.0
    assert abilities.apply_score_effect(effect, 10.0, "mid") == 10.0
    assert abilities.apply_score_effect(effect, 10.0, "final_stretch") == 12.0


def test_apply_score_effect_stumble_save_doesnt_affect_score():
    """stumble_save applies elsewhere; apply_score_effect leaves score alone."""
    assert abilities.apply_score_effect(
        {"kind": "stumble_save"}, 10.0, "mid"
    ) == 10.0


# ---------------------------------------------------------------------------
# Display + commentary
# ---------------------------------------------------------------------------


def test_display_summary_renders_both_slots():
    text = abilities.display_summary("closing_surge", "rival_hunter")
    assert "Closing Surge" in text
    assert "Rival Hunter" in text
    # Each slot on its own line
    assert "\n" in text


def test_display_summary_handles_unknown_key():
    """Unknown keys should be skipped silently."""
    text = abilities.display_summary("closing_surge", "does_not_exist")
    assert "Closing Surge" in text
    assert "does_not_exist" not in text


def test_display_summary_empty():
    assert abilities.display_summary(None, None) == ""


def test_format_commentary_event_prefix():
    ctx = _make_ctx(segment_index=3, total_segments=4, position=4, field_size=4)
    procs = abilities.evaluate("closing_surge", None, "Thunderhoof", ctx, random.Random(0))
    assert procs
    text = abilities.format_commentary_event(procs[0], "🟥")
    assert text.startswith("[ABILITY 🟥]")
    assert "Closing Surge" in text
    assert "Thunderhoof" in text  # name substituted


# ---------------------------------------------------------------------------
# Race colors
# ---------------------------------------------------------------------------


def test_assign_race_colors_unique_per_racer():
    colors = abilities.assign_race_colors([10, 20, 30, 40])
    assert len(set(colors.values())) == 4
    assert all(c in abilities.RACE_COLOR_PALETTE for c in colors.values())


def test_assign_race_colors_stable_across_calls():
    colors_a = abilities.assign_race_colors([10, 20, 30])
    colors_b = abilities.assign_race_colors([10, 20, 30])
    assert colors_a == colors_b


def test_assign_race_colors_order_independent():
    """Same racer ids in different input order → same color assignments."""
    colors_a = abilities.assign_race_colors([10, 20, 30])
    colors_b = abilities.assign_race_colors([30, 10, 20])
    assert colors_a == colors_b


def test_assign_race_colors_wraps_for_oversize_field():
    """If field is bigger than palette, wrap cleanly rather than crash."""
    many_ids = list(range(12))
    colors = abilities.assign_race_colors(many_ids)
    assert len(colors) == 12
    assert all(c in abilities.RACE_COLOR_PALETTE for c in colors.values())
