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

### Bet Types

There are 5 bet types, from safe to moon-shot. You can place **one bet of each type per race** (up to 5 bets total).

| Command | What You Pick | Win Condition | Typical Odds |
|---------|--------------|---------------|-------------|
| `/race bet-win <racer> <amount>` | 1 racer | Finishes 1st | 2x--10x |
| `/race bet-place <racer> <amount>` | 1 racer | Finishes 1st or 2nd | 1.5x--4x |
| `/race bet-exacta <1st> <2nd> <amount>` | 2 racers in order | Exact 1st and 2nd | 8x--50x |
| `/race bet-trifecta <1st> <2nd> <3rd> <amount>` | 3 racers in order | Exact 1st, 2nd, 3rd | 30x--300x |
| `/race bet-superfecta <1st> ... <6th> <amount>` | All 6 racers in order | Exact finish order | 200x--5000x |

### How Betting Works

1. Use `/race upcoming` to see the next race, the racers, and their win odds
2. Place bets during the betting window (default 2 minutes) using any of the commands above
3. If you change your mind, placing a new bet **of the same type** refunds the old one
4. You can have a Win bet **and** a Place bet **and** an Exacta etc. all on the same race
5. You must have enough coins in your wallet

**Superfecta** requires a full field of 6 racers. If the race has fewer, the bet is rejected.

### Odds

Odds are calculated based on each racer's power score relative to the field using conditional probability. The house takes a **10% edge**.

- **Win/Place** odds are based on each racer's chance of finishing in the top positions
- **Exacta/Trifecta/Superfecta** odds use conditional probability chains -- the probability of your 1st pick winning, times the probability of your 2nd pick winning from the remaining field, and so on
- The more positions you predict, the harder it is to hit, but the bigger the payout
- Maps affect odds -- a racer's power is weighted by the map's segment types
- No bet pays less than 1.1x

### Examples

- **Win bet**: Bet 100 on Thunderhoof at 3.2x. If Thunderhoof wins, you get 320 coins
- **Place bet**: Bet 100 on Thunderhoof at 1.8x. If Thunderhoof finishes 1st or 2nd, you get 180 coins
- **Exacta**: Bet 50 on Thunderhoof 1st, Blazeclaw 2nd at 12.5x. If they finish in that exact order, you get 625 coins
- **Superfecta**: Bet 20 on the entire field in order at 1500x. If you nail every position, you get 30,000 coins

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
