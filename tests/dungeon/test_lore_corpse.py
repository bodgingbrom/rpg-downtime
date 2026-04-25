"""Tests for PR 5 — lore fragment awarding, legendary unlock check,
corpse persistence and recovery, ``/dungeon lore`` book rendering, and
schema validation for the new content types and dungeon-level fields.

Repository tests use an in-memory SQLite DB so we exercise the actual
SQLAlchemy models. Pure-logic tests stay synchronous.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Ensure all relevant models are registered.
import dungeon.models  # noqa: F401
import economy.models  # noqa: F401
import rpg.models      # noqa: F401
from db_base import Base
from dungeon import explore, repositories as dungeon_repo


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


def _floor_with_lore_features():
    """Floor whose features include a lore_fragment content entry."""
    return {
        "layout": {"rooms_per_run": [3, 3]},
        "anchors": [
            {"position": "entrance", "room_id": "alcove"},
            {"position": "boss", "room_id": "endroom"},
        ],
        "wandering_threshold": 99,
        "wandering_pool": [],
        "monsters": [],
        "boss": {
            "id": "boss", "hp": 5, "defense": 0,
            "attack_dice": "1d4", "attack_bonus": 0, "xp": 1, "gold": [1, 1],
            "ai": {"attack": 100},
        },
        "room_pool": [
            {
                "id": "alcove",
                "weight": 100,
                "description_pool": ["alcove."],
                "features": [
                    {
                        "id": "wall",
                        "name": "wall",
                        "visibility": "visible",
                        "investigate_label": "Read the wall",
                        "noise": 1,
                        "content": [
                            {"type": "lore_fragment", "fragment_id": 1, "chance": 1.0},
                        ],
                    },
                ],
            },
            {"id": "filler", "weight": 100, "description_pool": ["filler."]},
            {"id": "endroom", "weight": 100, "description_pool": ["endroom."]},
        ],
    }


def _dungeon_data(fragments=None, legendary=None):
    return {
        "id": "test_dun",
        "name": "Test",
        "lore_fragments": fragments or [
            {"id": 1, "text": "First fragment text."},
            {"id": 2, "text": "Second fragment text."},
            {"id": 3, "text": "Third fragment text."},
        ],
        "legendary_reward": legendary or {
            "item_id": "vorpal_dagger",
            "name": "Apprentice's Pen",
        },
        "floors": [_floor_with_lore_features()],
    }


# ---------------------------------------------------------------------------
# Schema validation — top-level lore + legendary.
# ---------------------------------------------------------------------------


def test_validate_dungeon_meta_accepts_clean():
    errs = explore.validate_dungeon_meta(_dungeon_data())
    assert errs == []


def test_validate_dungeon_meta_rejects_duplicate_fragment_ids():
    bad = _dungeon_data(fragments=[
        {"id": 1, "text": "a"},
        {"id": 1, "text": "b"},
    ])
    errs = explore.validate_dungeon_meta(bad)
    assert errs and any("duplicated" in e for e in errs)


def test_validate_dungeon_meta_rejects_non_int_fragment_id():
    bad = _dungeon_data(fragments=[{"id": "one", "text": "a"}])
    errs = explore.validate_dungeon_meta(bad)
    assert errs and any("id must be an int" in e for e in errs)


def test_validate_dungeon_meta_rejects_empty_text():
    bad = _dungeon_data(fragments=[{"id": 1, "text": ""}])
    errs = explore.validate_dungeon_meta(bad)
    assert errs and any("text" in e for e in errs)


def test_validate_dungeon_meta_rejects_legendary_without_item_id():
    bad = _dungeon_data(legendary={"name": "Sword of Whatever"})
    errs = explore.validate_dungeon_meta(bad)
    assert errs and any("item_id" in e for e in errs)


def test_validate_dungeon_meta_rejects_lore_fragment_content_pointing_at_unknown_id():
    """A feature's content references fragment id 99, but only 1-3 are authored."""
    bad = _dungeon_data()
    bad["floors"][0]["room_pool"][0]["features"][0]["content"] = [
        {"type": "lore_fragment", "fragment_id": 99, "chance": 1.0},
    ]
    errs = explore.validate_dungeon_meta(bad)
    assert errs and any("99 not in top-level" in e for e in errs)


def test_validate_dungeon_meta_no_lore_fragments_is_ok():
    """Lore is optional — dungeons without it still validate."""
    plain = {"id": "x", "name": "x", "floors": []}
    assert explore.validate_dungeon_meta(plain) == []


# ---------------------------------------------------------------------------
# Feature-level validation — lore_fragment content type.
# ---------------------------------------------------------------------------


def test_validate_room_lore_fragment_requires_int_fragment_id():
    bad = {"features": [{
        "id": "x", "visibility": "visible",
        "content": [{"type": "lore_fragment", "fragment_id": "one"}],
    }]}
    errs = explore.validate_room(bad)
    assert errs and any("fragment_id" in e for e in errs)


def test_validate_room_lore_fragment_accepts_int():
    ok = {"features": [{
        "id": "x", "visibility": "visible",
        "content": [{"type": "lore_fragment", "fragment_id": 1}],
    }]}
    assert explore.validate_room(ok) == []


# ---------------------------------------------------------------------------
# take_investigate — lore reward emission.
# ---------------------------------------------------------------------------


def test_take_investigate_emits_lore_fragment_reward():
    floor = _floor_with_lore_features()
    state = explore.initial_floor_state(floor, random.Random(0))
    result = explore.take_investigate(
        state, floor, random.Random(0), feature_id="wall",
    )
    lore_rewards = [r for r in result.rewards if r["type"] == "lore_fragment"]
    assert len(lore_rewards) == 1
    assert lore_rewards[0]["fragment_id"] == 1


# ---------------------------------------------------------------------------
# Corpse seeding + feature injection.
# ---------------------------------------------------------------------------


def test_seed_corpse_in_floor_picks_non_boss_room():
    floor = _floor_with_lore_features()
    state = explore.initial_floor_state(floor, random.Random(0))
    chosen = explore.seed_corpse_in_floor(
        state, random.Random(0), loot=[{"type": "gold", "amount": [10, 10]}],
    )
    assert chosen is not None
    assert chosen != state["graph"]["boss"]
    assert state["corpse"]["room_node"] == chosen
    assert state["corpse"]["loot"] == [{"type": "gold", "amount": [10, 10]}]
    assert state["corpse"]["recovered"] is False


def test_corpse_feature_injected_only_in_chosen_room():
    floor = _floor_with_lore_features()
    state = explore.initial_floor_state(floor, random.Random(0))
    explore.seed_corpse_in_floor(
        state, random.Random(0), loot=[{"type": "gold", "amount": [5, 5]}],
    )
    target = state["corpse"]["room_node"]

    # Move the player to the target room.
    state["current"] = target
    state["room_states"].setdefault(target, {})["visited"] = True
    feats = explore._features_in_room(state, floor)
    assert any(f.get("id") == "your_corpse" for f in feats)

    # And NOT in some other room.
    other = next(n for n in state["graph"]["rooms"] if n != target)
    state["current"] = other
    state["room_states"].setdefault(other, {})["visited"] = True
    feats = explore._features_in_room(state, floor)
    assert all(f.get("id") != "your_corpse" for f in feats)


def test_corpse_feature_not_injected_after_recovery():
    floor = _floor_with_lore_features()
    state = explore.initial_floor_state(floor, random.Random(0))
    explore.seed_corpse_in_floor(
        state, random.Random(0), loot=[{"type": "gold", "amount": [5, 5]}],
    )
    target = state["corpse"]["room_node"]
    state["current"] = target
    state["room_states"].setdefault(target, {})["visited"] = True
    state["corpse"]["recovered"] = True
    feats = explore._features_in_room(state, floor)
    assert all(f.get("id") != "your_corpse" for f in feats)


def test_take_investigate_corpse_returns_recovery_signal():
    floor = _floor_with_lore_features()
    state = explore.initial_floor_state(floor, random.Random(0))
    explore.seed_corpse_in_floor(
        state, random.Random(0),
        loot=[{"type": "gold", "amount": [5, 5]}, {"type": "item", "item_id": "potion"}],
    )
    state["current"] = state["corpse"]["room_node"]
    rs = state["room_states"].setdefault(state["current"], {})
    rs["visited"] = True

    result = explore.take_investigate(
        state, floor, random.Random(0), feature_id="your_corpse",
    )
    types = [r["type"] for r in result.rewards]
    assert "gold" in types
    assert "item" in types
    assert "corpse_recovered" in types


def test_seed_corpse_returns_none_for_empty_graph():
    state = {"graph": {"rooms": {}, "boss": None}, "room_states": {}}
    assert explore.seed_corpse_in_floor(state, random.Random(0), loot=[]) is None


# ---------------------------------------------------------------------------
# Repository round-trips (lore + legendary + corpse).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_lore_fragment_dedupes(session):
    now = datetime.now(timezone.utc)
    added = await dungeon_repo.add_lore_fragment(session, 1, 1, "test_dun", 1, now)
    assert added is True
    again = await dungeon_repo.add_lore_fragment(session, 1, 1, "test_dun", 1, now)
    assert again is False
    await session.commit()
    rows = await dungeon_repo.get_lore_fragments(session, 1, 1, "test_dun")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_get_lore_fragments_scopes_per_dungeon(session):
    now = datetime.now(timezone.utc)
    await dungeon_repo.add_lore_fragment(session, 1, 1, "dun_a", 1, now)
    await dungeon_repo.add_lore_fragment(session, 1, 1, "dun_a", 2, now)
    await dungeon_repo.add_lore_fragment(session, 1, 1, "dun_b", 1, now)
    await session.commit()
    a = await dungeon_repo.get_lore_fragments(session, 1, 1, "dun_a")
    b = await dungeon_repo.get_lore_fragments(session, 1, 1, "dun_b")
    assert sorted(r.fragment_id for r in a) == [1, 2]
    assert [r.fragment_id for r in b] == [1]


@pytest.mark.asyncio
async def test_record_legendary_unlock_idempotent(session):
    now = datetime.now(timezone.utc)
    grant = await dungeon_repo.record_legendary_unlock(
        session, 1, 1, "test_dun", "vorpal_dagger", now,
    )
    assert grant is not None
    again = await dungeon_repo.record_legendary_unlock(
        session, 1, 1, "test_dun", "vorpal_dagger", now,
    )
    assert again is None
    await session.commit()
    row = await dungeon_repo.get_legendary_unlock(session, 1, 1, "test_dun")
    assert row is not None and row.item_id == "vorpal_dagger"


@pytest.mark.asyncio
async def test_corpse_upsert_overwrites(session):
    now = datetime.now(timezone.utc)
    first = await dungeon_repo.upsert_corpse(
        session, 1, 1, "dun_a", floor=2,
        loot_json='[{"type":"gold","amount":[5,5]}]',
        died_at=now,
    )
    assert first.floor == 2
    # Second death overwrites.
    second = await dungeon_repo.upsert_corpse(
        session, 1, 1, "dun_a", floor=3,
        loot_json='[]',
        died_at=now,
    )
    assert second.id == first.id
    assert second.floor == 3
    await session.commit()


@pytest.mark.asyncio
async def test_delete_corpse_removes_row(session):
    now = datetime.now(timezone.utc)
    await dungeon_repo.upsert_corpse(
        session, 1, 1, "dun_a", floor=1, loot_json="[]", died_at=now,
    )
    await session.commit()
    assert await dungeon_repo.get_corpse(session, 1, 1, "dun_a") is not None

    deleted = await dungeon_repo.delete_corpse(session, 1, 1, "dun_a")
    assert deleted is True
    await session.commit()
    assert await dungeon_repo.get_corpse(session, 1, 1, "dun_a") is None


@pytest.mark.asyncio
async def test_delete_corpse_returns_false_when_absent(session):
    deleted = await dungeon_repo.delete_corpse(session, 1, 1, "missing")
    assert deleted is False


# ---------------------------------------------------------------------------
# /dungeon lore book embed rendering.
# ---------------------------------------------------------------------------


def test_lore_embed_shows_collected_text_and_unread_gaps():
    from cogs.dungeon import _build_lore_embed

    embed = _build_lore_embed(
        _dungeon_data(),
        owned_fragment_ids={1, 3},
        unlock_item_id=None,
    )
    desc = embed.description or ""
    assert "First fragment text." in desc
    assert "Third fragment text." in desc
    # Fragment 2 is unread.
    assert "[2]" in desc
    assert "unread" in desc
    # Counter shows 2/3.
    assert "2 / 3" in desc


def test_lore_embed_announces_legendary_when_complete():
    from cogs.dungeon import _build_lore_embed

    embed = _build_lore_embed(
        _dungeon_data(),
        owned_fragment_ids={1, 2, 3},
        unlock_item_id="vorpal_dagger",
    )
    assert "Apprentice's Pen" in (embed.description or "")
    assert "complete" in (embed.description or "").lower()


def test_lore_embed_handles_dungeon_with_no_fragments():
    from cogs.dungeon import _build_lore_embed

    embed = _build_lore_embed(
        {"name": "Bare", "lore_fragments": []},
        owned_fragment_ids=set(),
        unlock_item_id=None,
    )
    assert "0 / 0" in (embed.description or "")


# ---------------------------------------------------------------------------
# Smoke test: dev_v2_skeleton exercises everything.
# ---------------------------------------------------------------------------


def test_dev_v2_skeleton_has_lore_and_legendary():
    from dungeon import logic
    logic._dungeons_cache = None
    dungeons = logic.load_dungeons()
    skel = dungeons["dev_v2_skeleton"]
    assert len(skel["lore_fragments"]) == 3
    assert skel["legendary_reward"]["item_id"] == "vorpal_dagger"
    # Each fragment is referenced by some feature in some room.
    referenced: set[int] = set()
    for floor in skel["floors"]:
        for room in floor.get("room_pool", []):
            for feat in room.get("features", []):
                for c in feat.get("content", []):
                    if c.get("type") == "lore_fragment":
                        referenced.add(c["fragment_id"])
    assert referenced == {1, 2, 3}
