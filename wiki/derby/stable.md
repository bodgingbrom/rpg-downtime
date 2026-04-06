# Your Stable

Your stable is where you manage all your owned racers. View it anytime with `/stable`.

## Viewing Racer Profiles

Use `/stable view <racer>` to see a racer's full profile. This works for **any** racer in the guild, not just your own. The profile shows:

- **Stats** -- speed, cornering, and stamina with quality labels and decline penalties
- **Temperament & Mood** -- current temperament and mood level
- **Career** -- races completed, career length, and current phase (Peak, Declining, Retiring Soon, Retired)
- **Rank** -- competitive tier (D through S)
- **Lineage** -- sire and dam names (for bred racers)
- **Foals** -- foal count (and maximum for females)
- **Tournament Record** -- wins and top-3 placements
- **Training** -- total sessions completed
- **Injury** -- current injury and remaining recovery time (if any)
- **Breed Cooldown** -- races remaining before eligible to breed again (if any)
- **Description** -- a physical description of the racer (requires `racer_flavor` to be set by an admin)

### Racer Flavor

Admins can set what kind of creatures the racers are with the `racer_flavor` guild setting:

```
/derby settings set racer_flavor cyberpunk racing lizards
```

This is free-text -- it could be "enchanted warhorses", "clockwork beetles", "sentient go-karts", or whatever fits your server's theme. Once set, racer descriptions will be generated based on this flavor.

## Buying Racers

### Browsing
Use `/stable browse` to see all unowned racers in the pool. The pool automatically replenishes so there's always racers available.

Each pool racer has a **24-48 hour window** before they rotate out and are replaced by a fresh racer. This keeps the browse list from getting stale -- check back regularly for new options!

### Pricing
Buy price is based on total stats:

> **Price = Base Cost + (Speed + Cornering + Stamina) x Multiplier**

Females cost **1.5x** the stat portion because of their breeding value. Default base cost is 20 coins with a 2x multiplier, so a racer with 45 total stats costs 20 + 90 = **110 coins** (or 155 for a female).

### Stable Slots
You start with **3 stable slots**. Retired racers still count toward your slots. You can expand with `/stable upgrade`:

| Upgrade | Cost |
|---------|------|
| 4th slot | 500 coins |
| 5th slot | 1,000 coins |
| 6th slot | 2,000 coins |

## Selling Racers

Use `/stable sell <racer>` to sell a racer back to the pool.

> **Sell Price = Buy Price x 50%**

Penalties apply:
- **Retired racers** sell for 60% of the normal sell price
- **Females with foals** sell for less -- the penalty scales linearly with foal count (3 foals = 30% floor)
- **Tournament winners** get a **flat bonus** per win, stacking:

| Rank | Bonus Per Win |
|------|--------------|
| D | +50 coins |
| C | +150 coins |
| B | +400 coins |
| A | +1,000 coins |
| S | +2,500 coins |

A B-Rank racer with 3 tournament wins gets +1,200 coins added to their sell price.

## Training

Use `/stable train <racer> <stat>` to increase speed, cornering, or stamina by 1 point.

### Cost
> **Training Cost = Base (10) + Current Stat Value x Multiplier (2)**

So training a stat from 15 to 16 costs 10 + 30 = **40 coins**. Training from 30 to 31 costs **70 coins**.

### Side Effects
- **Mood drops by 1** every time you train (even on failure)
- Training can **fail** if mood is low or the racer is injured:
  - Awful mood: 50% failure chance
  - Bad mood: 25% failure chance
  - Injured: 25% failure chance (stacks multiplicatively with mood)
- You still pay the full cost on failure
- Training counts toward your racer's training total (important for bred foals)

### Stat Cap
Stats max out at **31**. You can't train beyond that.

## Rest & Feed

Keep your racers happy to avoid training failures and improve race performance.

| Action | Command | Mood Change | Cost |
|--------|---------|------------|------|
| Rest | `/stable rest <racer>` | +1 | Free |
| Feed | `/stable feed <racer>` | +2 | 30 coins |

Mood is capped at 5 (Great). If already at max, the command is rejected and you aren't charged.

## Renaming

Use `/stable rename <racer> <name>` to rename a racer. Names must be unique within the guild and can be up to 32 characters.
