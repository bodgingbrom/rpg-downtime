# Getting Started

Welcome to Downtime Derby! Here's how to jump in.

## Your Wallet

Every player starts with **100 coins**. Check your balance anytime with `/wallet`. You earn coins by:

- **Winning bets** on races
- **Placement prizes** from daily races (top 3 finishers' owners get coins)
- **Tournament prizes** for top 4 finishers
- **Selling racers** you no longer want

## Step 1: Browse the Pool

Use `/stable browse` to see all the racers available for purchase. Each racer shows:

- **Stats** (Speed, Cornering, Stamina) displayed as quality bands
- **Temperament** that gives small bonuses/penalties to stats
- **Gender** (important for breeding later)
- **Rank** (D through S, based on total stats)
- **Price** in coins

## Step 2: Buy Your First Racer

Found one you like? Use `/stable buy <racer>` to purchase them. The price is based on their total stats -- stronger racers cost more, and females cost extra because they can produce foals.

You start with **3 stable slots**. You can buy more later with `/stable upgrade`.

## Step 3: Watch Races & Bet

Races happen automatically on a schedule (your server admin sets the times). Use `/race upcoming` to see the next race and the odds for each racer.

Place bets using one of 5 bet types -- from simple win bets to moon-shot superfectas:

- `/race bet-win <racer> <amount>` -- bet on a racer to finish 1st
- `/race bet-place <racer> <amount>` -- bet on a racer to finish 1st or 2nd
- `/race bet-exacta <1st> <2nd> <amount>` -- predict exact 1st and 2nd
- `/race bet-trifecta <1st> <2nd> <3rd> <amount>` -- predict exact top 3
- `/race bet-superfecta <1st> ... <6th> <amount>` -- predict the entire finish order

You can place **one of each type** per race (up to 5 bets total). Placing a new bet of the same type refunds the old. See [Racing & Betting](racing.md) for full details and odds.

## Step 4: Train Your Racer

Your racer enters the race pool automatically, but training makes them more competitive. Use `/stable train <racer> <stat>` to boost speed, cornering, or stamina by 1 point.

Training costs coins and **lowers mood by 1**, so manage your racer's happiness with `/stable rest` or `/stable feed`.

## Step 5: Breed & Compete

Once your racers have enough experience, you can breed them to produce foals that inherit stats from their parents. Enter your best racers in weekly tournaments for big prizes and bragging rights.

## Tips

- **Don't drain your racer's mood** -- low mood increases training failure chance and hurts race performance
- **Watch the maps** -- different track layouts favor different stats
- **Check temperaments** -- a racer with high speed but the "Reckless" temperament gets a cornering penalty
- **Tournaments match by rank** -- even weaker racers can compete and win in lower brackets
