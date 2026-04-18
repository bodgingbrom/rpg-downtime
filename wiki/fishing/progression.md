# Progression

Fishing has three intertwined progression systems: XP/levels, the fish log, and location trophies. All three are per-player, per-guild.

## XP and Levels

Every catch awards XP, which goes into your fishing level. Higher levels unlock new locations and give cast-speed bonuses.

### XP by rarity

| Catch | Base XP | @ Lv1 loc | @ Lv2 loc | @ Lv3 loc |
|-------|---------|-----------|-----------|-----------|
| Trash | 1 | 1 | 2 | 3 |
| Common | 5 | 5 | 10 | 15 |
| Uncommon | 15 | 15 | 30 | 45 |
| Rare | 40 | 40 | 80 | 120 |
| Legendary | 100 | 100 | 200 | 300 |

XP is scaled by the location's `skill_level` (1/2/3), so harder spots reward proportionally more.

### Level thresholds

| Level | XP Required | Unlocks |
|-------|-------------|---------|
| Lv1 | 0 (start) | Calm Pond |
| Lv2 | 100 | River Rapids |
| Lv3 | 300 | Deep Lake |
| Lv4 | 600 | — |
| Lv5 | 1000 | — |

Max level is currently 5. Levels 4-5 don't unlock new locations but still grant cast-speed bonuses via over-leveling (see below).

### Over-leveling Bonus

Fishing at a location you've out-leveled gives a **2% cast-speed reduction per level above the requirement**. For example, fishing at Calm Pond (Lv1) with your character at Lv4 = 3 levels over = 6% faster casts.

Combined with rod, bait, and trophy bonuses, veteran anglers can squeeze a lot of speed out of otherwise slow locations.

### Level-up announcements

When you level up, the channel gets a public one-liner:

> ⭐ @Brom reached **Fishing Level 3**!

Your XP total is always visible in `/fish gear`.

## Fish Log

Every time you catch a species for the first time, it's added to your log. Subsequent catches update your records.

```
/fish log              # Overview across all locations
/fish log calm_pond    # Detailed species list for a specific spot
```

### What's tracked per species

- **Catch count**: total times you've caught this fish
- **Best length**: biggest specimen you've landed (in inches)
- **Best value**: highest coin payout you've gotten for this species
- **First caught**: timestamp of your very first catch
- **Last caught**: most recent catch

The overview view shows how many species you've discovered at each location (e.g. "3/5 species discovered"). The per-location view shows each species with your stats, plus "???" placeholders for species you haven't caught yet (with rarity hints).

### Legendaries in the log

Legendary catches record the **underlying species name** (e.g. "Phantom Koi"), not the unique character name (e.g. "Koi-san the Drowsy"). This is intentional — the log tracks species, and the trophy system counts toward location completion. The unique character is preserved in the legendary record separately.

## Trophies

Catch every **non-trash species** at a location to earn a **location trophy**. Trophies give a permanent **10% cast-speed reduction** at that location, on top of all other reductions.

```
/fish trophies
```

Shows per-location progress (species caught / species total), earned status, and missing species when you haven't completed a location yet.

### Trophy earning announcement

When you complete a location, the channel gets a public one-liner:

> 🏆 @Brom completed the **Calm Pond** collection! Trophy earned!

### Trophy math example

Calm Pond has 5 non-trash species (Bluegill, Sunfish, Silverscale Trout, Golden Perch, Phantom Koi). You need to catch all 5 to earn the trophy. The Phantom Koi is a legendary, which means you **must engage with active fishing** using Premium Bait — there's no way to earn Calm Pond's trophy purely through AFK.

The 10% speed bonus is multiplied against everything else, so over a long session at your trophy location, you'll bank noticeably more catches.

### Trophy completion is tracked in `/fish locations`

The locations list shows 🏆 next to any location where you've earned the trophy.

## Daily Digest Integration

Active fishing results are folded into the guild's daily digest (if enabled):
- Total fish caught across all players yesterday
- Most active angler (by catches)
- Biggest catch by length
- Most valuable catch

Only non-trash catches count toward digest stats.
