"""Smoke tests: new dungeon YAMLs load cleanly and backgrounds are retrofitted.

These tests don't exercise combat — they verify the DATA shape so that
the dungeon schema stays honest as the dungeon evolves.
"""
from __future__ import annotations

import pytest

from dungeon import effects as fx
from dungeon import logic as dungeon_logic


# Reset the module-level cache once per session so the tests always read
# from disk rather than an import-time snapshot.
@pytest.fixture(autouse=True)
def _reset_dungeon_cache():
    dungeon_logic._dungeons_cache = None
    yield
    dungeon_logic._dungeons_cache = None


def test_all_three_dungeons_load():
    dungeons = dungeon_logic.load_dungeons()
    keys = set(dungeons.keys())
    assert "goblin_warrens" in keys
    assert "the_undercrypt" in keys
    assert "the_cartographers_folly" in keys


def test_existing_dungeons_have_background_retrofitted():
    dungeons = dungeon_logic.load_dungeons()
    for key in ("goblin_warrens", "the_undercrypt"):
        d = dungeons[key]
        assert "background" in d, f"{key} missing background"
        bg = d["background"]
        assert bg.get("tone"), f"{key} background missing tone"
        assert bg.get("pitch"), f"{key} background missing pitch"
        assert bg.get("lore"), f"{key} background missing lore"


def test_cartographers_folly_has_full_background():
    d = dungeon_logic.load_dungeons()["the_cartographers_folly"]
    bg = d["background"]
    assert bg.get("tone")
    assert bg.get("pitch")
    assert bg.get("lore")
    assert isinstance(bg.get("dm_hooks"), list) and bg["dm_hooks"]
    assert bg.get("style_notes")


def test_cartographers_folly_has_three_floors():
    d = dungeon_logic.load_dungeons()["the_cartographers_folly"]
    floors = d["floors"]
    assert len(floors) == 3
    for f in floors:
        assert f.get("background"), f"floor {f['floor']} missing background"


def test_cartographers_folly_floor1_has_scale_bar_with_dice_escalation():
    d = dungeon_logic.load_dungeons()["the_cartographers_folly"]
    boss = d["floors"][0]["boss"]
    assert boss["id"] == "the_scale_bar"
    abilities = boss.get("abilities") or []
    dice_step = [a for a in abilities if a.get("type") == "dice_step_self"]
    assert dice_step, "Scale Bar missing dice_step_self ability"
    schedule = dice_step[0]["step_schedule"]
    steps = {entry["turn"]: entry["step"] for entry in schedule}
    # Escalation plan from the design: turn 3→step 1, turn 5→step 2, turn 7→step 3
    assert steps.get(3) == 1
    assert steps.get(5) == 2
    assert steps.get(7) == 3


def test_cartographers_folly_floor2_terrain_swatch_has_three_variants():
    d = dungeon_logic.load_dungeons()["the_cartographers_folly"]
    monsters = d["floors"][1]["monsters"]
    swatch = next(m for m in monsters if m["id"] == "terrain_swatch")
    variants = swatch.get("variants")
    assert variants and len(variants) == 3
    keys = {v["key"] for v in variants}
    assert keys == {"swamp", "forest", "mountain"}


def test_cartographers_folly_floor2_key_legend_has_phases_and_random_pool():
    d = dungeon_logic.load_dungeons()["the_cartographers_folly"]
    boss = d["floors"][1]["boss"]
    assert boss["id"] == "the_key_legend"
    # Two phase thresholds (66, 33) — baseline "Inked" is implicit phase 0.
    phases = boss.get("phases") or []
    thresholds = [p.get("hp_below_pct") for p in phases]
    assert sorted(thresholds, reverse=True) == [66, 33]
    abilities = boss.get("abilities") or []
    pools = [a for a in abilities if a.get("type") == "random_effect_pool"]
    assert pools, "Key Legend missing random_effect_pool"
    assert pools[0]["every"] == 2
    # Pool contains the expected weird effects.
    pool_types = {p["type"] for p in pools[0]["pool"]}
    # Must include at least the signature pair: invert + advantage.
    assert "player_next_attack_invert" in pool_types
    assert "player_next_attack_advantage" in pool_types


def test_cartographers_folly_floor3_wandering_landmark_has_description_pool():
    d = dungeon_logic.load_dungeons()["the_cartographers_folly"]
    monsters = d["floors"][2]["monsters"]
    landmark = next(m for m in monsters if m["id"] == "wandering_landmark")
    pool = landmark.get("description_pool") or []
    assert len(pool) >= 4
    assert all(isinstance(s, str) and len(s) > 0 for s in pool)


def test_cartographers_folly_alaric_has_all_signature_pieces():
    d = dungeon_logic.load_dungeons()["the_cartographers_folly"]
    alaric = d["floors"][2]["boss"]
    assert alaric["id"] == "alaric_venn"
    # Phase 2 (wall) + phase 3 (existential strike).
    phases = alaric.get("phases") or []
    assert any(p.get("hp_below_pct") == 66 for p in phases)
    assert any(p.get("hp_below_pct") == 33 for p in phases)
    # Summoner ability present with phase gate and pool of multiple adds.
    abilities = alaric.get("abilities") or []
    summons = [a for a in abilities if a.get("type") == "summon_add"]
    assert summons, "Alaric missing summon_add ability"
    summon = summons[0]
    # BUG FIX: summon must be phase-gated to baseline only (stops in phase 2+).
    assert summon.get("phase_max") == 0, (
        "Alaric's summon_add must have phase_max: 0 or he summons forever "
        "(the bug that shipped in the original PR)."
    )
    # BUG FIX: summon must pick from a pool of at least 2 monsters, not just hounds.
    pool = summon.get("add_pool")
    assert isinstance(pool, list) and len(pool) >= 2, (
        "Alaric's summon_add must have an add_pool with at least 2 options"
    )

    # Verify every summon target is defined somewhere in the dungeon.
    all_monster_ids: set[str] = set()
    for f in d["floors"]:
        for m in f.get("monsters", []) or []:
            all_monster_ids.add(m["id"])
        b = f.get("boss") or {}
        if b.get("id"):
            all_monster_ids.add(b["id"])
    for add_id in pool:
        assert add_id in all_monster_ids, (
            f"summon references undefined monster '{add_id}'"
        )
    # Signature death line.
    assert alaric.get("on_death_narration"), "Alaric missing on_death_narration"
    assert "signature" in alaric["on_death_narration"].lower() \
        or "A. Venn" in alaric["on_death_narration"]


def test_cartographers_folly_has_inkwash_wraith_as_floor3_monster():
    """The inkwash_wraith was missing from the original commit — verify it's
    present as a standalone floor-3 monster so Alaric's summon_add can find
    it and so it can appear in regular combat rooms too."""
    d = dungeon_logic.load_dungeons()["the_cartographers_folly"]
    monster_ids = {m["id"] for m in d["floors"][2].get("monsters", [])}
    assert "inkwash_wraith" in monster_ids
    assert "scribbled_hound" in monster_ids


def test_cartographers_folly_has_no_free_tier_ingredients():
    """Per policy: dungeons never drop free-tier brewing ingredients."""
    FREE_INGREDIENTS = {
        "Ember Salt", "Moonpetal", "Wraith Moss",
        "Iron Root", "Gloomcap", "Brimstone Dust",
    }
    d = dungeon_logic.load_dungeons()["the_cartographers_folly"]
    offenders: list[str] = []
    for f in d["floors"]:
        for m in list(f.get("monsters", []) or []) + [f.get("boss") or {}]:
            for drop in m.get("loot", []) or []:
                if drop.get("type") == "cross_game_ingredient":
                    if drop.get("item_id") in FREE_INGREDIENTS:
                        offenders.append(f"{m.get('id')}:{drop['item_id']}")
    assert not offenders, f"free-tier drops found: {offenders}"


def test_all_dungeons_pass_ability_validation():
    """Integration: if any monster's YAML fails schema, load_dungeons skips
    the dungeon silently (logged to stderr). If all three load, validation
    passed for everything."""
    dungeons = dungeon_logic.load_dungeons()
    # We assert all three load — i.e. none were skipped due to validation errors.
    assert {"goblin_warrens", "the_undercrypt", "the_cartographers_folly"}.issubset(
        dungeons.keys()
    )


def test_explicit_validation_run_clean():
    """Belt-and-braces: validate each monster directly."""
    dungeons = dungeon_logic.load_dungeons()
    errors: list[str] = []
    for key, d in dungeons.items():
        for f in d.get("floors", []):
            for m in f.get("monsters", []) or []:
                errors.extend(fx.validate_monster(m, path=f"[{key}] {m.get('id')}: "))
            boss = f.get("boss") or {}
            if boss:
                errors.extend(fx.validate_monster(boss, path=f"[{key}] {boss.get('id')}: "))
    assert errors == [], f"validation errors: {errors}"
