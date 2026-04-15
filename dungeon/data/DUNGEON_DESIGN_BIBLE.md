# Monster Mash — Dungeon Design Bible

A complete guide to creating new dungeons for the Monster Mash mini-game. Drop a `.yaml` file into `dungeon/data/dungeons/` and it will be automatically loaded.

---

## File Structure

Every dungeon is a single YAML file in `dungeon/data/dungeons/`. The filename (without `.yaml`) becomes the dungeon's internal key used for commands and DB references.

```
dungeon/data/dungeons/
  goblin_warrens.yaml      # key: "goblin_warrens"
  the_undercrypt.yaml       # key: "the_undercrypt"
```

---

## Root-Level Fields

```yaml
id: goblin_warrens                # Unique identifier (should match filename)
name: The Goblin Warrens          # Display name shown in embeds
description: "A network of..."    # Flavor text for the dungeon selection screen

floors:                           # Array of floor definitions (at least 1)
  - floor: 1
    ...
```

---

## Floor Definition

Each floor is a self-contained level with its own monsters, traps, treasure, and a boss at the end.

```yaml
- floor: 1                        # Floor number (sequential, starting at 1)
  theme: musty_tunnels             # Thematic identifier (flavor only, not parsed)
  rooms: [5, 6]                   # [min_rooms, max_rooms] before boss
  room_weights:                   # Weighted random room type selection
    combat: 50                    #   Combat encounter with a random floor monster
    treasure: 20                  #   Gold-only treasure room
    trap: 15                      #   DEX-check trap
    rest: 15                      #   Rest shrine (heals 30% max HP)
  rest_shrines: 1                 # Guaranteed rest shrines per floor (smart placement)
  treasure_tier: common           # Gold quality: common | uncommon | rare | epic
  monsters: [...]                 # Regular monsters encountered in combat rooms
  traps: [...]                    # Traps encountered in trap rooms
  boss:                           # Boss monster (always the last room)
    ...
```

### Room Generation Rules

- Room count is randomly chosen between `rooms[0]` and `rooms[1]`
- Each room type is picked by weighted random from `room_weights`
- Rest shrines are **never** placed as the first room
- If `rest_shrines: 1` and no rest room has appeared by the last procedural room, one is forced
- Extra rest rooms beyond the guaranteed count are converted to combat
- The boss room is always appended after all procedural rooms
- If `traps` is empty, trap rooms fall back to combat

### Treasure Tiers

| Tier       | Gold Range | Typical Floor |
|------------|-----------|---------------|
| `common`   | 3–12      | Floor 1       |
| `uncommon` | 8–20      | Floor 2       |
| `rare`     | 15–35     | Floor 3       |
| `epic`     | 25–60     | Floor 4+      |

---

## Monster Definition

Used in both the `monsters` array and the `boss` field.

```yaml
- id: goblin                       # Unique ID across ALL dungeons (used in bestiary)
  name: Goblin                     # Display name
  description: "A sneering..."     # Flavor text shown in combat embed
  hp: 12                           # Hit points
  defense: 0                       # Flat damage reduction vs player attacks
  attack_dice: "1d4"              # Damage dice (NdM format: 1d4, 1d6, 1d8, 1d10, 1d12)
  attack_bonus: 1                  # Added to each attack roll
  xp: 10                          # XP awarded on kill
  gold: [3, 8]                    # [min, max] gold drop
  ai:                              # Action weights (should sum to 100)
    attack: 60                     #   Standard attack
    heavy: 20                      #   Heavy attack (higher damage)
    defend: 20                     #   Defensive stance (halves incoming damage)
  loot:                            # Loot table (each entry rolled independently)
    - {item_id: health_potion, chance: 15}
    - {item_id: worm, chance: 20, type: cross_game_bait}
    - {item_id: Ember Salt, chance: 8, type: cross_game_ingredient, name: Ember Salt}
    - {item_id: shortsword_plus1, chance: 8, type: gear}
```

### Stat Guidelines by Floor Depth

| Floor | HP Range  | Defense | Attack Dice | Attack Bonus | XP Range  | Gold Range |
|-------|-----------|---------|-------------|--------------|-----------|------------|
| 1     | 6–15      | 0       | 1d4         | -1 to +1     | 5–15      | 1–10       |
| 2     | 8–20      | 0–1     | 1d6         | 0 to +2      | 10–20     | 3–15       |
| 3     | 14–30     | 1–2     | 1d6–1d8     | +2 to +3     | 20–25     | 8–20       |
| 4+    | 20–40     | 2–3     | 1d8–1d10    | +3 to +4     | 25–35     | 10–25      |

### Boss Stat Guidelines

Bosses should be significantly tougher than regular monsters on the same floor.

| Floor | HP Range  | Defense | Attack Dice | Attack Bonus | XP Range  | Gold Range  |
|-------|-----------|---------|-------------|--------------|-----------|-------------|
| 1     | 25–35     | 1–2     | 1d6         | +2           | 25–35     | 15–30       |
| 2     | 40–55     | 2–3     | 1d8         | +3           | 40–55     | 25–50       |
| 3     | 60–75     | 3–4     | 1d10        | +4           | 70–85     | 50–100      |
| 4+    | 80–100    | 4–5     | 1d10–1d12   | +4 to +5     | 90–120    | 75–150      |

### Monster AI Profiles

Mix and match these archetypes:

| Profile      | attack | heavy | defend | Best For              |
|-------------|--------|-------|--------|-----------------------|
| Berserker   | 50     | 50    | 0      | Beasts, mindless foes |
| Aggressive  | 60–70  | 20–30 | 10     | Wolves, spiders       |
| Balanced    | 50     | 30    | 20     | Trained fighters      |
| Cautious    | 40     | 25    | 35     | Smart bosses, mages   |
| Glass Cannon| 55     | 35    | 10     | Assassins, archers    |
| Tank        | 40     | 20    | 40     | Armored enemies       |

---

## Trap Definition

```yaml
- id: pit_trap                     # Unique ID
  name: Pit Trap                   # Display name
  damage: [3, 8]                  # [min, max] damage on failure
  dex_dc: 12                      # Difficulty class (player rolls d20 + DEX mod)
  flavor_success: "You spot..."   # Text shown on successful save
  flavor_fail: "The floor..."     # Text shown on failed save
```

### Trap DC Guidelines

| Floor | DC Range | Damage Range | Notes                        |
|-------|----------|-------------|------------------------------|
| 1     | 11–12    | 2–8         | Survivable even at level 1   |
| 2     | 12–14    | 3–10        | DEX investment starts paying  |
| 3     | 14–15    | 4–14        | Real threat without DEX       |
| 4+    | 15–17    | 6–18        | Punishing for dump-stat DEX   |

---

## Loot Tables

Every loot entry is rolled independently — a monster can drop multiple items. Each entry has:

```yaml
loot:
  - item_id: health_potion         # ID from items.yaml or gear.yaml
    chance: 15                     # Percent chance (1-100) per kill
    # Optional fields:
    type: gear                     # One of: gear, cross_game_bait, cross_game_ingredient
    name: Ember Salt               # Display name (required for cross_game_ingredient)
```

### Loot Types

| Type | Description | On Death | Example |
|------|-------------|----------|---------|
| *(omitted)* | Consumable item from `items.yaml` | **Lost** | `health_potion`, `smoke_bomb` |
| `gear` | Equipment from `gear.yaml` | **Lost** | `shortsword_plus1`, `leather_armor_plus1` |
| `cross_game_bait` | Fishing bait, awarded instantly | **Kept** | `worm`, `insect`, `shiny_lure` |
| `cross_game_ingredient` | Brewing ingredient, awarded instantly | **Kept** | `Ember Salt`, `Wraith Moss` |

Cross-game drops are awarded the moment they drop (not stored in the run's found items), so they survive death. This is intentional — the player earned the kill.

### Available Cross-Game Loot IDs

**Fishing Bait** (`type: cross_game_bait`):
| item_id | Name | Rarity Feel |
|---------|------|-------------|
| `worm` | Worm | Common — hand these out freely |
| `insect` | Insect | Uncommon — moderate drop rates |
| `shiny_lure` | Shiny Lure | Rare — boss-only or deep floors |
| `premium` | Premium Bait | Very rare — special rewards only |

**Brewing Ingredients** (`type: cross_game_ingredient`, must include `name` field):

*Free tier (common drops, 8-10% on regular monsters):*
| item_id / name | Tags |
|----------------|------|
| `Ember Salt` | Thermal / Volatile |
| `Moonpetal` | Luminous / Celestial |
| `Wraith Moss` | Spectral / Verdant |
| `Iron Root` | Calcified / Stabilizing |
| `Gloomcap` | Abyssal / Mutagenic |
| `Brimstone Dust` | Thermal / Corrosive |

*Uncommon tier (rare drops, 3-5% on deeper floors):*
| item_id / name | Tags |
|----------------|------|
| `Singing Quartz` | Resonant / Calcified |
| `Voidbloom` | Abyssal / Verdant |
| `Ashenworm Silk` | Thermal / Stabilizing |
| `Ghostlight Oil` | Spectral / Luminous |
| `Rot Blossom` | Corrosive / Verdant |
| `Starite Shard` | Celestial / Resonant |
| `Flickerstone` | Volatile / Luminous |
| `Marshglow Lichen` | Verdant / Luminous |
| `Echo Bone` | Resonant / Spectral |
| `Nullite Powder` | Stabilizing / Abyssal |
| `Tremor Grub` | Mutagenic / Resonant |
| `Duskfen Mud` | Abyssal / Calcified |
| `Scorchcap Spore` | Thermal / Mutagenic |
| `Prism Beetle Shell` | Luminous / Calcified |
| `Coilweed` | Stabilizing / Verdant |

*Rare tier (very rare drops, boss-only, 10-15%):*
| item_id / name | Tags |
|----------------|------|
| `Wyrm's Tear` | Volatile / Celestial |
| `Hollow King's Sigh` | Spectral / Abyssal |
| `Titan Marrow` | Calcified / Mutagenic |
| `Phoenix Cinder` | Thermal / Celestial |

Pick ingredients thematically — undead dungeons should drop `Spectral` and `Abyssal` ingredients, fire dungeons should drop `Thermal` and `Volatile`, etc.

### Available Gear IDs for Loot Tables

**Enchanted Weapons** (`type: gear`, loot-only):
| item_id | Dice | Bonus | STR Req | Rarity |
|---------|------|-------|---------|--------|
| `shortsword_plus1` | 1d6 | +1 | 11 | uncommon |
| `shortsword_plus2` | 1d6 | +2 | 13 | rare |
| `shortsword_plus3` | 1d6 | +3 | 15 | epic |
| `handaxe_plus1` | 1d6 | +1 | 11 | uncommon |
| `handaxe_plus2` | 1d6 | +2 | 13 | rare |
| `handaxe_plus3` | 1d6 | +3 | 15 | epic |
| `longsword_plus1` | 1d8 | +1 | 11 | uncommon |
| `longsword_plus2` | 1d8 | +2 | 13 | rare |
| `longsword_plus3` | 1d8 | +3 | 15 | epic |
| `war_hammer_plus1` | 1d8 | +2 | 11 | uncommon |
| `war_hammer_plus2` | 1d8 | +3 | 13 | rare |
| `war_hammer_plus3` | 1d8 | +4 | 15 | epic |
| `battle_axe_plus1` | 1d8 | +1 | 11 | uncommon |
| `battle_axe_plus2` | 1d8 | +2 | 13 | rare |
| `battle_axe_plus3` | 1d8 | +3 | 15 | epic |
| `greataxe_plus1` | 1d10 | +2 | 11 | rare |
| `greataxe_plus2` | 1d10 | +3 | 13 | rare |
| `greataxe_plus3` | 1d10 | +4 | 15 | epic |

**Enchanted Armors** (`type: gear`, loot-only):
| item_id | Defense | DEX Req | Rarity |
|---------|---------|---------|--------|
| `leather_armor_plus1` | 3 | 11 | uncommon |
| `leather_armor_plus2` | 4 | 13 | rare |
| `leather_armor_plus3` | 5 | 15 | epic |
| `chain_shirt_plus1` | 4 | 11 | uncommon |
| `chain_shirt_plus2` | 5 | 13 | rare |
| `chain_shirt_plus3` | 6 | 15 | epic |
| `scale_mail_plus1` | 5 | 11 | uncommon |
| `scale_mail_plus2` | 6 | 13 | rare |
| `scale_mail_plus3` | 7 | 15 | epic |
| `half_plate_plus1` | 6 | 11 | rare |
| `half_plate_plus2` | 7 | 13 | rare |
| `half_plate_plus3` | 8 | 15 | epic |

### Available Consumables for Loot Tables

From `items.yaml` (no `type` field needed):
| item_id | Effect | Typical Chance |
|---------|--------|---------------|
| `health_potion` | Heal 8 HP | 15-25% |
| `greater_health_potion` | Heal 20 HP | Boss-only, 30-50% |
| `antidote` | Cure poison | 10-15% (if dungeon has poison) |
| `smoke_bomb` | Guaranteed flee | 10-15% (assassin/rogue types) |

### Loot Philosophy

**Regular monsters** should drop:
- Consumables at 15-25% (one type per monster, maybe two)
- Common bait at 20-25% (worm)
- Uncommon bait at 10-15% on deeper floors (insect)
- Free-tier ingredients at 8-10% (pick 2-3 thematic ones)
- Uncommon ingredients at 3-5% on floor 2+ (pick 1-2)
- **No gear drops** from regular monsters

**Bosses** should drop:
- One guaranteed consumable or base gear (chance: 100)
- Bait at higher rates (25-40%)
- Ingredients at higher rates (15-25%)
- +1 enchanted gear at 8-12% (any boss)
- +2 enchanted gear at 4-5% (floor 3+ bosses only)
- +3 gear is NOT in any loot table yet (reserved for future content)
- Rare-tier ingredients at 10-15% (final boss only)

---

## Enchanted Gear Drop Restrictions

| Enchantment | Minimum Floor Boss | Drop Rate |
|-------------|-------------------|-----------|
| +1 weapon/armor | Any boss | 8-12% |
| +2 weapon/armor | Floor 3+ boss | 4-5% |
| +3 weapon/armor | Not yet in loot tables | Reserved |

---

## Design Checklist for New Dungeons

- [ ] Unique `id` field matching the filename
- [ ] All monster IDs are globally unique (check existing dungeons)
- [ ] At least 3 monsters per floor (variety in random encounters)
- [ ] At least 2 traps per floor
- [ ] Every floor has a boss
- [ ] Boss is meaningfully harder than floor regulars
- [ ] Loot tables include cross-game drops (bait + ingredients)
- [ ] Ingredient choices are thematically appropriate
- [ ] Enchanted gear only on bosses, respecting floor restrictions
- [ ] Difficulty escalates floor-over-floor
- [ ] `room_weights` add up to 100
- [ ] `ai` weights for each monster add up to 100
- [ ] `rest_shrines: 1` on every floor (players need a heal opportunity)
- [ ] `treasure_tier` escalates with floor depth
- [ ] Flavor text on all monsters, traps, success/fail messages
- [ ] Gold rewards scale with floor difficulty

---

## Example: Minimal 2-Floor Dungeon

```yaml
id: example_dungeon
name: Example Dungeon
description: "A brief example dungeon."

floors:
  - floor: 1
    theme: entrance
    rooms: [4, 5]
    room_weights:
      combat: 50
      treasure: 20
      trap: 15
      rest: 15
    monsters:
      - id: example_rat
        name: Giant Rat
        description: "A very big rat."
        hp: 8
        defense: 0
        attack_dice: "1d4"
        attack_bonus: 0
        xp: 5
        gold: [1, 4]
        ai: {attack: 80, heavy: 20}
        loot:
          - {item_id: worm, chance: 25, type: cross_game_bait}
          - {item_id: Ember Salt, chance: 8, type: cross_game_ingredient, name: Ember Salt}
    traps:
      - id: example_pit
        name: Pit Trap
        damage: [2, 6]
        dex_dc: 11
        flavor_success: "You jump over the pit."
        flavor_fail: "You fall into the pit!"
    treasure_tier: common
    rest_shrines: 1
    boss:
      id: example_boss
      name: Rat King
      description: "A massive rat wearing a tiny crown."
      hp: 30
      defense: 1
      attack_dice: "1d6"
      attack_bonus: 2
      xp: 30
      gold: [10, 20]
      ai: {attack: 50, heavy: 30, defend: 20}
      loot:
        - {item_id: health_potion, chance: 100}
        - {item_id: worm, chance: 35, type: cross_game_bait}
        - {item_id: shortsword_plus1, chance: 8, type: gear}
```

---

## Player Power Reference

Use this to sanity-check your numbers against what players can actually do.

| Level | Stat Points | Typical STR | Weapon | Avg Player Damage/Turn | Typical HP |
|-------|-------------|-------------|--------|----------------------|------------|
| 1     | 0 extra     | 10 (+0)     | Fists (1d4) | 2–3 | 20 |
| 2     | 1 extra     | 10–11 (+0)  | Rusty Dagger (1d4) | 2–4 | 20–22 |
| 3     | 2 extra     | 11–12 (+0-1)| Shortsword (1d6) | 3–6 | 22–24 |
| 5     | 4 extra     | 12–13 (+1)  | Longsword (1d8) or +1 | 5–9 | 24–28 |
| 7     | 6 extra     | 13–14 (+1-2)| +1 or +2 weapon | 6–12 | 26–32 |
| 10    | 9 extra     | 14–16 (+2-3)| +2 weapon | 8–15 | 28–38 |

**Death penalty:** 50% of run gold lost, ALL found gear lost. XP and cross-game drops are always kept.
