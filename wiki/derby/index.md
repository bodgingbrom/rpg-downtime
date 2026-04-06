# Downtime Derby

Downtime Derby is a fantasy racing game where players own, train, breed, and race creatures. Races happen automatically on a schedule, and you can bet on any race or enter your own racers into weekly tournaments.

## How It Works

1. **Browse & Buy** a racer from the pool with `/stable browse` and `/stable buy`
2. **Train** your racer's stats with `/stable train` to make them competitive
3. **Bet** on daily races with `/race bet-win` (and 4 other bet types) to earn coins
4. **Breed** your racers to produce foals that can inherit the best traits
5. **Enter Tournaments** with `/tournament register` for big prizes and accolades

## Pages

| Page | Description |
|------|-------------|
| [Getting Started](getting-started.md) | Your first racer, earning coins, the basics |
| [Your Stable](stable.md) | Buying, selling, training, feeding, resting, upgrading |
| [Racer Stats](racer-stats.md) | Speed, cornering, stamina, temperament, mood, careers |
| [Racing & Betting](racing.md) | How races simulate, odds, betting, maps |
| [Breeding](breeding.md) | Requirements, stat inheritance, foals |
| [Tournaments](tournaments.md) | Ranks, brackets, schedule, prizes |
| [Admin Guide](admin.md) | Commands, settings, server configuration |

## Commands at a Glance

### Player Commands
| Command | Description |
|---------|-------------|
| `/race upcoming` | See the next race, odds, and racers |
| `/race bet-win <racer> <amount>` | Bet on a racer to win |
| `/race bet-place <racer> <amount>` | Bet on a racer to finish top 2 |
| `/race bet-exacta <1st> <2nd> <amount>` | Predict exact 1st and 2nd |
| `/race bet-trifecta <1st> <2nd> <3rd> <amount>` | Predict exact top 3 |
| `/race bet-superfecta <1st>...<6th> <amount>` | Predict entire finish order |
| `/stable view <racer>` | View a racer's full profile |
| `/race history` | View recent race results |
| `/stable` | View your owned racers |
| `/stable browse` | See racers available for purchase |
| `/stable buy <racer>` | Buy an unowned racer |
| `/stable sell <racer>` | Sell one of your racers |
| `/stable rename <racer> <name>` | Rename one of your racers |
| `/stable train <racer> <stat>` | Train a stat (costs coins, lowers mood) |
| `/stable rest <racer>` | Rest a racer to improve mood (+1) |
| `/stable feed <racer>` | Feed premium oats for mood (+2) |
| `/stable upgrade` | Buy an extra stable slot |
| `/stable breed <male> <female>` | Breed two racers to produce a foal |
| `/tournament register <racer>` | Enter a racer in the next tournament |
| `/tournament cancel <racer>` | Withdraw from a pending tournament |
| `/tournament list` | View pending tournaments and registrations |
| `/wallet` | Check your coin balance |
