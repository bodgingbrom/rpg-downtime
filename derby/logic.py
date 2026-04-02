"""Utility functions to run and resolve derby races."""

from __future__ import annotations

import os
import random
from typing import Dict, List, Sequence, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


def calculate_odds(
    racers: Sequence[models.Racer] | Sequence[int],
    course_segments: Sequence[models.CourseSegment] | None,
    house_edge: float,
) -> Dict[int, float]:
    """Return a payout multiplier for each racer.

    Odds are weighted by each racer's power score (stats after temperament).
    Stronger racers get lower payouts; weaker racers get higher payouts.
    Falls back to equal odds for bare-int racer lists.
    """
    if not racers:
        return {}

    # Fall back to equal odds for bare ints (no stat attributes)
    if not hasattr(racers[0], "speed"):
        num = len(racers)
        base_prob = 1.0 / num
        payout = (1.0 - house_edge) / base_prob
        return {(r.id if hasattr(r, "id") else int(r)): payout for r in racers}

    # Stat-weighted odds: power + baseline noise expectation
    NOISE_BASELINE = 20.0  # average of uniform(0, 40)
    weights: List[float] = []
    for racer in racers:
        weights.append(_racer_power(racer) + NOISE_BASELINE)

    total_weight = sum(weights)
    result: Dict[int, float] = {}
    for racer, weight in zip(racers, weights):
        prob = weight / total_weight
        result[racer.id] = round((1.0 - house_edge) / prob, 2)
    return result


def simulate_race(
    race: models.Race | Dict[str, list], seed: int
) -> Tuple[List[int], List[str]]:
    """Simulate a race and return placements and an event log.

    ``race`` must expose a list of racers under the ``racers`` attribute or key
    and may optionally expose ``course_segments``.

    When racers have stat attributes (speed, cornering, stamina), placements are
    determined by a weighted score: power (stats after temperament) plus random
    noise.  For bare-int racer lists, falls back to a random shuffle.
    """
    rng = random.Random(seed)

    if isinstance(race, dict):
        raw_racers = race.get("racers", [])
        segments = race.get("course_segments", [])
    else:
        raw_racers = getattr(race, "racers", [])
        segments = getattr(race, "course_segments", [])

    has_stats = raw_racers and hasattr(raw_racers[0], "speed")

    if has_stats:
        # Stat-weighted placement: power + noise
        scored: List[Tuple[int, float]] = []
        for racer in raw_racers:
            power = _racer_power(racer)
            score = power + rng.uniform(0, 40)
            scored.append((racer.id, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        placements = [rid for rid, _ in scored]
    else:
        # Bare-int fallback: pure random shuffle
        placements = [
            r.id if hasattr(r, "id") else int(r) for r in raw_racers
        ]
        rng.shuffle(placements)

    # Build name lookup from racer objects when available
    names: Dict[int, str] = {}
    if has_stats:
        names = {r.id: r.name for r in raw_racers}

    event_log: List[str] = []
    for idx, _ in enumerate(segments, start=1):
        leader = rng.choice(placements)
        leader_name = names.get(leader, f"Racer {leader}")
        event_log.append(f"Segment {idx}: {leader_name} takes the lead")

    return placements, event_log


async def resolve_payouts(
    session: AsyncSession, race_id: int, winner_id: int
) -> None:
    """Resolve all bets for ``race_id`` and update wallets.

    Pays out double the bet amount to bets placed on ``winner_id``. All
    processed bets are removed from the database.
    """

    bet_rows = await session.execute(
        select(models.Bet).where(models.Bet.race_id == race_id)
    )
    bets = bet_rows.scalars().all()

    if not bets:
        return

    winning_racer = winner_id

    for bet in bets:
        wallet = await session.get(models.Wallet, bet.user_id)
        if wallet is None:
            wallet = models.Wallet(user_id=bet.user_id, balance=0)
            session.add(wallet)
            await session.commit()
            await session.refresh(wallet)

        if bet.racer_id == winning_racer:
            wallet.balance += bet.amount * 2
        await session.delete(bet)

    await session.commit()
