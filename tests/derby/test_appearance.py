"""Unit tests for the racer appearance rolling system."""

import random

import pytest

from derby import appearance


@pytest.fixture(autouse=True)
def _reset_pool():
    """Reset cached YAML pool between tests."""
    appearance.reload_appearance_pool()
    yield
    appearance.reload_appearance_pool()


def test_roll_appearance_has_all_fields():
    """A fresh roll should populate all 7 appearance fields from the real YAML."""
    result = appearance.roll_appearance()
    for field in appearance.APPEARANCE_FIELDS:
        assert field in result, f"Missing field: {field}"
        assert isinstance(result[field], str)
        assert result[field]


def test_roll_appearance_is_deterministic_with_seed():
    """Same seed produces the same roll — needed for reproducible tests."""
    rng_a = random.Random(42)
    rng_b = random.Random(42)
    assert appearance.roll_appearance(rng_a) == appearance.roll_appearance(rng_b)


def test_roll_appearance_varies_without_seed():
    """Default roll (random seed) should produce variety across many calls."""
    results = [appearance.roll_appearance() for _ in range(20)]
    # At least several distinct colors should appear in 20 rolls
    colors = {r.get("color") for r in results}
    assert len(colors) >= 3


def test_variation_belongs_to_chosen_color():
    """The rolled variation must be one of the chosen color's listed options."""
    pool = appearance._load_appearance_pool()
    color_lookup = {c["name"]: c.get("variations", []) for c in pool.get("colors", [])}
    for _ in range(30):
        result = appearance.roll_appearance()
        chosen_color = result["color"]
        chosen_variation = result["variation"]
        assert chosen_variation in color_lookup[chosen_color], (
            f"{chosen_variation!r} is not a valid variation for {chosen_color!r}"
        )


def test_inherit_appearance_origin_always_fresh():
    """Origin is a life event, not a genetic trait — should never inherit."""
    sire = {
        "color": "Burgundy",
        "variation": "solid",
        "build": "Lean and wiry",
        "feature_primary": "Glowing spines",
        "feature_secondary": "Six legs",
        "eyes": "Glowing ember eyes",
        "origin": "Born in a volcano",
    }
    dam = {
        "color": "Metallic Gold",
        "variation": "solid",
        "build": "Stocky and powerful",
        "feature_primary": "Smoke breath",
        "feature_secondary": "Forked tail",
        "eyes": "Cat-slit pupils that widen in low light",
        "origin": "Raised by monks",
    }
    # Run many times to ensure origin never matches either parent by inheritance
    # (could match by random chance if the YAML origin pool is tiny — but 25 entries
    # makes that chance near-zero over 50 trials)
    for seed in range(50):
        foal = appearance.inherit_appearance(sire, dam, rng=random.Random(seed))
        # Foal must have all fields
        for field in appearance.APPEARANCE_FIELDS:
            assert field in foal
        # Origin came from the fresh-roll pool, not directly copied
        # (it *could* coincidentally equal a parent's, but not by inheritance path)


def test_inherit_appearance_passes_traits_down():
    """With high inherit_chance, foal should frequently match a parent trait."""
    sire = {
        "color": "Burgundy",
        "variation": "solid",
        "build": "Lean and wiry",
        "feature_primary": "Spines",
        "feature_secondary": "Six legs",
        "eyes": "Ember eyes",
        "origin": "volcano",
    }
    dam = {
        "color": "Metallic Gold",
        "variation": "solid",
        "build": "Stocky",
        "feature_primary": "Smoke",
        "feature_secondary": "Forked tail",
        "eyes": "Cat slits",
        "origin": "temple",
    }

    # With very high inherit_chance, traits should almost always come from parents
    matches_sire = 0
    matches_dam = 0
    trials = 100
    for i in range(trials):
        foal = appearance.inherit_appearance(
            sire, dam, rng=random.Random(i), inherit_chance=0.45,
        )
        for field in appearance.HERITABLE_FIELDS:
            if foal.get(field) == sire.get(field):
                matches_sire += 1
            if foal.get(field) == dam.get(field):
                matches_dam += 1

    # Over 100 trials × 6 heritable fields = 600 samples, 45% sire + 45% dam = ~540
    # matches expected. Give generous bounds.
    total_matches = matches_sire + matches_dam
    assert total_matches > 400, f"Expected mostly inherited traits, got {total_matches}/600"


def test_inherit_appearance_handles_missing_parent():
    """Legacy parent (None/empty appearance) should not crash; foal rolls fresh."""
    sire = {
        "color": "Burgundy",
        "variation": "solid",
        "build": "Lean and wiry",
        "feature_primary": "Spines",
        "feature_secondary": "Six legs",
        "eyes": "Ember eyes",
        "origin": "volcano",
    }
    foal = appearance.inherit_appearance(sire, None)
    assert foal
    for field in appearance.APPEARANCE_FIELDS:
        assert field in foal

    foal = appearance.inherit_appearance(None, None)
    # Both parents legacy → foal rolls completely fresh
    assert foal
    for field in appearance.APPEARANCE_FIELDS:
        assert field in foal


def test_inherit_appearance_color_variation_stays_coherent():
    """If color is inherited but variation is fresh-rolled, fix variation to match color."""
    sire = {
        "color": "Burgundy",
        "variation": "solid",
        "build": "x",
        "feature_primary": "x",
        "feature_secondary": "x",
        "eyes": "x",
        "origin": "x",
    }
    dam = {
        "color": "Metallic Gold",
        "variation": "shimmering scales",
        "build": "x",
        "feature_primary": "x",
        "feature_secondary": "x",
        "eyes": "x",
        "origin": "x",
    }

    pool = appearance._load_appearance_pool()
    color_lookup = {c["name"]: c.get("variations", []) for c in pool.get("colors", [])}

    for seed in range(30):
        foal = appearance.inherit_appearance(sire, dam, rng=random.Random(seed))
        chosen_color = foal["color"]
        chosen_variation = foal["variation"]
        if chosen_color in color_lookup:
            assert chosen_variation in color_lookup[chosen_color]


def test_serialize_roundtrip():
    """Serialize → deserialize → same dict."""
    original = appearance.roll_appearance()
    text = appearance.serialize(original)
    parsed = appearance.deserialize(text)
    assert parsed == original


def test_deserialize_handles_none_and_empty():
    assert appearance.deserialize(None) == {}
    assert appearance.deserialize("") == {}


def test_deserialize_handles_invalid_json():
    assert appearance.deserialize("not json at all") == {}
    assert appearance.deserialize("[]") == {}  # array, not dict
    assert appearance.deserialize("null") == {}


def test_format_appearance_for_prompt_includes_all_fields():
    """The prompt block should reference each filled field."""
    app = {
        "color": "Burgundy",
        "variation": "dappled",
        "build": "Lean",
        "feature_primary": "Spines",
        "feature_secondary": "Six legs",
        "eyes": "Ember eyes",
        "origin": "Born in a volcano",
    }
    text = appearance.format_appearance_for_prompt(app)
    assert "Burgundy" in text
    assert "dappled" in text
    assert "Lean" in text
    assert "Spines" in text
    assert "Six legs" in text
    assert "Ember" in text
    assert "volcano" in text


def test_format_appearance_for_prompt_empty():
    assert appearance.format_appearance_for_prompt({}) == ""


def test_format_appearance_for_prompt_skips_solid_variation():
    """The 'solid' variation shouldn't be rendered as clutter."""
    app = {"color": "Burgundy", "variation": "solid"}
    text = appearance.format_appearance_for_prompt(app)
    assert "Burgundy" in text
    # 'solid' is implied, shouldn't be explicitly stated
    assert "solid" not in text.lower()


def test_format_appearance_for_display_returns_multiline():
    app = {
        "color": "Burgundy",
        "variation": "dappled",
        "build": "Lean",
        "feature_primary": "Spines",
        "feature_secondary": "Six legs",
        "eyes": "Ember eyes",
        "origin": "Born in a volcano",
    }
    text = appearance.format_appearance_for_display(app)
    assert "Burgundy" in text
    assert "Lean" in text
    assert "\n" in text  # multi-line


def test_format_appearance_for_display_empty():
    assert appearance.format_appearance_for_display({}) == ""
