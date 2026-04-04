"""Utility functions to run and resolve derby races."""

from __future__ import annotations

import glob
import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set, Tuple

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from economy.models import Wallet
from . import models

TEMPERAMENTS = {
    "Agile": {"up": "speed", "down": "stamina"},
    "Reckless": {"up": "speed", "down": "cornering"},
    "Tactical": {"up": "cornering", "down": "speed"},
    "Burly": {"up": "stamina", "down": "cornering"},
    "Steady": {"up": "stamina", "down": "speed"},
    "Sharpshift": {"up": "cornering", "down": "stamina"},
    "Quirky": {"up": None, "down": None},
}

TEMPERAMENT_MODIFIER = 0.1

MOOD_LABELS = {
    1: "Awful",
    2: "Bad",
    3: "Normal",
    4: "Good",
    5: "Great",
}


# ---------------------------------------------------------------------------
# Map data structures and loading
# ---------------------------------------------------------------------------

SEGMENT_TYPES = {
    "straight": {"speed": 1.0, "cornering": 0.3, "stamina": 0.5},
    "corner": {"speed": 0.3, "cornering": 1.0, "stamina": 0.5},
    "climb": {"speed": 0.5, "cornering": 0.3, "stamina": 1.0},
    "descent": {"speed": 0.8, "cornering": 0.7, "stamina": 0.3},
    "hazard": {"speed": 0.4, "cornering": 0.6, "stamina": 0.8},
}


@dataclass
class MapSegment:
    type: str
    distance: int = 2
    description: str = ""


@dataclass
class RaceMap:
    name: str
    theme: str
    description: str
    segments: list[MapSegment] = field(default_factory=list)


@dataclass
class SegmentResult:
    """Results for a single segment of a race."""

    position: int  # 1-based segment number
    segment_type: str
    segment_description: str
    standings: list[tuple[int, float, float]]  # (racer_id, seg_score, cumulative)
    events: list[str]  # auto-detected notable moments


@dataclass
class RaceResult:
    """Full results of a simulated race."""

    placements: list[int]  # racer IDs, winner first
    segments: list[SegmentResult]
    racer_names: dict[int, str]
    map_name: str = ""


_MAPS_DIR = os.path.join(os.path.dirname(__file__), "maps")


def load_map(path: str) -> RaceMap:
    """Load a single map YAML file and return a RaceMap."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    segments = [
        MapSegment(
            type=s["type"],
            distance=s.get("distance", 2),
            description=s.get("description", ""),
        )
        for s in data.get("segments", [])
    ]
    return RaceMap(
        name=data["name"],
        theme=data.get("theme", "standard"),
        description=data.get("description", ""),
        segments=segments,
    )


def load_all_maps() -> list[RaceMap]:
    """Load all .yaml map files from the maps directory."""
    maps: list[RaceMap] = []
    for path in sorted(glob.glob(os.path.join(_MAPS_DIR, "*.yaml"))):
        maps.append(load_map(path))
    return maps


def pick_map() -> RaceMap | None:
    """Pick a random map from the available maps."""
    maps = load_all_maps()
    if not maps:
        return None
    return random.choice(maps)


# ---------------------------------------------------------------------------
# Name pool
# ---------------------------------------------------------------------------

_NAMES_FILE = os.path.join(os.path.dirname(__file__), "names.txt")


def _load_names() -> list[str]:
    with open(_NAMES_FILE, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def pick_name(taken: Set[str]) -> str | None:
    """Pick a random name from the pool that isn't already taken."""
    taken_lower = {n.lower() for n in taken}
    available = [n for n in _load_names() if n.lower() not in taken_lower]
    if not available:
        return None
    return random.choice(available)


def stat_band(value: int) -> str:
    """Return a human-readable quality label for a stat value (0-31)."""
    if value <= 15:
        return "Decent"
    if value <= 25:
        return "Good"
    if value <= 29:
        return "Very Good"
    if value == 30:
        return "Fantastic"
    return "Perfect"


def mood_label(value: int) -> str:
    """Return a human-readable label for a mood value (1-5)."""
    return MOOD_LABELS.get(value, str(value))


def apply_temperament(
    stats: Dict[str, int], temperament: str, modifier: float = TEMPERAMENT_MODIFIER
) -> Dict[str, int]:
    """Return ``stats`` adjusted for ``temperament``.

    ``modifier`` is the percentage bonus or penalty applied to the affected
    statistics. Unknown temperaments return stats unchanged.
    """

    result = stats.copy()
    t = TEMPERAMENTS.get(temperament)
    if not t:
        return result

    up = t.get("up")
    down = t.get("down")

    if up is not None:
        result[up] = int(round(result[up] * (1 + modifier)))
    if down is not None:
        result[down] = int(round(result[down] * (1 - modifier)))
    return result


def _racer_power(racer: models.Racer) -> float:
    """Return the effective power score for a racer after temperament."""
    stats = apply_temperament(
        {"speed": racer.speed, "cornering": racer.cornering, "stamina": racer.stamina},
        racer.temperament,
    )
    return float(stats["speed"] + stats["cornering"] + stats["stamina"])


def _segment_score(
    racer_stats: Dict[str, int],
    segment_type: str,
    distance: int,
    segment_index: int,
    rng: random.Random,
) -> tuple[float, float]:
    """Calculate a racer's score for a single segment.

    Returns ``(score, noise)`` where noise is the raw random component
    (used for event detection).
    """
    weights = SEGMENT_TYPES.get(segment_type, SEGMENT_TYPES["straight"])

    raw = (
        racer_stats["speed"] * weights["speed"]
        + racer_stats["cornering"] * weights["cornering"]
        + racer_stats["stamina"] * weights["stamina"]
    )

    distance_factor = 0.8 + (distance * 0.2)
    fatigue = max(0, segment_index * 1.5 - racer_stats["stamina"] * 0.15)
    noise = rng.uniform(0, 15)

    return (raw * distance_factor) - fatigue + noise, noise


def _detect_events(
    prev_order: List[int],
    curr_order: List[int],
    names: Dict[int, str],
    scores: Dict[int, float],
    noise_rolls: Dict[int, float],
) -> List[str]:
    """Detect notable events from a segment's results."""
    events: List[str] = []
    prev_pos = {rid: i for i, rid in enumerate(prev_order)}

    for i, rid in enumerate(curr_order):
        old = prev_pos.get(rid, i)
        if old - i >= 2:
            events.append(
                f"{names.get(rid, f'Racer {rid}')} overtakes {old - i} racers!"
            )

    if len(curr_order) >= 2:
        first = scores[curr_order[0]]
        second = scores[curr_order[1]]
        gap = first - second
        if gap < 3:
            events.append(
                f"Close battle between {names.get(curr_order[0], '???')} "
                f"and {names.get(curr_order[1], '???')}!"
            )
        elif gap > 15:
            events.append(
                f"{names.get(curr_order[0], '???')} pulls away with a commanding lead!"
            )

    for rid, noise in noise_rolls.items():
        if noise < 3:
            events.append(f"{names.get(rid, f'Racer {rid}')} stumbles!")
        elif noise > 12:
            events.append(f"{names.get(rid, f'Racer {rid}')} surges forward!")

    return events


def _map_weighted_power(
    racer: models.Racer, race_map: RaceMap
) -> float:
    """Return the expected power score for a racer on a specific map."""
    stats = apply_temperament(
        {"speed": racer.speed, "cornering": racer.cornering, "stamina": racer.stamina},
        racer.temperament,
    )
    total = 0.0
    for seg in race_map.segments:
        weights = SEGMENT_TYPES.get(seg.type, SEGMENT_TYPES["straight"])
        total += (
            stats["speed"] * weights["speed"]
            + stats["cornering"] * weights["cornering"]
            + stats["stamina"] * weights["stamina"]
        )
    return total / len(race_map.segments) if race_map.segments else _racer_power(racer)


def calculate_odds(
    racers: Sequence[models.Racer] | Sequence[int],
    course_segments: Sequence | None,
    house_edge: float,
    race_map: RaceMap | None = None,
) -> Dict[int, float]:
    """Return a payout multiplier for each racer.

    When ``race_map`` is provided, odds are weighted by map-specific power.
    Otherwise falls back to flat power score.
    """
    if not racers:
        return {}

    if not hasattr(racers[0], "speed"):
        num = len(racers)
        base_prob = 1.0 / num
        payout = (1.0 - house_edge) / base_prob
        return {(r.id if hasattr(r, "id") else int(r)): payout for r in racers}

    NOISE_BASELINE = 7.5  # average of uniform(0, 15) per segment
    weights: List[float] = []
    for racer in racers:
        if race_map and race_map.segments:
            power = _map_weighted_power(racer, race_map)
        else:
            power = _racer_power(racer)
        weights.append(power + NOISE_BASELINE)

    total_weight = sum(weights)
    result: Dict[int, float] = {}
    for racer, weight in zip(racers, weights):
        prob = weight / total_weight
        result[racer.id] = round((1.0 - house_edge) / prob, 2)
    return result


def simulate_race(
    race: models.Race | Dict[str, list],
    seed: int,
    race_map: RaceMap | None = None,
) -> RaceResult:
    """Simulate a race and return a RaceResult.

    When ``race_map`` is provided, runs a segment-by-segment simulation where
    each segment type favors different stats. Without a map, falls back to a
    single-pass power + noise calculation.
    """
    rng = random.Random(seed)

    if isinstance(race, dict):
        raw_racers = race.get("racers", [])
    else:
        raw_racers = getattr(race, "racers", [])

    has_stats = raw_racers and hasattr(raw_racers[0], "speed")

    names: Dict[int, str] = {}
    if has_stats:
        names = {r.id: r.name for r in raw_racers}

    map_name = race_map.name if race_map else ""

    # --- Segment-by-segment simulation ---
    if has_stats and race_map and race_map.segments:
        racer_stats: Dict[int, Dict[str, int]] = {}
        for r in raw_racers:
            racer_stats[r.id] = apply_temperament(
                {"speed": r.speed, "cornering": r.cornering, "stamina": r.stamina},
                r.temperament,
            )

        cumulative: Dict[int, float] = {r.id: 0.0 for r in raw_racers}
        prev_order = [r.id for r in raw_racers]
        segment_results: List[SegmentResult] = []

        for seg_idx, seg in enumerate(race_map.segments):
            seg_scores: Dict[int, float] = {}
            noise_rolls: Dict[int, float] = {}
            for rid, stats in racer_stats.items():
                score, noise = _segment_score(
                    stats, seg.type, seg.distance, seg_idx, rng
                )
                seg_scores[rid] = score
                noise_rolls[rid] = noise
                cumulative[rid] += score

            curr_order = sorted(
                cumulative.keys(), key=lambda rid: cumulative[rid], reverse=True
            )
            standings = [
                (rid, seg_scores[rid], cumulative[rid]) for rid in curr_order
            ]
            events = _detect_events(
                prev_order, curr_order, names, cumulative, noise_rolls
            )
            segment_results.append(
                SegmentResult(
                    position=seg_idx + 1,
                    segment_type=seg.type,
                    segment_description=seg.description,
                    standings=standings,
                    events=events,
                )
            )
            prev_order = curr_order

        placements = [rid for rid, _, _ in segment_results[-1].standings]
        return RaceResult(
            placements=placements,
            segments=segment_results,
            racer_names=names,
            map_name=map_name,
        )

    # --- Legacy single-pass fallback ---
    if has_stats:
        scored: List[Tuple[int, float]] = []
        for racer in raw_racers:
            power = _racer_power(racer)
            score = power + rng.uniform(0, 40)
            scored.append((racer.id, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        placements = [rid for rid, _ in scored]
    else:
        placements = [
            r.id if hasattr(r, "id") else int(r) for r in raw_racers
        ]
        rng.shuffle(placements)

    return RaceResult(
        placements=placements,
        segments=[],
        racer_names=names,
        map_name=map_name,
    )


async def resolve_payouts(
    session: AsyncSession, race_id: int, winner_id: int
) -> None:
    """Resolve all bets for ``race_id`` and update wallets.

    Winning bets pay ``amount * payout_multiplier`` (the multiplier stored
    at bet time based on the racer's odds). All processed bets are removed
    from the database.
    """

    bet_rows = await session.execute(
        select(models.Bet).where(models.Bet.race_id == race_id)
    )
    bets = bet_rows.scalars().all()

    if not bets:
        return

    for bet in bets:
        wallet = await session.get(Wallet, bet.user_id)
        if wallet is None:
            wallet = Wallet(user_id=bet.user_id, balance=0)
            session.add(wallet)
            await session.commit()
            await session.refresh(wallet)

        if bet.racer_id == winner_id:
            payout = int(bet.amount * bet.payout_multiplier)
            wallet.balance += payout
        await session.delete(bet)

    await session.commit()
