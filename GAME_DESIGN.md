# Downtime Derby — Game Design Document

Downtime Derby is a Discord mini-game where players bet on automated animal races using in-server currency. On the surface it's simple — pick a racer, place a bet, watch the race — but under the hood it's designed to reward players who pay attention to racer stats, temperaments, moods, and lineages. It runs daily between D&D sessions to keep downtime lively.

---

## How It Works Today

### Races

Races run automatically on a daily schedule (configurable via `race_frequency`). Each race:

1. **Announces** in the configured channel with a list of participants and odds
2. **Opens betting** for 2 minutes (configurable via `bet_window`)
3. **Counts down** (3... 2... 1...)
4. **Streams live commentary** with 2-second delays between events
5. **Posts results** as an embed with final placements
6. **DMs bettors** with their win/loss outcome

Up to 8 random active (non-retired) racers are selected per race. Races are seeded by race ID for reproducibility. Race outcomes are determined by a weighted scoring system: each racer's stats (after temperament modifiers) produce a power score, plus random noise. Stronger racers win more often but upsets happen.

### Betting

- Players place bets with `/race bet <racer_id> <amount>`
- **One bet per player per race** — placing a new bet refunds the previous one
- Coins are deducted immediately when the bet is placed
- Winning bets pay **2x the wager** (bet 50, receive 100)
- Losing bets are simply gone

### Wallets

- Auto-created on first `/wallet` check or first bet
- Starting balance: **100 coins** (configurable via `default_wallet`)
- Only two flows: coins out (betting) and coins in (winning)

### Racer Stats

Each racer has three core stats on a **0–31 scale**:

| Stat | Description |
|------|-------------|
| **Speed** | Straightaway performance and acceleration |
| **Cornering** | Handling through turns and tight sections |
| **Stamina** | Endurance over longer races |

Stats are displayed as quality bands in `/race info`:

| Range | Label |
|-------|-------|
| 0–15 | Decent |
| 16–25 | Good |
| 26–29 | Very Good |
| 30 | Fantastic |
| 31 | Perfect |

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

The `apply_temperament()` function exists in `derby/logic.py` to calculate modified stats.

### Mood

Racers have a mood on a 1–5 scale:

| Value | Label |
|-------|-------|
| 1 | Awful |
| 2 | Bad |
| 3 | Normal (default) |
| 4 | Good |
| 5 | Great |

Mood is visible in `/race info`.

### Injuries

A freeform text field on each racer. Displayed in `/race info` as "None" if empty. Currently admin-managed only.

### Retirement & Succession

After each race, every participant has a chance to retire based on the `retirement_threshold` setting. When a racer retires:

- They're flagged as `retired` and removed from the active pool
- A successor is created with the name `"{Original Name} II"`, the same owner, and the parent's temperament
- Successors inherit **50–75%** of each parent stat (randomized per stat)

Default threshold: **96** — meaning `random(1–100) >= 96` triggers retirement, which is roughly a **5% chance per race**. This gives racers meaningful multi-race careers while still introducing turnover over time.

### Odds

Odds are calculated based on each racer's effective power score (stats after temperament). Strong racers get lower payout multipliers (e.g., 1.5x) while underdogs get higher ones (e.g., 4x+). A 10% house edge is applied. This means betting on favorites is safer but less profitable, while betting on underdogs is riskier but pays more.

---

## Implementation Status

| System | Status | Details |
|--------|--------|---------|
| Race scheduling | **Working** | Daily automated creation and execution via `DerbyScheduler` |
| Betting mechanics | **Working** | Place/change/refund bets, wallet management, balance checks |
| Live commentary | **Working** | Streamed to channel with delay, cancellable |
| Race results & DMs | **Working** | Embed posted, individual DMs sent to bettors |
| Racer stats (speed/cornering/stamina) | **Working** | Stats influence race outcomes via weighted scoring |
| Temperament modifiers | **Working** | Applied during simulation — boosts/penalizes stats by 10% |
| Mood | **Stored only** | Saved and displayed in `/race info`, no game effect yet |
| Injuries | **Stored only** | Freeform text field, no mechanics or effects |
| Course segments | **Schema only** | `CourseSegment` model exists, no track generation or segment-based simulation |
| Odds calculation | **Working** | Stat-weighted odds — strong racers get lower payouts, underdogs get higher |
| Retirement/succession | **Working** | ~5% chance per race; successors inherit 50-75% of parent stats + temperament |
| Racer ownership | **Metadata only** | `owner_id` stored but owners get no economic benefit from their racer winning |
| Guild settings | **Schema only** | `GuildSettings` model exists but `config.yaml` is used for all settings |
| Admin tools | **Working** | Add/edit/delete racers, create/cancel/force-start races, debug dumps |
| Race winner tracking | **Working** | `winner_id` stored on Race model, used for history display |

### Remaining Gaps

1. **Mood has no game effect.** Stored and displayed but doesn't influence race outcomes.

2. **Injuries have no mechanics.** Freeform text field with no actual debuffs or recovery system.

3. **Course segments aren't used.** The model exists but races don't generate or use segment types.

4. **Ownership has no economic tie-in.** Owners don't earn anything when their racer wins.

---

## Future Vision

These features are referenced in `AGENTS.md` and align with the original design intent:

### Priority: Wire Up Remaining Systems

- **Mood effects** — Mood should provide a bonus/penalty multiplier on race day (e.g., Great mood = small boost, Awful = penalty)
- **Mood drift** — Mood should change over time or based on race results (winning improves mood, losing worsens it)

### New Features (Planned)

- **Course segments** — Different segment types (straights, curves, endurance stretches, hazards) that favor different stats. The `CourseSegment` model already exists for this.
- **Ownership stakes** — Players buy into a racer and earn a percentage cut of that racer's winnings. Creates investment beyond single-race betting.
- **Lineage & breeding** — Track racer family trees. Cross-breed two racers to create offspring with inherited stat tendencies. Deeper than single-parent succession.
- **Racer training** — Players spend coins or time to improve a racer's stats. Creates a money sink and long-term engagement.
- **Player-owned stables** — Manage multiple racers, choose which ones race, invest in their development.
- **Tournaments** — Multi-race events with cumulative scoring and bigger prize pools.
- **Weather effects** — Random or scheduled weather that modifies race conditions (rain penalizes speed, wind affects cornering, etc.).
- **Injury mechanics** — Racing carries injury risk. Injuries apply stat debuffs and require recovery time before the racer can compete again.

---

## Command Reference

### Player Commands

| Command | Description |
|---------|-------------|
| `/race next` | Show the next scheduled race ID |
| `/race upcoming` | Show upcoming race with racer list and odds |
| `/race bet <racer_id> <amount>` | Place or change a bet on the next race |
| `/race watch` | Watch the next race with interactive commentary (Next button) |
| `/race info <racer_id>` | View a racer's stats, temperament, mood, and injuries |
| `/race history [count]` | Show recent race results (default: 5) |
| `/wallet` | Check your coin balance |

### Admin Commands (requires "Race Admin" role)

| Command | Description |
|---------|-------------|
| `/derby add_racer <name> <owner> [random_stats] [speed] [cornering] [stamina] [temperament]` | Create a new racer |
| `/derby edit_racer <racer_id> [name] [speed] [cornering] [stamina] [temperament]` | Modify a racer's attributes |
| `/derby racer delete <racer_id>` | Remove a racer permanently |
| `/derby start_race` | Manually create a new race |
| `/derby cancel_race` | Cancel the next pending race |
| `/derby race force-start [race_id]` | Immediately simulate a pending race |
| `/derby debug race <race_id>` | Dump full race data as JSON |

### Configuration (`config.yaml`)

| Setting | Default | Description |
|---------|---------|-------------|
| `race_frequency` | 1 | Races created per guild per day |
| `default_wallet` | 100 | Starting coin balance for new players |
| `retirement_threshold` | 96 | Retirement triggers when `random(1-100) >= threshold` |
| `bet_window` | 120 | Seconds the betting window stays open |
| `countdown_total` | 10 | Seconds for the pre-race countdown |
| `channel_name` | downtime-derby | Channel name to announce races in |

---

## Architecture

```
bot.py                    Entry point, initializes DerbyScheduler
cogs/derby.py             All player and admin slash commands
derby/
  models.py               SQLAlchemy ORM models (Racer, Race, Bet, Wallet, CourseSegment, GuildSettings)
  logic.py                Race simulation, odds calculation, payout resolution, temperament system
  repositories.py         Data access layer (CRUD for all models)
  scheduler.py            Background task: daily race creation, execution, commentary, retirement
config/
  __init__.py             Settings loader (Pydantic + YAML)
database/
  database.db             SQLite database (auto-created)
```
