# Downtime Derby Quick Start Guide

A racing and betting mini-game for your D&D downtime sessions.

---

## Player Guide

### How It Works

Downtime Derby is a racing mini-game where you bet on racers between D&D sessions. Each racer has stats (speed, cornering, stamina) and a temperament that affects their performance. Stronger racers win more often, but upsets happen!

### Getting Started

1. **`/wallet`** -- Check your balance (creates your wallet with 100 starting coins on first use)
2. **`/race upcoming`** -- See the next race, all racers, their odds, and example payouts
3. **`/race bet`** -- Pick a racer from the dropdown and set your wager
4. Wait for the race to run! Results and commentary stream live in the channel.
5. If you win, you get a DM with your payout. Losers get a condolence DM.

### Player Commands

| Command | What It Does |
|---------|-------------|
| `/wallet` | Show your coin balance (creates wallet on first use) |
| `/race upcoming` | See the next race with all racers, odds, and payout examples |
| `/race bet` | Bet on a racer -- type to search by name, pick from dropdown |
| `/race info` | Inspect a racer's stats, temperament, and mood |
| `/race history` | See recent race results and winners |

### Betting Tips

- **Odds reflect stats** -- a 1.5x payout means the racer is a heavy favorite; 4x+ means a longshot
- **You can change your bet** -- placing a new bet automatically refunds the old one (you'll see the refund in the confirmation)
- **One bet per race** -- you can only back one racer at a time
- **Payout = bet x odds** -- bet 50 on a 2.3x racer and you'll get 115 coins if they win

### Reading the Odds

When you run `/race upcoming`, each racer shows something like:

> **Lightning (#5)** -- 2.3x -- bet 100, win 230

- **Name (#ID)** -- the racer and their ID number
- **2.3x** -- the payout multiplier (lower = more likely to win)
- **bet 100, win 230** -- example payout to help you decide

---

## Admin Guide

This section is for the DM or whoever has the Race Admin role.

### First-Time Setup

1. **Create the Race Admin role** in your Discord server settings
2. **Assign it to yourself** (and any co-DMs)
3. **Add racers:** `/derby add_racer` -- give each player a racer, or use `random_stats` for quick setup
4. **Start a race:** `/derby start_race` -- creates a race and shows you all participants + odds

That's it! The scheduler handles the rest -- announcing, countdown, simulation, results, and payouts.

### Admin Commands

| Command | What It Does |
|---------|-------------|
| `/derby add_racer` | Add a racer -- set stats manually or use random_stats. Temperament is a dropdown! |
| `/derby edit_racer` | Edit a racer -- shows before/after diff for changed stats |
| `/derby racer delete` | Remove a racer permanently |
| `/derby start_race` | Create a race -- shows participant pool and odds |
| `/derby cancel_race` | Cancel the next pending race |
| `/derby race force-start` | Run a race instantly (skips bet window) |
| `/derby debug race` | Dump raw race data (bets, participants) as JSON |

### Adding Racers

Two ways to create a racer:

- **Manual stats:** `/derby add_racer name:Lightning owner:@Player speed:25 cornering:20 stamina:22 temperament:Agile`
- **Random stats:** `/derby add_racer name:Lightning owner:@Player random_stats:True`

Both show a confirmation embed with the racer's full stat card. Temperament options appear as a **dropdown menu** -- no need to remember the names.

### Temperaments

| Temperament | Boosted Stat (+10%) | Reduced Stat (-10%) |
|-------------|--------------------|--------------------|
| **Agile** | Speed | Stamina |
| **Reckless** | Speed | Cornering |
| **Tactical** | Cornering | Speed |
| **Burly** | Stamina | Cornering |
| **Steady** | Stamina | Speed |
| **Sharpshift** | Cornering | Stamina |
| **Quirky** | None | None |

### Race Flow

When the scheduler (or force-start) runs a race, here's what happens:

1. **Announcement** -- Embed with participants, odds, payout examples, and a "Use /race bet" hint
2. **Bet window** -- Players have 2 minutes to place or change bets
3. **Countdown** -- 3... 2... 1...
4. **Commentary** -- Live race events streamed to the channel
5. **Results** -- Final placements with racer names
6. **Payouts** -- Winners and losers get DMs with racer names and amounts
7. **Retirements** -- If any racer retires (career complete), a channel announcement celebrates their career

### Tips

- **Force-start for testing:** `/derby race force-start` skips the 2-minute bet window -- great for verifying setup
- **Autocomplete everywhere:** All racer fields have searchable dropdowns -- no memorizing IDs
- **Debug mode:** `/derby debug race [id]` dumps full JSON (bets, participants) as an ephemeral message only you can see
- **Retirement:** Racers retire when their career ends (25-40 races). No successors -- buy or breed replacements
