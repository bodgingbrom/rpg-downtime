"""Utility functions to run and resolve derby races."""

from __future__ import annotations

import random
from typing import Dict, List, Sequence, Tuple

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


def calculate_odds(
    racers: Sequence[models.Racer] | Sequence[int],
    course_segments: Sequence[models.CourseSegment] | None,
    house_edge: float,
) -> Dict[int, float]:
    """Return a payout multiplier for each racer.

    The odds are currently calculated assuming every racer has an equal chance of
    winning. The payout multiplier is adjusted by ``house_edge``.
    """
    if not racers:
        return {}

    num = len(racers)
    base_prob = 1.0 / num
    payout = (1.0 - house_edge) / base_prob

    result: Dict[int, float] = {}
    for racer in racers:
        racer_id = racer.id if hasattr(racer, "id") else int(racer)
        result[racer_id] = payout
    return result


def simulate_race(
    race: models.Race | Dict[str, list], seed: int
) -> Tuple[List[int], List[str]]:
    """Simulate a race and return placements and an event log.

    ``race`` must expose a list of racers under the ``racers`` attribute or key
    and may optionally expose ``course_segments``.
    """
    rng = random.Random(seed)

    racers: List[int] = []
    if isinstance(race, dict):
        racers = [r.id if hasattr(r, "id") else int(r) for r in race.get("racers", [])]
        segments = race.get("course_segments", [])
    else:
        racers = [
            r.id if hasattr(r, "id") else int(r) for r in getattr(race, "racers", [])
        ]
        segments = getattr(race, "course_segments", [])

    placements = list(racers)
    rng.shuffle(placements)

    event_log = []
    for idx, _ in enumerate(segments, start=1):
        leader = rng.choice(placements)
        event_log.append(f"Segment {idx}: Racer {leader} takes the lead")

    return placements, event_log


async def resolve_payouts(session: AsyncSession, race_id: int) -> None:
    """Resolve all bets for ``race_id`` and update wallets.

    The current implementation selects all bets associated with the race and pays
    out double the bet amount to bets placed on the winning racer. The winning
    racer is determined by the lowest racer id among the bets. All processed bets
    are removed from the database.
    """

    bet_rows = await session.execute(
        select(models.Bet).where(models.Bet.race_id == race_id)
    )
    bets = bet_rows.scalars().all()

    if not bets:
        return

    winning_racer = min(bet.racer_id for bet in bets)

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
