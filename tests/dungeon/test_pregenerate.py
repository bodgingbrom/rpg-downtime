"""Tests for the v2 pre-generation pass — pre-rolled feature content +
batched LLM narration cached on floor_state.

Pre-generation is two parts:
  1. ``initial_floor_state`` calls ``roll_feature_content`` for every
     authored feature, storing the result on ``room_states[node].pre_rolled_rewards``.
  2. ``_v2_pregenerate_narration`` (in cogs/dungeon.py) fires every LLM
     call in parallel and stashes results on
     ``room_states[node].llm_intro`` and ``llm_search_outcomes``.

``take_investigate`` then reads from the cache: pre-rolled rewards + LLM
narration. No click-time RNG, no click-time LLM call.
"""
from __future__ import annotations

import asyncio
import random
from unittest.mock import AsyncMock, MagicMock

import pytest

from dungeon import explore


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _floor():
    return {
        "floor": 1,
        "layout": {"rooms_per_run": [3, 3]},
        "anchors": [
            {"position": "entrance", "room_id": "a"},
            {"position": "boss", "room_id": "c"},
        ],
        "wandering_threshold": 99,
        "wandering_pool": [],
        "monsters": [],
        "boss": {"id": "boss", "hp": 5, "defense": 0, "attack_dice": "1d4",
                 "attack_bonus": 0, "xp": 1, "gold": [1, 1], "ai": {"attack": 100}},
        "room_pool": [
            {
                "id": "a",
                "description_pool": ["A."],
                "ambient_pool": ["draft."],
                "features": [
                    {
                        "id": "chest",
                        "name": "chest",
                        "visibility": "visible",
                        "content": [
                            {"type": "gold", "amount": [10, 20], "chance": 1.0},
                        ],
                    },
                    {
                        "id": "loose_stone",
                        "name": "loose stone",
                        "visibility": "concealed",
                        "perception_dc": 12,
                        "content": [
                            {"type": "item", "item_id": "potion", "chance": 1.0},
                        ],
                    },
                ],
            },
            {"id": "b", "description_pool": ["B."]},
            {"id": "c", "description_pool": ["C."]},
        ],
    }


# ---------------------------------------------------------------------------
# roll_feature_content — the pre-roll primitive.
# ---------------------------------------------------------------------------


def test_roll_feature_content_returns_concrete_amounts():
    feature = {
        "content": [
            {"type": "gold", "amount": [5, 5], "chance": 1.0},
            {"type": "item", "item_id": "potion", "chance": 1.0},
        ],
    }
    rewards = explore.roll_feature_content(feature, random.Random(0))
    assert rewards == [
        {"type": "gold", "amount": 5},
        {"type": "item", "item_id": "potion"},
    ]


def test_roll_feature_content_skips_failed_chance_rolls():
    feature = {"content": [{"type": "gold", "amount": [5, 5], "chance": 0.0}]}
    rewards = explore.roll_feature_content(feature, random.Random(0))
    assert rewards == []


def test_roll_feature_content_includes_narrate_entries():
    feature = {"content": [{"type": "narrate", "text": "Hello."}]}
    rewards = explore.roll_feature_content(feature, random.Random(0))
    assert rewards == [{"type": "narrate", "text": "Hello."}]


def test_roll_feature_content_handles_corpse_recovery():
    feature = {"content": [{
        "type": "corpse_recovery",
        "loot": [
            {"type": "gold", "amount": [3, 3]},
            {"type": "item", "item_id": "rope"},
        ],
        "chance": 1.0,
    }]}
    rewards = explore.roll_feature_content(feature, random.Random(0))
    types = [r["type"] for r in rewards]
    assert "gold" in types and "item" in types and "corpse_recovered" in types


def test_roll_feature_content_seed_determinism():
    feature = {"content": [{"type": "gold", "amount": [1, 100], "chance": 1.0}]}
    a = explore.roll_feature_content(feature, random.Random(42))
    b = explore.roll_feature_content(feature, random.Random(42))
    assert a == b


# ---------------------------------------------------------------------------
# initial_floor_state — pre-roll cache shape.
# ---------------------------------------------------------------------------


def test_initial_floor_state_pre_rolls_every_feature():
    state = explore.initial_floor_state(_floor(), random.Random(0))
    pre = state["room_states"][state["current"]]["pre_rolled_rewards"]
    # The alcove room has two features.
    assert "chest" in pre
    assert "loose_stone" in pre
    # And the rolls produced concrete rewards.
    assert pre["chest"][0]["type"] == "gold"
    assert pre["loose_stone"][0]["type"] == "item"


def test_initial_floor_state_starts_with_pregen_pending():
    state = explore.initial_floor_state(_floor(), random.Random(0))
    assert state["pregen_status"] == "pending"


def test_initial_floor_state_pre_rolls_concealed_and_secret():
    """Pre-roll covers ALL feature visibility tiers; what the player can
    see is gated separately by perception."""
    floor = _floor()
    floor["room_pool"][0]["features"].append({
        "id": "secret_door",
        "name": "secret door",
        "visibility": "secret",
        "revealed_by": "chest",
        "content": [{"type": "gold", "amount": [99, 99], "chance": 1.0}],
    })
    state = explore.initial_floor_state(floor, random.Random(0))
    pre = state["room_states"][state["current"]]["pre_rolled_rewards"]
    assert "secret_door" in pre
    assert pre["secret_door"][0]["amount"] == 99


def test_initial_floor_state_seeded_pre_rolls_are_deterministic():
    s1 = explore.initial_floor_state(_floor(), random.Random(7))
    s2 = explore.initial_floor_state(_floor(), random.Random(7))
    pre1 = s1["room_states"][s1["current"]]["pre_rolled_rewards"]
    pre2 = s2["room_states"][s2["current"]]["pre_rolled_rewards"]
    assert pre1 == pre2


# ---------------------------------------------------------------------------
# take_investigate — reads from cache, doesn't reroll.
# ---------------------------------------------------------------------------


def test_take_investigate_uses_pre_rolled_rewards():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(42))
    # Capture the pre-rolled gold amount.
    pre_gold = state["room_states"][state["current"]]["pre_rolled_rewards"]["chest"][0]["amount"]

    # Investigate with a *different* RNG seed — should still get the
    # pre-rolled amount, not a fresh roll.
    result = explore.take_investigate(
        state, floor, random.Random(99999), feature_id="chest",
    )
    gold_rewards = [r for r in result.rewards if r["type"] == "gold"]
    assert len(gold_rewards) == 1
    assert gold_rewards[0]["amount"] == pre_gold


def test_take_investigate_uses_cached_llm_narration_when_present():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    state["room_states"][state["current"]]["llm_search_outcomes"] = {
        "chest": "_The chest yields its secret quietly._",
    }
    result = explore.take_investigate(
        state, floor, random.Random(0), feature_id="chest",
    )
    assert result.narrative[0] == "_The chest yields its secret quietly._"


def test_take_investigate_falls_back_to_authored_when_no_cache():
    floor = _floor()
    floor["room_pool"][0]["features"][0]["flavor_success"] = "_You open it._"
    state = explore.initial_floor_state(floor, random.Random(0))
    # No llm_search_outcomes set — fallback path.
    result = explore.take_investigate(
        state, floor, random.Random(0), feature_id="chest",
    )
    assert "_You open it._" in result.narrative
    # Authored fallback also enumerates the rewards.
    assert any("g" in line for line in result.narrative)


def test_take_investigate_synthetic_corpse_falls_back_to_runtime_roll():
    """Corpse features are injected at runtime AFTER initial_floor_state
    runs, so they don't have a pre-rolled entry. take_investigate must
    fall back to ``roll_feature_content`` for those."""
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    explore.seed_corpse_in_floor(
        state, random.Random(0),
        loot=[{"type": "gold", "amount": [25, 25]}],
    )
    state["current"] = state["corpse"]["room_node"]
    state["room_states"].setdefault(state["current"], {})["visited"] = True
    result = explore.take_investigate(
        state, floor, random.Random(0), feature_id="your_corpse",
    )
    types = [r["type"] for r in result.rewards]
    assert "gold" in types
    assert "corpse_recovered" in types


# ---------------------------------------------------------------------------
# Batched pre-gen — cogs/dungeon._v2_pregenerate_narration.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pregenerate_narration_skips_when_llm_unavailable(monkeypatch):
    """If the LLM client is unavailable, pre-gen marks the floor "skipped"
    and doesn't call into the LLM at all."""
    from cogs.dungeon import _v2_pregenerate_narration
    from dungeon import llm as dungeon_llm

    monkeypatch.setattr(dungeon_llm, "_get_client", lambda: None)
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    run = MagicMock()
    run.floor = 1
    dungeon_data = {"floors": [floor]}

    await _v2_pregenerate_narration(run, dungeon_data, state)
    assert state["pregen_status"] == "skipped"


@pytest.mark.asyncio
async def test_pregenerate_narration_caches_per_room_and_feature(monkeypatch):
    """When the LLM is available, every room gets an intro + every feature
    with rewards gets a search outcome — cached on the floor state."""
    from cogs.dungeon import _v2_pregenerate_narration
    from dungeon import llm as dungeon_llm

    intro_call_count = [0]
    outcome_call_count = [0]

    async def fake_intro(*args, **kwargs):
        intro_call_count[0] += 1
        return f"intro-{intro_call_count[0]}"

    async def fake_outcome(*args, **kwargs):
        outcome_call_count[0] += 1
        return f"outcome-{outcome_call_count[0]}"

    monkeypatch.setattr(dungeon_llm, "_get_client", lambda: object())
    monkeypatch.setattr(dungeon_llm, "narrate_room_intro", fake_intro)
    monkeypatch.setattr(dungeon_llm, "narrate_search_outcome", fake_outcome)
    monkeypatch.setenv("DUNGEON_LLM_ROOM_INTROS", "1")
    monkeypatch.setenv("DUNGEON_LLM_SEARCH_OUTCOMES", "1")

    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    run = MagicMock()
    run.floor = 1
    dungeon_data = {"floors": [floor]}

    await _v2_pregenerate_narration(run, dungeon_data, state)
    assert state["pregen_status"] == "done"
    # 3 rooms with descriptions → 3 intro calls.
    assert intro_call_count[0] == 3
    # The alcove has 2 features each with content → 2 outcome calls.
    # The other rooms have no features.
    assert outcome_call_count[0] == 2
    # Cached on the room state.
    assert state["room_states"]["r0"]["llm_intro"] is not None
    assert "chest" in state["room_states"]["r0"]["llm_search_outcomes"]
    assert "loose_stone" in state["room_states"]["r0"]["llm_search_outcomes"]


@pytest.mark.asyncio
async def test_pregenerate_narration_idempotent(monkeypatch):
    """Re-running pre-gen on the same state is a no-op — pregen_status is
    already 'done' so we don't re-fire LLM calls."""
    from cogs.dungeon import _v2_pregenerate_narration
    from dungeon import llm as dungeon_llm

    call_count = [0]

    async def fake(*args, **kwargs):
        call_count[0] += 1
        return "stuff"

    monkeypatch.setattr(dungeon_llm, "_get_client", lambda: object())
    monkeypatch.setattr(dungeon_llm, "narrate_room_intro", fake)
    monkeypatch.setattr(dungeon_llm, "narrate_search_outcome", fake)
    monkeypatch.setenv("DUNGEON_LLM_ROOM_INTROS", "1")
    monkeypatch.setenv("DUNGEON_LLM_SEARCH_OUTCOMES", "1")

    state = explore.initial_floor_state(_floor(), random.Random(0))
    run = MagicMock()
    run.floor = 1
    dungeon_data = {"floors": [_floor()]}
    await _v2_pregenerate_narration(run, dungeon_data, state)
    first = call_count[0]
    await _v2_pregenerate_narration(run, dungeon_data, state)
    assert call_count[0] == first  # no extra calls


@pytest.mark.asyncio
async def test_pregenerate_narration_room_intros_disabled(monkeypatch):
    """DUNGEON_LLM_ROOM_INTROS=0 skips intros but still does outcomes."""
    from cogs.dungeon import _v2_pregenerate_narration
    from dungeon import llm as dungeon_llm

    intro_calls = [0]
    outcome_calls = [0]

    async def fake_intro(*args, **kwargs):
        intro_calls[0] += 1
        return "x"

    async def fake_outcome(*args, **kwargs):
        outcome_calls[0] += 1
        return "y"

    monkeypatch.setattr(dungeon_llm, "_get_client", lambda: object())
    monkeypatch.setattr(dungeon_llm, "narrate_room_intro", fake_intro)
    monkeypatch.setattr(dungeon_llm, "narrate_search_outcome", fake_outcome)
    monkeypatch.setenv("DUNGEON_LLM_ROOM_INTROS", "0")
    monkeypatch.setenv("DUNGEON_LLM_SEARCH_OUTCOMES", "1")

    state = explore.initial_floor_state(_floor(), random.Random(0))
    run = MagicMock()
    run.floor = 1
    dungeon_data = {"floors": [_floor()]}
    await _v2_pregenerate_narration(run, dungeon_data, state)
    assert intro_calls[0] == 0
    assert outcome_calls[0] == 2


@pytest.mark.asyncio
async def test_pregenerate_narration_handles_individual_failures(monkeypatch):
    """If one LLM call fails, the others still cache. The failed feature
    falls back to authored at click time."""
    from cogs.dungeon import _v2_pregenerate_narration
    from dungeon import llm as dungeon_llm

    async def flaky_intro(*args, **kwargs):
        raise RuntimeError("api down")

    async def good_outcome(*args, **kwargs):
        return "outcome-text"

    monkeypatch.setattr(dungeon_llm, "_get_client", lambda: object())
    monkeypatch.setattr(dungeon_llm, "narrate_room_intro", flaky_intro)
    monkeypatch.setattr(dungeon_llm, "narrate_search_outcome", good_outcome)
    monkeypatch.setenv("DUNGEON_LLM_ROOM_INTROS", "1")
    monkeypatch.setenv("DUNGEON_LLM_SEARCH_OUTCOMES", "1")

    state = explore.initial_floor_state(_floor(), random.Random(0))
    run = MagicMock()
    run.floor = 1
    dungeon_data = {"floors": [_floor()]}
    await _v2_pregenerate_narration(run, dungeon_data, state)
    # Pre-gen still completes.
    assert state["pregen_status"] == "done"
    # No intros cached (all failed).
    assert state["room_states"]["r0"].get("llm_intro") is None
    # Outcomes did cache.
    assert state["room_states"]["r0"]["llm_search_outcomes"]["chest"] == "outcome-text"
