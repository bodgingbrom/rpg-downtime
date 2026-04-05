# Tournaments

Tournaments are weekly ranked competitions with bigger prizes than daily races. They run in brackets based on racer rank, so even lower-stat racers have a chance to compete and win.

## How It Works

1. **Register** your racer with `/tournament register <racer>`
2. **Wait** for the scheduled tournament time
3. **The bracket fills** -- if fewer than 8 players registered, pool racers fill the remaining slots
4. **Three rounds of elimination** play out (8 -> 4 -> 2 -> winner)
5. **Results are announced** round by round with final standings and prizes

## Brackets

Tournaments are separated by rank. Your racer competes only against racers of the same rank tier.

| Rank | Stat Total | Typical Racers |
|------|-----------|----------------|
| D-Rank | 0-23 | Low-stat pool racers, early purchases |
| C-Rank | 24-46 | Average pool racers, lightly trained |
| B-Rank | 47-65 | Well-trained racers |
| A-Rank | 66-80 | Heavily trained or well-bred |
| S-Rank | 81-93 | Elite bred racers |

Remember: rank is based on **base stats at creation**, not current trained stats. A D-Rank racer trained to high stats still competes in D-Rank tournaments.

## Schedule (UTC)

| Day | Time | Ranks |
|-----|------|-------|
| Saturday | 12:00 AM | D-Rank, C-Rank (10 min apart) |
| Sunday | 12:00 AM | B-Rank, A-Rank (10 min apart) |
| Monday | 12:00 AM | S-Rank |

Tournaments only fire if **at least one player** has registered a racer. If nobody registers, nothing happens.

## Registration

- Use `/tournament register <racer>` anytime -- registration stays open until the tournament fires
- You can register **one racer per rank bracket** per tournament
- Use `/tournament cancel <racer>` to withdraw before the tournament starts
- Use `/tournament list` to see all pending tournaments with registered players

The tournament is auto-created when the first player registers for a given rank.

## Field Filling

Every tournament has exactly **8 racers**. If fewer than 8 players registered:

1. Existing unowned pool racers of the same rank are pulled in
2. If there still aren't enough, new rank-appropriate pool racers are generated

This means you'll always face a full bracket, even if you're the only player who registered.

## Elimination Format

The tournament runs **3 rounds**, each simulated as a full race on a randomly selected map:

| Round | Racers | Advance | Eliminated |
|-------|--------|---------|-----------|
| Round 1 | 8 | Top 4 | Bottom 4 |
| Round 2 | 4 | Top 2 | Bottom 2 |
| Round 3 (Final) | 2 | Winner | Runner-up |

Each round is an independent race -- a racer who barely scraped into the top 4 in round 1 might dominate round 2 if the map favors their stats or they get lucky.

## Prizes

### Coin Prizes
The top 4 finishers' **owners** receive coins (pool racers don't collect prizes):

| Place | D | C | B | A | S |
|-------|---|---|---|---|---|
| 1st | 150 | 400 | 1,000 | 2,500 | 5,000 |
| 2nd | 75 | 200 | 500 | 1,250 | 2,500 |
| 3rd | 37 | 100 | 250 | 625 | 1,250 |
| 4th | 37 | 100 | 250 | 625 | 1,250 |

### Rewards

| Place | Mood | Breed Cooldown | Accolade |
|-------|------|---------------|----------|
| 1st | Set to 5 (Great) | Reset to 0 | +1 Tournament Win |
| 2nd-4th | +1 (cap 5) | No change | +1 Tournament Placement |

### Sell Price Bonus
Each tournament **win** permanently increases the racer's sell price:

| Rank | Bonus Per Win |
|------|--------------|
| D | +50 coins |
| C | +150 coins |
| B | +400 coins |
| A | +1,000 coins |
| S | +2,500 coins |

This stacks -- an S-Rank racer with 3 wins gets +7,500 coins on their sell price.

## Accolades

Tournament wins and placements are tracked on each racer and displayed in `/race info` and `/stable`. These are permanent records of your racer's competitive achievements.
