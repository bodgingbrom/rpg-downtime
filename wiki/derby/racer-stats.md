# Racer Stats

Every racer has three core stats, a temperament, a mood, and a career arc that all affect their racing performance.

## Core Stats (0-31)

| Stat | What It Does |
|------|-------------|
| **Speed** | Dominates on straights and descents |
| **Cornering** | Dominates on corners and helps on hazards |
| **Stamina** | Dominates on climbs and helps everywhere |

Stats are displayed as quality bands:

| Range | Label |
|-------|-------|
| 0-15 | Decent |
| 16-25 | Good |
| 26-29 | Very Good |
| 30 | Fantastic |
| 31 | Perfect |

Each stat caps at **31**. You can increase stats through training (`/stable train`) but never beyond the cap.

## Temperament

Every racer has a temperament that gives a **10% bonus** to one stat and a **10% penalty** to another. This is applied during races, not to base stats.

| Temperament | Boosted Stat | Penalized Stat |
|-------------|-------------|----------------|
| Agile | Speed | Stamina |
| Reckless | Speed | Cornering |
| Tactical | Cornering | Speed |
| Burly | Stamina | Cornering |
| Steady | Stamina | Speed |
| Sharpshift | Cornering | Stamina |
| Quirky | None | None |

Temperament is permanent and set at birth. When breeding, there's a 10% chance the foal gets a random temperament, otherwise 75% chance to inherit from the sire and 25% from the dam.

## Mood (1-5)

Mood affects race performance through a hidden **Race Day Form** modifier and d20 bonus/penalty rolls during each segment.

| Mood | Label | Race Day Form Range | Bonus Chance | Penalty Chance |
|------|-------|-------------------|-------------|----------------|
| 1 | Awful | -35% to +10% | 0% | 20% |
| 2 | Bad | -25% to +15% | 5% | 10% |
| 3 | Normal | -20% to +25% | 10% | 10% |
| 4 | Good | -10% to +35% | 15% | 20% |
| 5 | Great | -5% to +45% | 20% | 5% |

**Race Day Form** is rolled once per race and applies to every segment. A racer in Great mood almost always gets a positive form bonus, while an Awful mood racer is likely to have a bad day.

### How Mood Changes

- **After a race:** Winner gets +1 mood, last place gets -1 mood, everyone else drifts toward Normal (3)
- **Training** lowers mood by 1
- **Resting** (`/stable rest`) raises mood by 1 (free)
- **Feeding** (`/stable feed`) raises mood by 2 (costs 30 coins)
- **Winning a tournament:** 1st place gets mood set to 5, 2nd-4th get +1

Mood is capped between 1 and 5.

## Career Phases

Every racer has a career length (25-40 races) and a peak period (first ~60% of their career).

| Phase | When | Effect |
|-------|------|--------|
| **Peak** | Before peak_end | Full stats, best performance |
| **Declining (-N)** | After peak, before retirement | Stats reduced by N per race past peak |
| **Retiring Soon** | 3 or fewer races left | Still declining, about to retire |
| **Retired** | Career complete | Can no longer race or be trained |

During decline, effective stats drop by 1 for each race past peak. A racer with 28 Speed who is 5 races past peak effectively has 23 Speed in races.

## Injuries

Racers can get injured during races. Each stumble during a race gives a **5% chance** (nat 1 on d20) of injury. Last place gets one extra injury roll.

Injuries last **2-8 races** (2d4) and give a **25% training failure chance** while active. Injuries heal automatically as races complete. The racer still participates in races while injured.

## Rank

Every racer's rank is based on their **base stat total** (speed + cornering + stamina). Rank is **recalculated whenever stats change** -- through training or admin edits -- so a racer can climb into higher tournament brackets by training up their stats.

| Rank | Stat Total | Description |
|------|-----------|-------------|
| D | 0-23 | Common, low stats |
| C | 24-46 | Average, bulk of the pool |
| B | 47-65 | Above average |
| A | 66-80 | Elite |
| S | 81-93 | Legendary, typically bred |

Rank determines which [tournament bracket](tournaments.md) a racer competes in.
