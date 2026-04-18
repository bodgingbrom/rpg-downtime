# Gear & Bait

Your rod and bait stack multiplicatively with skill level and trophies to reduce cast time and shape the catch pool.

## Rods

Four tiers. Upgrade with `/fish upgrade-rod` — each tier replaces the previous one (there's no going back, but you wouldn't want to).

| Rod | Cost | Cast Speed | Trash Rate | Rare Boost |
|-----|------|-----------|-----------|-----------|
| **Basic Rod** | Free (starter) | 0% | 100% | +0% |
| **Oak Rod** | 400 coins | −15% | 50% | +5% |
| **Steel Rod** | 900 coins | −25% | 25% | +10% |
| **Master Rod** | 1,800 coins | −35% | 0% | +15% |

- **Cast Speed** reduces the timer between catches (AFK and active both).
- **Trash Rate** is the relative weight of trash items in your catch pool. At Master (0%), you never pull trash in AFK mode.
- **Rare Boost** adds to the weight of uncommon+ fish in the pool. Over a long session this meaningfully shifts your catch mix toward rarer fish.

The Master Rod pays for itself over time at River Rapids or Deep Lake, but it's a long grind to afford it.

## Bait

Four types. Bait is consumed one-per-catch regardless of outcome. Stock up with `/fish buy-bait`.

| Bait | Cost | Cast Speed | Preference Boost | Notes |
|------|------|-----------|-----------------|-------|
| **Worm** | 2 coins | 0% | 1.5× | Cheapest, fine for commons |
| **Insect** | 5 coins | −2% | 1.8× | Small speed bump, better for mid-tier fish |
| **Shiny Lure** | 12 coins | −5% | 2.0× | Sweet spot for most players |
| **Premium Bait** | 20 coins | −8% | 2.5× | Required for certain legendaries |

**Preference boost** is applied to fish that specifically prefer that bait type — each fish in the YAML has a `preferred_bait`, and using it multiplies that fish's weight in the draw. Some fish (legendaries especially) have a `required_bait`, meaning they *only* appear when you're using that specific bait.

## How Cast Time Is Calculated

The formula, for both modes:

```
final_time = base_time
           × (1 − rod_cast_reduction)
           × (1 − bait_cast_reduction)
           × (1 − skill_reduction)
           × (1 − trophy_reduction)
           × race_multiplier
```

- `base_time`: AFK uses the location's `base_cast_time` (10-35 min depending on location). Active uses a random 30-90 second roll per bite.
- `skill_reduction`: 2% per level above the location's required level.
- `trophy_reduction`: 10% once you've completed the location's species collection.
- `race_multiplier`: player race modifier (e.g. Orc cast-time reduction).

There's a floor of 60 seconds for AFK and 15 seconds for active, so the math can't fully zero out.

**Example**: Steel Rod + Shiny Lure at River Rapids with Lv3 skill and no trophy:
- base: 1500s (25 min)
- × (1 − 0.25) × (1 − 0.05) × (1 − 0.02) × (1 − 0)
- = 1500 × 0.75 × 0.95 × 0.98
- ≈ 1047s (~17.5 min)

## Shopping

`/fish shop` shows current bait prices, your next rod upgrade, and your coin balance all in one ephemeral embed — use it before any session to make sure you're not starting empty-handed.

## Upgrade Path

A sensible progression:

1. **Start**: Basic Rod + Worms @ Calm Pond. Grind ~2-3 hours of AFK for the Oak Rod.
2. **Mid-early**: Oak Rod + Insects @ Calm Pond. Grind up to Lv2 (300 XP) to unlock River Rapids.
3. **Mid**: Steel Rod + Shiny Lure @ River Rapids. This is the sweet spot for most players.
4. **Late**: Master Rod + Premium Bait @ Deep Lake. Zero trash, maximum rares, and the only way to reliably encounter the legendary characters.
