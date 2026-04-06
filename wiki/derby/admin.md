# Admin Guide

This page covers server setup, admin commands, and configurable settings. All admin commands require the **Manage Server** permission and/or the **Race Admin** role.

## Setup

The bot runs automatically once added to your server. It will:

1. Create the database tables on first run
2. Generate a pool of unowned racers (default: 20)
3. Start the race schedule at the configured times
4. Start the tournament schedule tick

The bot posts race announcements to a channel matching the `channel_name` setting (default: none -- falls back to system channel or first text channel).

## Admin Commands

### Racer Management

| Command | Description |
|---------|-------------|
| `/derby add_racer <name> <owner> [stats...]` | Create a racer with specific or random stats |
| `/derby edit_racer <racer> [name] [speed] [cornering] [stamina] [temperament]` | Modify a racer's attributes |
| `/derby racer delete <racer>` | Permanently remove a racer |
| `/derby racer injure <racer> <description> <races>` | Manually injure a racer |
| `/derby racer heal <racer>` | Instantly heal a racer's injury |

### Race Management

| Command | Description |
|---------|-------------|
| `/derby race force-start [race_id]` | Immediately run the next pending race (or a specific one) |
| `/derby start_schedule` | Start the automatic race timer |
| `/derby stop_schedule` | Stop the automatic race timer |
| `/derby cancel_race` | Delete the next pending race |

**Note:** Force-starting a race automatically queues the next scheduled race, so the schedule continues uninterrupted. Force-starts are "bonus" races.

### Economy

| Command | Description |
|---------|-------------|
| `/derby give-coins <user> <amount>` | Give coins to a player (use negative amount to remove) |

Cannot remove more coins than a player has (balance floor is 0). If the player doesn't have a wallet yet, one is created with the default starting balance before applying the amount.

### Debug

| Command | Description |
|---------|-------------|
| `/derby debug race <race_id>` | Dump full race data as JSON (ephemeral) |

## Settings

View current settings with `/derby settings`. Override any setting for your server with `/derby settings set <key> <value>`. Use `reset` as the value to revert to the global default.

### Race Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `race_times` | 09:00, 15:00, 21:00 | Race schedule times (UTC), comma-separated |
| `max_racers_per_race` | 6 | Maximum racers in each race |
| `bet_window` | 120 | Seconds players have to place bets after announcement |
| `countdown_total` | 10 | Seconds for the pre-race countdown |
| `commentary_delay` | 6.0 | Seconds between commentary messages |
| `placement_prizes` | 50,30,20 | Coins for 1st, 2nd, 3rd place owners |
| `channel_name` | (none) | Channel name for race announcements |
| `min_training_to_race` | 5 | Training sessions required before a bred foal can race |
| `min_pool_size` | 40 | Minimum unowned racers maintained in the pool |
| `race_stat_window` | 35 | Stat-total range for competitive race matching |

### Economy Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `default_wallet` | 100 | Starting coins for new players |
| `racer_buy_base` | 20 | Base cost for buying a racer |
| `racer_buy_multiplier` | 2 | Per-stat-point cost multiplier |
| `racer_sell_fraction` | 0.5 | Sell price as fraction of buy price |
| `female_buy_multiplier` | 1.5 | Extra cost multiplier for female racers |
| `retired_sell_penalty` | 0.6 | Sell price multiplier for retired racers |
| `foal_sell_penalty` | 0.3 | Sell price floor multiplier at max foals |
| `training_base` | 10 | Base cost for training |
| `training_multiplier` | 2 | Per-stat-point training cost multiplier |
| `rest_cost` | 0 | Cost to rest a racer (+1 mood) |
| `feed_cost` | 30 | Cost to feed a racer (+2 mood) |

### Stable & Breeding Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `max_racers_per_owner` | 3 | Base stable slots per player |
| `stable_upgrade_costs` | 500,1000,2000 | Cost for each additional slot |
| `breeding_fee` | 25 | Cost to breed two racers |
| `breeding_cooldown` | 6 | Races both parents must wait before breeding again |
| `min_races_to_breed` | 5 | Races a racer needs before it can breed |
| `max_foals_per_female` | 3 | Maximum foals a female can produce |

### Flavor Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `racer_flavor` | (none) | Free-text creature theme for racer descriptions (e.g., "cyberpunk racing lizards") |

### Tournament Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `tournament_enabled` | true | Enable/disable tournament system (global only) |

Tournament schedule and prizes are not per-guild configurable -- they use fixed values (see [Tournaments](tournaments.md)).

## Pool Management

The bot automatically maintains a minimum pool of unowned racers. Each tick, if the pool drops below `min_pool_size`, up to 5 new racers are generated with random stats and names.

Pool racers have `owner_id = 0` and are available for players to purchase via `/stable buy`.

## Race Retirement

When a racer completes their career (races_completed reaches career_length), they automatically retire. Retired racers:

- Can no longer enter races
- Can no longer be trained
- Still count toward stable slots
- Can still be sold (at a 60% penalty)
- Cannot breed
