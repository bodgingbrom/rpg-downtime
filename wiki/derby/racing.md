# Racing & Betting

Races are the core of Downtime Derby. They happen automatically on a schedule set by your server admin, and any player can bet on the outcome.

## How Races Work

### Schedule
Races fire at configured times (default: 9:00 AM, 3:00 PM, and 9:00 PM UTC). Your admin can change this. When a race fires:

1. **Announcement** -- the race is posted with all participating racers and their odds
2. **Betting window** -- players have 2 minutes (default) to place bets
3. **Countdown** -- 3... 2... 1...
4. **Race simulation** -- the race plays out segment by segment with live commentary
5. **Results** -- final placements, bet payouts, injuries, and prizes announced

### Participants
Each race has up to **6 racers** drawn from the guild's pool (both owned and unowned). Racers must have completed enough training sessions to be eligible (default: 5).

### Race Simulation

Races play out on a randomly selected **map** with multiple segments. Each segment type favors different stats:

| Segment Type | Speed Weight | Cornering Weight | Stamina Weight |
|-------------|-------------|-----------------|----------------|
| Straight | High | Low | Medium |
| Corner | Low | High | Medium |
| Climb | Medium | Low | High |
| Descent | High | Medium | Low |
| Hazard | Low | Medium | High |

For each segment, every racer gets a score based on:
1. **Weighted stats** for that segment type (modified by temperament)
2. **Multiplicative noise** (x0.55 to x1.45) -- random performance variance
3. **Additive noise floor** (0-40 points) -- baseline chaos that lets weaker racers compete
4. **Mood d20 roll** -- chance for a bonus or penalty burst
5. **Race Day Form** -- a hidden modifier rolled once per race based on mood

Scores accumulate across all segments. The racer with the highest cumulative score wins.

### What This Means
- **Stats matter** -- stronger racers win more often (~60-65% in a 6-racer field)
- **Upsets happen** -- the noise floor ensures any racer can have a great day
- **Mood is important** -- a racer in Great mood has a much better form range than one in Awful mood
- **Maps shake things up** -- a speed-focused racer dominates on Desert Sprint but struggles on Mountain Pass

## Maps

Four maps are available, each with different segment layouts:

### Desert Sprint
A wide-open track built for speed. Heavy on straights with a sandstorm hazard.

### Frozen Circuit
Treacherous ice with tight corners and a brutal glacier climb. Favors cornering and stamina.

### Mountain Pass
Grueling climbs through rocky terrain with a hazardous gravel stretch. Stamina-heavy.

### The Gauntlet
A brutal mixed course through city streets. Tests every stat with straights, corners, hazards, a climb, and a descent.

## Betting

### Placing Bets
Use `/race upcoming` to see the next race and odds, then `/race bet <racer> <amount>` to wager.

- You can only have **one bet per race**
- Placing a new bet **refunds** your previous one
- You must have enough coins in your wallet

### Odds
Odds are calculated based on each racer's power score relative to the field. The house takes a **10% edge**.

- **Favorites** have low multipliers (e.g., 1.5x) -- safer but lower payout
- **Longshots** have high multipliers (e.g., 8x) -- risky but huge payout
- Maps affect odds -- a racer's power is weighted by the map's segment types

If your racer wins, you receive `bet amount x payout multiplier`.

### Viewing Results
Use `/race history` to see the last 5 race results (or specify a count).

## Post-Race Effects

After every race, several things happen to all participants:

| Effect | Who | What |
|--------|-----|------|
| **Mood drift** | Winner | +1 mood |
| **Mood drift** | Last place | -1 mood |
| **Mood drift** | Everyone else | Drifts 1 step toward Normal (3) |
| **Placement prizes** | Top 3 owners | Coins (default: 50/30/20) |
| **Injury check** | All racers | 5% per stumble, extra roll for last place |
| **Career progress** | All racers | +1 race completed |
| **Breed cooldown** | All racers | -1 cooldown tick (if on cooldown) |
| **Injury healing** | Injured racers | -1 race remaining on recovery |
| **Retirement** | End of career | Racer retires when races_completed = career_length |
