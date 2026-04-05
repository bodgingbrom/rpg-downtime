# Downtime Derby — Game Design Document

Downtime Derby is a Discord mini-game where players bet on automated animal races using in-server currency. On the surface it's simple — pick a racer, place a bet, watch the race — but under the hood it's designed to reward players who pay attention to racer stats, temperaments, moods, and lineages. It runs daily between D&D sessions to keep downtime lively.

---

## How It Works Today

### Races

Races run automatically on a configurable schedule (up to 3 times per day via `race_times`). Each race:

1. **Announces** in the configured channel with a list of participants and odds
2. **Opens betting** for 2 minutes (configurable via `bet_window`)
3. **Counts down** (3... 2... 1...)
4. **Streams live commentary** with delays between events, narrating segment-by-segment action
5. **Posts results** as an embed with final placements
6. **DMs bettors** with their win/loss outcome
7. **Awards placement prizes** to owners of racers that finish 1st/2nd/3rd
8. **Applies post-race effects** — mood drift, injury checks, injury recovery, career increments, retirements

Up to 6 random active (non-retired, non-injured) racers are selected per race. Races are seeded by race ID for reproducibility.

### Race Maps & Segments

Races use map-based simulation with different segment types (straight, corner, climb, descent, hazard). Each segment type weights the three stats differently — straights favor speed, corners favor cornering, climbs favor stamina, etc. Maps are loaded from `derby/maps/` as JSON files.

Per segment, each racer's score is calculated from weighted stats, fatigue (stamina-dependent), mood rolls, and random noise. Cumulative scores across all segments determine final placement.

### Betting

- Players place bets with `/race bet <racer> <amount>`
- **One bet per player per race** — placing a new bet refunds the previous one
- Coins are deducted immediately when the bet is placed
- Odds are calculated from effective power scores — strong racers get lower payouts, underdogs pay more
- A 10% house edge is applied
- Losing bets are simply gone

### Wallets

- Auto-created on first interaction with a starting balance of **100 coins** (configurable)
- Coins flow in from: winning bets, placement prizes (owning a racer that places)
- Coins flow out to: betting, buying racers, training, resting, feeding

### Racer Stats

Each racer has three core stats on a **0-31 scale**:

| Stat | Description |
|------|-------------|
| **Speed** | Straightaway performance and acceleration |
| **Cornering** | Handling through turns and tight sections |
| **Stamina** | Endurance over longer races, reduces fatigue |

Stats are displayed as quality bands:

| Range | Label |
|-------|-------|
| 0-15 | Decent |
| 16-25 | Good |
| 26-29 | Very Good |
| 30 | Fantastic |
| 31 | Perfect |

**Effective stats** account for age decline: after `peak_end` races, each stat is reduced by `(races_completed - peak_end)`. Base stats are never modified — the penalty is applied at simulation time only.

### Temperaments

Each racer has one of 7 temperaments that apply a **+10% / -10% modifier** to two stats:

| Temperament | Boosted Stat | Penalized Stat |
|-------------|-------------|----------------|
| Agile | Speed | Stamina |
| Reckless | Speed | Cornering |
| Tactical | Cornering | Speed |
| Burly | Stamina | Cornering |
| Steady | Stamina | Speed |
| Sharpshift | Cornering | Stamina |
| Quirky | — | — |

### Mood

Racers have a mood on a 1-5 scale that directly affects race performance:

| Value | Label | Per-Segment Effect |
|-------|-------|--------------------|
| 1 | Awful | 0% bonus chance, 20% penalty chance (-5 pts) |
| 2 | Bad | 5% bonus chance, 20% penalty chance |
| 3 | Normal | 10% bonus, 10% penalty (balanced) |
| 4 | Good | 15% bonus chance, 10% penalty chance |
| 5 | Great | 20% bonus chance (+5 pts), 5% penalty chance |

Each segment, a D20 is rolled per racer. The roll is compared against mood-based thresholds to determine bonus/penalty application. This creates meaningful variance — a Great mood racer gets frequent boosts while an Awful mood racer suffers frequent penalties.

**Mood changes:**
- **Post-race drift**: Winner +1 (cap 5), last place -1 (floor 1), others drift toward 3
- **Training**: Always -1 (floor 1)
- **Rest** (`/stable rest`): +1 mood for 15 coins
- **Feed** (`/stable feed`): +2 mood for 30 coins

### Injuries

Racing carries injury risk. When a racer stumbles during simulation (low noise roll), they may sustain an injury:

- Injuries have a descriptive label and a recovery timer (`injury_races_remaining`)
- Injured racers are **excluded from races** until healed
- Recovery ticks down by 1 after each race in their guild
- Injuries add a **25% training failure chance** (multiplicative with mood penalties)

### Ownership & Stables

Players can own up to 3 racers (configurable) and manage them through `/stable` commands:

- **Browse & Buy**: View unowned racers with stat-based pricing (`base + total_stats * multiplier`)
- **Sell**: Return a racer to the pool at 50% of buy price
- **Rename**: Give your racer a custom name (unique per guild)
- **Train**: Spend coins to permanently increase a stat by 1 (cost scales with current stat value, drops mood by 1, can fail based on mood/injury)
- **Rest**: Spend 15 coins to improve mood by 1
- **Feed**: Spend 30 coins to improve mood by 2

**Placement prizes**: When an owned racer finishes 1st/2nd/3rd, the owner earns coins (default 50/30/20). This creates passive income from investing in good racers.

**Pool replenishment**: The system automatically generates unowned racers (from a pool of 200 names with random stats) to maintain at least 20 available for purchase per guild.

### Retirement & Succession

Racers have a career length (25-40 races) and a peak phase (first 60%). After peak, stats decline linearly. When `races_completed >= career_length`, a racer retires:

- Flagged as `retired` and removed from the active pool
- A successor is created with the name `"{Original Name} II"`, the same owner, and randomized stats
- Career length and peak are re-rolled for the successor

### Odds

Odds are calculated from each racer's effective power score (stats + temperament modifiers + mood expected bonus), weighted by the race map's segment composition. Strong racers get lower payout multipliers while underdogs get higher ones. A 10% house edge is applied.

---

## Implementation Status

| System | Status | Details |
|--------|--------|---------|
| Race scheduling | **Working** | Configurable daily schedule via `race_times`, automated creation and execution |
| Race maps & segments | **Working** | JSON maps with segment types (straight, corner, climb, descent, hazard) |
| Live commentary | **Working** | Streamed to channel with configurable delay, segment-by-segment narration |
| Betting mechanics | **Working** | Place/change/refund bets, wallet management, odds-based payouts |
| Race results & DMs | **Working** | Embed posted, individual DMs sent to bettors |
| Racer stats | **Working** | Speed/cornering/stamina (0-31) influence outcomes via weighted segment scoring |
| Effective stats & decline | **Working** | Age-based decline after peak_end, applied at simulation time |
| Temperament modifiers | **Working** | Applied during simulation — boosts/penalizes stats by 10% |
| Mood system | **Working** | D20 rolls per segment, post-race drift, rest/feed commands |
| Injury mechanics | **Working** | Injury risk from stumbles, recovery timer, race exclusion, training penalty |
| Odds calculation | **Working** | Map-weighted power scores with mood bonus, 10% house edge |
| Retirement & succession | **Working** | Career length system with peak/decline, successor creation on retirement |
| Racer ownership | **Working** | Buy/sell/rename, stat-based pricing, ownership limit |
| Placement prizes | **Working** | Owners earn 50/30/20 coins for 1st/2nd/3rd place finishes |
| Training | **Working** | `/stable train` — spend coins to improve stats, mood cost, failure chance |
| Mood care | **Working** | `/stable rest` (+1 mood) and `/stable feed` (+2 mood) |
| Pool replenishment | **Working** | Auto-generates unowned racers to maintain minimum pool size |
| Guild settings | **Working** | Per-guild nullable overrides for all configurable settings |
| Admin tools | **Working** | Add/edit/delete racers, create/cancel/force-start races, configure settings |

---

## Future Vision

### Planned Features

- **Tournaments** — Multi-race events with cumulative scoring and bigger prize pools. Players enter racers into a tournament bracket spanning multiple races, with points awarded per placement.
- **Lineage & breeding** — Track racer family trees beyond single-parent succession. Cross-breed two racers to create offspring with inherited stat tendencies from both parents.

---

## Command Reference

### Player Commands

| Command | Description |
|---------|-------------|
| `/race next` | Show the next scheduled race ID |
| `/race upcoming` | Show upcoming race with racer list and odds |
| `/race bet <racer> <amount>` | Place or change a bet on the next race |
| `/race watch` | Watch the next race with interactive commentary |
| `/race info <racer>` | View a racer's stats, temperament, mood, and injuries |
| `/race history [count]` | Show recent race results (default: 5) |
| `/wallet` | Check your coin balance |
| `/stable` | View your owned racers |
| `/stable browse` | Browse racers available for purchase |
| `/stable buy <racer>` | Purchase an unowned racer |
| `/stable sell <racer>` | Sell one of your racers back to the pool |
| `/stable rename <racer> <new_name>` | Rename one of your racers |
| `/stable train <racer> <stat>` | Train a racer to improve a stat (+1, costs coins and mood) |
| `/stable rest <racer>` | Rest a racer to improve mood (+1, 15 coins) |
| `/stable feed <racer>` | Feed a racer premium oats to boost mood (+2, 30 coins) |

### Admin Commands (requires "Race Admin" role)

| Command | Description |
|---------|-------------|
| `/derby add_racer <name> <owner> [random_stats] [speed] [cornering] [stamina] [temperament]` | Create a new racer |
| `/derby edit_racer <racer> [name] [speed] [cornering] [stamina] [temperament]` | Modify a racer's attributes |
| `/derby racer delete <racer>` | Remove a racer permanently |
| `/derby start_race` | Manually create a new race |
| `/derby cancel_race` | Cancel the next pending race |
| `/derby race force-start [race_id]` | Immediately simulate a pending race |
| `/derby settings` | View all settings with current values |
| `/derby settings set <key> <value>` | Override a setting for this guild |
| `/derby debug race <race_id>` | Dump full race data as JSON |

### Configuration

All settings have global defaults (in `config.yaml`) and can be overridden per guild via `/derby settings set`.

| Setting | Default | Description |
|---------|---------|-------------|
| `default_wallet` | 100 | Starting coin balance for new players |
| `retirement_threshold` | 96 | Retirement triggers when `random(1-100) >= threshold` (~5% chance) |
| `bet_window` | 120 | Seconds the betting window stays open |
| `countdown_total` | 10 | Seconds for the pre-race countdown |
| `max_racers_per_race` | 6 | Maximum racers selected per race |
| `commentary_delay` | 6.0 | Seconds between commentary messages |
| `channel_name` | downtime-games | Channel name to announce races in |
| `racer_buy_base` | 20 | Base cost to purchase a racer |
| `racer_buy_multiplier` | 2 | Cost multiplier per total stat point |
| `racer_sell_fraction` | 0.5 | Sell price as fraction of buy price |
| `max_racers_per_owner` | 3 | Maximum racers a player can own |
| `min_pool_size` | 20 | Minimum unowned racers to maintain per guild |
| `placement_prizes` | "50,30,20" | Coins awarded to owners for 1st/2nd/3rd |
| `training_base` | 10 | Base cost per training session |
| `training_multiplier` | 2 | Training cost multiplier per current stat value |
| `rest_cost` | 15 | Cost to rest a racer (+1 mood) |
| `feed_cost` | 30 | Cost to feed a racer (+2 mood) |

---

## Architecture

```
bot.py                    Entry point, initializes DerbyScheduler
cogs/
  derby.py                All player and admin slash commands (Derby + Stable cogs)
  economy.py              Wallet commands
derby/
  models.py               SQLAlchemy ORM models (Racer, Race, Bet, RaceEntry, GuildSettings)
  logic.py                Race simulation, odds, payouts, temperament, mood, training, injuries
  repositories.py         Data access layer (CRUD for all models)
  scheduler.py            Background task: race scheduling, execution, post-race effects, migrations
  commentary.py           Race commentary generation
  names.txt               200 unique racer names for pool generation
  maps/                   JSON race maps with segment definitions
economy/
  models.py               Wallet model
  repositories.py         Wallet CRUD
config/
  __init__.py             Settings loader (Pydantic + YAML) with guild override resolution
database/
  database.db             SQLite database (auto-created)
```
