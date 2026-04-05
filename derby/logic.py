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

# D20 mood thresholds: (bonus_threshold, penalty_max)
# Roll >= bonus_threshold → +MOOD_BONUS; roll <= penalty_max → -MOOD_BONUS
MOOD_THRESHOLDS = {
    5: (17, 1),   # Great: 20% bonus, 5% penalty
    4: (18, 2),   # Good:  15% bonus, 10% penalty
    3: (19, 2),   # Normal: 10% bonus, 10% penalty
    2: (20, 4),   # Bad:   5% bonus, 20% penalty
    1: (21, 4),   # Awful: 0% bonus (impossible on d20), 20% penalty
}

MOOD_BONUS = 5.0  # flat points added/subtracted per mood event

# Multiplicative noise range for segment scoring.
NOISE_MULT_LOW = 0.55
NOISE_MULT_HIGH = 1.45

# Additive noise floor — represents inherent chaos of racing (track
# conditions, crowds, random stumbles).  Independent of stats so weaker
# racers get a baseline randomness that can close gaps.
NOISE_FLOOR = 40.0

# Race Day Form table: mood -> (low_offset, high_offset)
# Applied as segment_score * (1.0 + form_offset) for ALL segments in a race
FORM_TABLE: dict[int, tuple[float, float]] = {
    1: (-0.35, 0.10),   # Awful: mostly bad days
    2: (-0.25, 0.15),   # Bad: downside bias
    3: (-0.20, 0.25),   # Normal: slight upside bias
    4: (-0.10, 0.35),   # Good: mostly good days
    5: (-0.05, 0.45),   # Great: almost always a boost
}


def roll_mood_bonus(mood: int, rng: random.Random) -> tuple[int, float]:
    """Roll a d20 for a mood event and return ``(roll, bonus)``.

    ``bonus`` is ``+MOOD_BONUS``, ``-MOOD_BONUS``, or ``0``.
    """
    roll = rng.randint(1, 20)
    bonus_threshold, penalty_max = MOOD_THRESHOLDS.get(mood, (19, 2))

    if roll >= bonus_threshold:
        return roll, MOOD_BONUS
    if roll <= penalty_max:
        return roll, -MOOD_BONUS
    return roll, 0.0


def roll_race_day_form(mood: int, rng: random.Random) -> float:
    """Roll a hidden Race Day Form modifier based on mood.

    Returns a float offset applied multiplicatively to all segment scores
    for this racer during the race: ``segment_score * (1.0 + form)``.
    Better mood gives a wider upside range and narrower downside.
    """
    low, high = FORM_TABLE.get(mood, FORM_TABLE[3])
    return rng.uniform(low, high)


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
    stumble_counts: dict[int, int] = field(default_factory=dict)  # racer_id -> count
    form: dict[int, float] = field(default_factory=dict)  # racer_id -> race day form offset


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


def calculate_buy_price(
    racer: models.Racer,
    base_cost: int,
    multiplier: int,
    female_multiplier: float = 1.0,
) -> int:
    """Return the buy price for a racer based on its base stats.

    Females cost more because of their breeding value.  The stat component
    is scaled by *female_multiplier* when the racer's gender is ``"F"``.
    """
    stat_total = (racer.speed + racer.cornering + racer.stamina) * multiplier
    if getattr(racer, "gender", "M") == "F":
        stat_total = int(stat_total * female_multiplier)
    return base_cost + stat_total


def calculate_sell_price(
    racer: models.Racer,
    base_cost: int,
    multiplier: int,
    sell_fraction: float,
    female_multiplier: float = 1.0,
    retired_penalty: float = 1.0,
    foal_penalty: float = 1.0,
    max_foals: int = 3,
) -> int:
    """Return the sell price with penalties for retired status and foal count.

    *retired_penalty* scales the price when the racer is retired (e.g. 0.6).
    *foal_penalty* is the floor multiplier at max foals.  The actual
    multiplier interpolates linearly: ``1 - (foal_count / max_foals) * (1 - foal_penalty)``.
    """
    buy = calculate_buy_price(racer, base_cost, multiplier, female_multiplier)
    price = buy * sell_fraction
    if getattr(racer, "retired", False):
        price *= retired_penalty
    foal_count = getattr(racer, "foal_count", 0) or 0
    if foal_count > 0 and max_foals > 0:
        foal_mult = 1.0 - (foal_count / max_foals) * (1.0 - foal_penalty)
        price *= foal_mult
    return int(price)


def parse_stable_upgrade_costs(cost_string: str) -> list[int]:
    """Parse a comma-separated string like ``"500,1000,2000"`` into a list of ints."""
    if not cost_string or not cost_string.strip():
        return []
    return [int(v.strip()) for v in cost_string.split(",") if v.strip()]


def get_next_upgrade_cost(extra_slots: int, costs: list[int]) -> int | None:
    """Return the cost of the next stable slot upgrade, or None if maxed."""
    if extra_slots >= len(costs):
        return None
    return costs[extra_slots]


def generate_pool_racer(guild_id: int, taken_names: Set[str]) -> dict:
    """Return kwargs suitable for ``create_racer()`` to populate the pool.

    Generates random stats, temperament, and a name from the name pool.
    Falls back to a numbered name if all pool names are taken.
    """
    name = pick_name(taken_names)
    if name is None:
        # All 100 names exhausted — use a fallback
        base = random.choice(_load_names())
        name = f"{base}-{random.randint(100, 999)}"
    career_length = random.randint(25, 40)
    speed = random.randint(0, 31)
    cornering = random.randint(0, 31)
    stamina = random.randint(0, 31)
    return {
        "name": name,
        "owner_id": 0,
        "guild_id": guild_id,
        "speed": speed,
        "cornering": cornering,
        "stamina": stamina,
        "temperament": random.choice(list(TEMPERAMENTS.keys())),
        "career_length": career_length,
        "peak_end": int(career_length * 0.6),
        "gender": random.choice(["M", "F"]),
        "rank": calculate_rank(speed, cornering, stamina),
    }


MAX_STAT = 31
TRAINABLE_STATS = {"speed", "cornering", "stamina"}

# ---------------------------------------------------------------------------
# Rank system
# ---------------------------------------------------------------------------

# Ordered highest-first so the first match wins.
RANK_THRESHOLDS: list[tuple[str, int]] = [
    ("S", 81),
    ("A", 66),
    ("B", 47),
    ("C", 24),
    ("D", 0),
]


def calculate_rank(speed: int, cornering: int, stamina: int) -> str:
    """Return the rank letter (D/C/B/A/S) for a given set of base stats."""
    total = speed + cornering + stamina
    for letter, minimum in RANK_THRESHOLDS:
        if total >= minimum:
            return letter
    return "D"  # fallback, shouldn't happen


def assign_rank_if_needed(racer: models.Racer) -> str | None:
    """Set the racer's rank if not already assigned.  Returns the rank if
    newly assigned, or ``None`` if the racer already had one.

    Rank is permanent — once set it never changes, even if stats change.
    """
    if racer.rank is not None:
        return None
    racer.rank = calculate_rank(racer.speed, racer.cornering, racer.stamina)
    return racer.rank


def rank_label(rank: str | None) -> str:
    """Return a human-readable rank label like 'B-Rank' or 'Unranked'."""
    if rank is None:
        return "Unranked"
    return f"{rank}-Rank"


# ---------------------------------------------------------------------------
# Tournament system
# ---------------------------------------------------------------------------

TOURNAMENT_PRIZES: dict[str, list[int]] = {
    "D": [150, 75, 37, 37],
    "C": [400, 200, 100, 100],
    "B": [1000, 500, 250, 250],
    "A": [2500, 1250, 625, 625],
    "S": [5000, 2500, 1250, 1250],
}

# Stat total ranges per rank (inclusive) for pool racer generation
_RANK_STAT_RANGES: dict[str, tuple[int, int]] = {
    "D": (0, 23),
    "C": (24, 46),
    "B": (47, 65),
    "A": (66, 80),
    "S": (81, 93),
}

TOURNAMENT_SELL_BONUS: dict[str, int] = {
    "D": 50,
    "C": 150,
    "B": 400,
    "A": 1000,
    "S": 2500,
}


@dataclass
class TournamentRoundResult:
    """Results of a single tournament round."""

    round_number: int  # 1, 2, or 3
    race_result: "RaceResult"
    advancing: list[int]  # racer IDs advancing to next round
    eliminated: list[int]  # racer IDs eliminated this round


@dataclass
class TournamentResult:
    """Full results of a 3-round tournament."""

    rounds: list[TournamentRoundResult]
    final_placements: list[int]  # racer IDs, 1st place first (all 8)


def run_tournament(
    racers: list[models.Racer],
    seed: int,
    race_map: "RaceMap | None" = None,
) -> TournamentResult:
    """Run a 3-round elimination tournament (8 → 4 → 2 → winner).

    Each round uses ``simulate_race`` with a per-round seed derived from
    the base seed.  Returns full placements for all 8 racers.

    Parameters
    ----------
    racers:
        Exactly 8 Racer objects to compete.
    seed:
        Base RNG seed; each round derives its own sub-seed.
    race_map:
        Optional map; if ``None``, a random map is picked per round.
    """
    if len(racers) != 8:
        raise ValueError(f"Tournament requires exactly 8 racers, got {len(racers)}")

    rng = random.Random(seed)
    remaining = list(racers)
    rounds: list[TournamentRoundResult] = []
    all_eliminated: list[int] = []  # ordered from last eliminated to first

    # Pre-load maps so we can pick deterministically with the seeded rng
    available_maps = load_all_maps() if race_map is None else []

    for round_num, cut_to in [(1, 4), (2, 2), (3, 1)]:
        round_seed = rng.randint(0, 2**31)
        if race_map is not None:
            round_map = race_map
        elif available_maps:
            round_map = rng.choice(available_maps)
        else:
            round_map = None

        # Build a pseudo-race dict for simulate_race
        race_obj: Dict[str, list] = {"racers": remaining}
        result = simulate_race(race_obj, round_seed, race_map=round_map)

        # Split placements into advancing and eliminated
        advancing_ids = result.placements[:cut_to]
        eliminated_ids = result.placements[cut_to:]

        # Eliminated are stored in reverse placement order (worst first)
        # so final_placements ends up correct
        all_eliminated = list(reversed(eliminated_ids)) + all_eliminated

        rounds.append(
            TournamentRoundResult(
                round_number=round_num,
                race_result=result,
                advancing=advancing_ids,
                eliminated=eliminated_ids,
            )
        )

        # Build remaining racers list for next round
        advancing_set = set(advancing_ids)
        remaining = [r for r in remaining if r.id in advancing_set]

    # Final placements: winner first, then all eliminated in reverse order
    # (runner-up is the last one added to all_eliminated from round 3)
    final_round = rounds[-1]
    winner_id = final_round.race_result.placements[0]
    final_placements = [winner_id] + all_eliminated

    return TournamentResult(
        rounds=rounds,
        final_placements=final_placements,
    )


def generate_pool_racer_for_rank(
    rank: str,
    guild_id: int,
    taken_names: Set[str],
) -> dict:
    """Generate a pool racer with stats within the given rank's range.

    Returns kwargs suitable for ``create_racer()``.  The stat total is
    uniformly distributed within the rank's range, then randomly split
    across speed/cornering/stamina (each capped at 31).
    """
    stat_min, stat_max = _RANK_STAT_RANGES.get(rank, (0, 23))
    total = random.randint(stat_min, stat_max)

    # Distribute total across 3 stats, each capped at MAX_STAT (31)
    speed, cornering, stamina = _distribute_stats(total)

    name = pick_name(taken_names)
    if name is None:
        base = random.choice(_load_names())
        name = f"{base}-{random.randint(100, 999)}"

    career_length = random.randint(25, 40)
    return {
        "name": name,
        "owner_id": 0,
        "guild_id": guild_id,
        "speed": speed,
        "cornering": cornering,
        "stamina": stamina,
        "temperament": random.choice(list(TEMPERAMENTS.keys())),
        "career_length": career_length,
        "peak_end": int(career_length * 0.6),
        "gender": random.choice(["M", "F"]),
        "rank": rank,
    }


def _distribute_stats(total: int) -> tuple[int, int, int]:
    """Randomly distribute *total* points across 3 stats, each capped at 31."""
    cap = MAX_STAT
    for _ in range(1000):
        # a must leave a feasible remainder for b+c (each <= cap)
        a_min = max(0, total - 2 * cap)
        a_max = min(total, cap)
        a = random.randint(a_min, a_max)
        remainder = total - a
        b_min = max(0, remainder - cap)
        b_max = min(remainder, cap)
        b = random.randint(b_min, b_max)
        c = remainder - b
        if 0 <= c <= cap:
            stats = [a, b, c]
            random.shuffle(stats)
            return stats[0], stats[1], stats[2]
    # Fallback: even distribution
    base = total // 3
    extra = total % 3
    return base + (1 if extra > 0 else 0), base + (1 if extra > 1 else 0), base


GENDER_LABELS = {"M": "\u2642", "F": "\u2640"}


def calculate_training_cost(current_stat: int, base: int, multiplier: int) -> int:
    """Return the coin cost to train a stat from *current_stat* to *current_stat + 1*."""
    return base + current_stat * multiplier


def training_failure_chance(mood: int, injured: bool) -> float:
    """Return probability (0.0-1.0) that a training session fails.

    Mood penalty: Awful (1) = 50%, Bad (2) = 25%, Normal+ = 0%.
    Injury penalty: 25% if injured (injury_races_remaining > 0).
    Combined multiplicatively: P(fail) = 1 - (1 - mood_fail) * (1 - injury_fail).
    """
    mood_fail = {1: 0.50, 2: 0.25}.get(mood, 0.0)
    injury_fail = 0.25 if injured else 0.0
    return 1.0 - (1.0 - mood_fail) * (1.0 - injury_fail)


def apply_rest(current_mood: int) -> tuple[int, str | None]:
    """Apply rest to a racer, raising mood by 1 (cap 5).

    Returns ``(new_mood, error_message)``.  *error_message* is non-``None``
    when the action should be rejected (racer already at max mood).
    """
    if current_mood >= 5:
        return current_mood, "This racer is already in great spirits and doesn't need rest."
    return min(current_mood + 1, 5), None


def apply_feed(current_mood: int) -> tuple[int, str | None]:
    """Apply premium feed to a racer, raising mood by 2 (cap 5).

    Returns ``(new_mood, error_message)``.  *error_message* is non-``None``
    when the action should be rejected (racer already at max mood).
    """
    if current_mood >= 5:
        return current_mood, "This racer is already in great spirits and doesn't need feeding."
    return min(current_mood + 2, 5), None


# ---------------------------------------------------------------------------
# Breeding
# ---------------------------------------------------------------------------


def check_lineage(racer_a: models.Racer, racer_b: models.Racer) -> str | None:
    """Return an error string if the two racers are too closely related.

    Rejects parent-child and half-sibling pairings.  Returns ``None``
    when the pairing is acceptable.
    """
    # Parent-child
    if racer_a.sire_id == racer_b.id or racer_a.dam_id == racer_b.id:
        return "Cannot breed a racer with its own parent."
    if racer_b.sire_id == racer_a.id or racer_b.dam_id == racer_a.id:
        return "Cannot breed a racer with its own parent."

    # Half-siblings (shared sire or dam, both non-None)
    if (
        racer_a.sire_id is not None
        and racer_b.sire_id is not None
        and racer_a.sire_id == racer_b.sire_id
    ):
        return "Cannot breed half-siblings (shared sire)."
    if (
        racer_a.dam_id is not None
        and racer_b.dam_id is not None
        and racer_a.dam_id == racer_b.dam_id
    ):
        return "Cannot breed half-siblings (shared dam)."

    return None


def validate_breeding(
    sire: models.Racer,
    dam: models.Racer,
    owner_id: int,
    stable_count: int,
    max_slots: int,
    *,
    min_races: int = 5,
    max_foals: int = 3,
) -> str | None:
    """Return an error string if the breeding is invalid, else ``None``."""
    if sire.gender != "M":
        return f"**{sire.name}** is not male."
    if dam.gender != "F":
        return f"**{dam.name}** is not female."
    if sire.owner_id != owner_id or dam.owner_id != owner_id:
        return "You must own both racers to breed them."
    if (sire.races_completed or 0) < min_races:
        return f"**{sire.name}** needs at least {min_races} races before breeding."
    if (dam.races_completed or 0) < min_races:
        return f"**{dam.name}** needs at least {min_races} races before breeding."
    if (sire.breed_cooldown or 0) > 0:
        return f"**{sire.name}** is on breeding cooldown ({sire.breed_cooldown} races remaining)."
    if (dam.breed_cooldown or 0) > 0:
        return f"**{dam.name}** is on breeding cooldown ({dam.breed_cooldown} races remaining)."
    if (dam.foal_count or 0) >= max_foals:
        return f"**{dam.name}** has already had the maximum {max_foals} foals."
    if stable_count >= max_slots:
        return "Your stable is full. Sell a racer or upgrade your stable first."

    lineage_err = check_lineage(sire, dam)
    if lineage_err:
        return lineage_err

    return None


def breed_racer(
    sire: models.Racer,
    dam: models.Racer,
    guild_id: int,
    rng: random.Random | None = None,
) -> dict:
    """Generate kwargs for ``create_racer()`` representing the offspring.

    Stat inheritance: one stat (chosen randomly) inherits from parents
    (random value between them), the other two are random 0-31.
    Temperament: 10% random mutation, else 75% sire / 25% dam.
    Career: average of parents ±5, clamped 25-40.
    """
    if rng is None:
        rng = random.Random()

    # Pick 1 stat to inherit
    stats = ["speed", "cornering", "stamina"]
    inherited_stat = rng.choice(stats)
    other_stats = [s for s in stats if s != inherited_stat]

    sire_val = getattr(sire, inherited_stat)
    dam_val = getattr(dam, inherited_stat)
    lo, hi = min(sire_val, dam_val), max(sire_val, dam_val)
    inherited_value = rng.randint(lo, hi)

    result_stats = {}
    result_stats[inherited_stat] = inherited_value
    for s in other_stats:
        result_stats[s] = rng.randint(0, 31)

    # Temperament
    if rng.randint(1, 10) == 1:
        temperament = rng.choice(list(TEMPERAMENTS.keys()))
    else:
        temperament = sire.temperament if rng.randint(1, 4) <= 3 else dam.temperament

    # Career
    avg_career = (sire.career_length + dam.career_length) / 2
    career_length = int(avg_career + rng.randint(-5, 5))
    career_length = max(25, min(40, career_length))

    return {
        "name": f"{dam.name}'s Foal",
        "owner_id": sire.owner_id,
        "guild_id": guild_id,
        "speed": result_stats["speed"],
        "cornering": result_stats["cornering"],
        "stamina": result_stats["stamina"],
        "temperament": temperament,
        "mood": 3,
        "career_length": career_length,
        "peak_end": int(career_length * 0.6),
        "gender": rng.choice(["M", "F"]),
        "sire_id": sire.id,
        "dam_id": dam.id,
        "training_count": 0,
        "rank": calculate_rank(
            result_stats["speed"], result_stats["cornering"], result_stats["stamina"]
        ),
    }


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


def effective_stats(racer: models.Racer) -> dict[str, int]:
    """Return a racer's stats with decline penalty applied.

    During the decline phase (races_completed > peak_end), each stat is
    reduced by ``(races_completed - peak_end)``.  Base stats are never
    modified — the penalty is applied at simulation time only.
    """
    completed = getattr(racer, "races_completed", None) or 0
    peak = getattr(racer, "peak_end", None) or 18
    penalty = max(0, completed - peak)
    return {
        "speed": max(0, racer.speed - penalty),
        "cornering": max(0, racer.cornering - penalty),
        "stamina": max(0, racer.stamina - penalty),
    }


def career_phase(racer: models.Racer) -> str:
    """Return a human-readable career phase label."""
    if racer.races_completed >= racer.career_length:
        return "Retired"
    remaining = racer.career_length - racer.races_completed
    if remaining <= 3:
        return "Retiring Soon"
    if racer.races_completed > racer.peak_end:
        decline = racer.races_completed - racer.peak_end
        return f"Declining (-{decline})"
    return "Peak"


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
    """Return the effective power score for a racer after temperament and decline."""
    stats = apply_temperament(effective_stats(racer), racer.temperament)
    return float(stats["speed"] + stats["cornering"] + stats["stamina"])


def _segment_score(
    racer_stats: Dict[str, int],
    segment_type: str,
    distance: int,
    segment_index: int,
    rng: random.Random,
) -> tuple[float, float]:
    """Calculate a racer's score for a single segment.

    Returns ``(score, noise_mult)`` where *noise_mult* is the multiplicative
    random factor (used for event detection).  Noise is multiplicative so
    variance scales with power — everyone can have bad segments.
    """
    weights = SEGMENT_TYPES.get(segment_type, SEGMENT_TYPES["straight"])

    raw = (
        racer_stats["speed"] * weights["speed"]
        + racer_stats["cornering"] * weights["cornering"]
        + racer_stats["stamina"] * weights["stamina"]
    )

    distance_factor = 0.8 + (distance * 0.2)
    fatigue = max(0, segment_index * 1.5 - racer_stats["stamina"] * 0.15)
    noise_mult = rng.uniform(NOISE_MULT_LOW, NOISE_MULT_HIGH)
    floor = rng.uniform(0, NOISE_FLOOR)

    return (raw * distance_factor - fatigue) * noise_mult + floor, noise_mult


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

    for rid, noise_mult in noise_rolls.items():
        if noise_mult < 0.65:
            events.append(f"{names.get(rid, f'Racer {rid}')} stumbles!")
        elif noise_mult > 1.35:
            events.append(f"{names.get(rid, f'Racer {rid}')} surges forward!")

    return events


def _mood_expected_bonus(mood: int) -> float:
    """Return the average per-segment bonus for a given mood.

    Used to nudge odds so they reflect mood advantage/disadvantage.
    """
    bonus_threshold, penalty_max = MOOD_THRESHOLDS.get(mood, (19, 2))
    bonus_chance = max(0, 21 - bonus_threshold) / 20.0
    penalty_chance = penalty_max / 20.0
    return (bonus_chance - penalty_chance) * MOOD_BONUS


def _map_weighted_power(
    racer: models.Racer, race_map: RaceMap
) -> float:
    """Return the expected power score for a racer on a specific map."""
    stats = apply_temperament(effective_stats(racer), racer.temperament)
    total = 0.0
    for seg in race_map.segments:
        weights = SEGMENT_TYPES.get(seg.type, SEGMENT_TYPES["straight"])
        total += (
            stats["speed"] * weights["speed"]
            + stats["cornering"] * weights["cornering"]
            + stats["stamina"] * weights["stamina"]
        )
    base = total / len(race_map.segments) if race_map.segments else _racer_power(racer)
    return base + _mood_expected_bonus(getattr(racer, "mood", 3))


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

    NOISE_BASELINE = 5.0  # small floor so zero-stat racers get non-zero odds
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
                effective_stats(r), r.temperament,
            )

        cumulative: Dict[int, float] = {r.id: 0.0 for r in raw_racers}
        stumble_counts: Dict[int, int] = {r.id: 0 for r in raw_racers}
        prev_order = [r.id for r in raw_racers]
        segment_results: List[SegmentResult] = []

        # Build mood lookup for d20 rolls
        racer_moods: Dict[int, int] = {
            r.id: getattr(r, "mood", 3) for r in raw_racers
        }

        # Roll hidden Race Day Form for each racer (mood-influenced)
        race_form: Dict[int, float] = {
            r.id: roll_race_day_form(racer_moods[r.id], rng)
            for r in raw_racers
        }

        for seg_idx, seg in enumerate(race_map.segments):
            seg_scores: Dict[int, float] = {}
            noise_rolls: Dict[int, float] = {}
            mood_rolls: Dict[int, tuple[int, float]] = {}
            for rid, stats in racer_stats.items():
                score, noise_mult = _segment_score(
                    stats, seg.type, seg.distance, seg_idx, rng
                )
                # Mood d20 roll
                d20, mood_bonus = roll_mood_bonus(racer_moods.get(rid, 3), rng)
                mood_rolls[rid] = (d20, mood_bonus)
                score += mood_bonus

                # Apply hidden Race Day Form
                score *= (1.0 + race_form[rid])

                seg_scores[rid] = score
                noise_rolls[rid] = noise_mult
                cumulative[rid] += score
                if noise_mult < 0.65:
                    stumble_counts[rid] += 1

            curr_order = sorted(
                cumulative.keys(), key=lambda rid: cumulative[rid], reverse=True
            )
            standings = [
                (rid, seg_scores[rid], cumulative[rid]) for rid in curr_order
            ]
            events = _detect_events(
                prev_order, curr_order, names, cumulative, noise_rolls
            )

            # Add mood roll events
            for rid, (d20, bonus) in mood_rolls.items():
                rname = names.get(rid, f"Racer {rid}")
                if bonus > 0:
                    if d20 == 20:
                        events.append(f"{rname} rolls a natural 20! Inspired burst of energy!")
                    else:
                        events.append(f"{rname} finds a burst of confidence! (d20: {d20})")
                elif bonus < 0:
                    if d20 == 1:
                        events.append(f"{rname} rolls a natural 1! Completely loses focus!")
                    else:
                        events.append(f"{rname} loses concentration. (d20: {d20})")

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
            stumble_counts=stumble_counts,
            form=race_form,
        )

    # --- Legacy single-pass fallback ---
    if has_stats:
        scored: List[Tuple[int, float]] = []
        for racer in raw_racers:
            power = _racer_power(racer)
            score = power * rng.uniform(NOISE_MULT_LOW, NOISE_MULT_HIGH) + rng.uniform(0, NOISE_FLOOR)
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


INJURY_DESCRIPTIONS = [
    "Pulled hamstring",
    "Bruised shoulder",
    "Sprained ankle",
    "Strained tendon",
    "Twisted knee",
    "Cracked rib",
    "Sore back",
    "Jarred hoof",
]


def check_injury_risk(
    result: RaceResult,
    rng: random.Random | None = None,
) -> list[tuple[int, str, int]]:
    """Check for post-race injuries based on stumbles and last place.

    Each stumble during the race gives a 5% injury chance (nat 1 on d20).
    Last place gets one additional 5% check.

    Returns list of ``(racer_id, injury_description, recovery_races)``.
    """
    if rng is None:
        rng = random.Random()

    injuries: list[tuple[int, str, int]] = []
    if not result.placements:
        return injuries

    last_place_id = result.placements[-1] if len(result.placements) > 1 else None

    # Collect all racers who need injury rolls
    # Each stumble = one d20 roll, nat 1 = injured
    for rid in result.placements:
        num_stumble_rolls = result.stumble_counts.get(rid, 0)

        # Last place gets one extra roll
        if rid == last_place_id:
            num_stumble_rolls += 1

        for _ in range(num_stumble_rolls):
            if rng.randint(1, 20) == 1:  # nat 1 = 5%
                description = rng.choice(INJURY_DESCRIPTIONS)
                recovery = rng.randint(1, 4) + rng.randint(1, 4)  # 2d4
                injuries.append((rid, description, recovery))
                break  # only one injury per racer per race

    return injuries


async def apply_injuries(
    session: AsyncSession,
    injuries: list[tuple[int, str, int]],
    participants: list[models.Racer] | None = None,
) -> None:
    """Apply injuries to racers in the database."""
    racer_map: dict[int, models.Racer] = {}
    if participants:
        racer_map = {r.id: r for r in participants}

    for rid, description, recovery in injuries:
        racer = racer_map.get(rid) or await session.get(models.Racer, rid)
        if racer is None:
            continue
        racer.injuries = description
        racer.injury_races_remaining = recovery


async def apply_mood_drift(
    session: AsyncSession,
    placements: list[int],
    participants: list[models.Racer] | None = None,
) -> dict[int, tuple[int, int]]:
    """Adjust racer moods after a race and return changes.

    Winner mood +1 (cap 5), last place mood -1 (floor 1).
    All other racers drift one step toward neutral (3) — this keeps
    unowned racers from spiralling into permanent bad mood.

    Returns ``{racer_id: (old_mood, new_mood)}`` for racers that changed.
    """
    if not placements:
        return {}

    changes: dict[int, tuple[int, int]] = {}
    winner_id = placements[0]
    loser_id = placements[-1] if len(placements) > 1 else None

    # Build lookup of participants for in-memory updates
    racer_map: dict[int, models.Racer] = {}
    if participants:
        racer_map = {r.id: r for r in participants}

    for rid in placements:
        racer = racer_map.get(rid) or await session.get(models.Racer, rid)
        if racer is None:
            continue
        old_mood = racer.mood

        if rid == winner_id:
            new_mood = min(5, old_mood + 1)
        elif rid == loser_id:
            new_mood = max(1, old_mood - 1)
        else:
            # Drift toward neutral (3)
            if old_mood > 3:
                new_mood = old_mood - 1
            elif old_mood < 3:
                new_mood = old_mood + 1
            else:
                new_mood = old_mood

        if new_mood != old_mood:
            racer.mood = new_mood
            changes[rid] = (old_mood, new_mood)

    return changes


def parse_placement_prizes(prize_string: str) -> list[int]:
    """Parse a comma-separated prize string like ``"50,30,20"`` into a list of ints."""
    if not prize_string or not prize_string.strip():
        return []
    return [int(v.strip()) for v in prize_string.split(",") if v.strip()]


async def resolve_placement_prizes(
    session: AsyncSession,
    placements: list[int],
    participants: list[models.Racer],
    guild_id: int,
    prize_list: list[int],
) -> list[tuple[int, int, int]]:
    """Credit owner wallets for placement finishes.

    Returns ``[(owner_id, racer_id, prize)]`` for each prize awarded.
    Unowned racers (owner_id == 0) and positions beyond the prize list
    are skipped.
    """
    racer_map = {r.id: r for r in participants}
    awarded: list[tuple[int, int, int]] = []

    for position, racer_id in enumerate(placements):
        if position >= len(prize_list):
            break
        prize = prize_list[position]
        if prize <= 0:
            continue
        racer = racer_map.get(racer_id)
        if racer is None or racer.owner_id == 0:
            continue

        wallet = (
            await session.execute(
                select(Wallet).where(
                    Wallet.user_id == racer.owner_id,
                    Wallet.guild_id == guild_id,
                )
            )
        ).scalars().first()
        if wallet is None:
            wallet = Wallet(user_id=racer.owner_id, guild_id=guild_id, balance=0)
            session.add(wallet)
            await session.commit()
            await session.refresh(wallet)

        wallet.balance += prize
        awarded.append((racer.owner_id, racer_id, prize))

    return awarded


async def resolve_payouts(
    session: AsyncSession, race_id: int, winner_id: int, guild_id: int = 0
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
        wallet = (
            await session.execute(
                select(Wallet).where(
                    Wallet.user_id == bet.user_id,
                    Wallet.guild_id == guild_id,
                )
            )
        ).scalars().first()
        if wallet is None:
            wallet = Wallet(user_id=bet.user_id, guild_id=guild_id, balance=0)
            session.add(wallet)
            await session.commit()
            await session.refresh(wallet)

        if bet.racer_id == winner_id:
            payout = int(bet.amount * bet.payout_multiplier)
            wallet.balance += payout
        await session.delete(bet)

    await session.commit()
