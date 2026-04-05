# Breeding

Breeding lets you combine two of your racers to produce a foal that can inherit the best traits. It's the primary way to create high-stat racers that can compete in upper-tier tournaments.

## Requirements

To breed, you need:

- **One male (sire) and one female (dam)** -- you must own both
- **Both racers have 5+ races completed** (configurable by admin)
- **Neither racer is on breeding cooldown**
- **The dam has fewer than 3 foals** (configurable by admin)
- **You have an open stable slot** for the foal
- **25 coins** breeding fee (configurable by admin)
- Neither racer can be retired

Use `/stable breed <male> <female>` to breed.

## Cooldown

After breeding, **both parents go on a 6-race cooldown** (configurable). This means they need to complete 6 more races before they can breed again. The cooldown ticks down by 1 after every race they participate in.

Tournament wins reset the winner's breed cooldown to 0.

## Foal Stats

### Stat Inheritance
One stat is randomly chosen to be **inherited** -- the foal's value for that stat is a random number between the sire's and dam's values. The other two stats are completely random (0-31).

**Example:** If the sire has 25 Speed and the dam has 19 Speed, and Speed is the inherited stat, the foal gets a random Speed between 19 and 25. Cornering and Stamina are rolled fresh (0-31).

### Temperament
- **10% chance** of a random mutation (any of the 7 temperaments)
- **75% chance** to inherit the sire's temperament
- **25% chance** to inherit the dam's temperament (if no mutation)

### Gender
50/50 male or female.

### Career Length
The foal's career length is the **average of both parents' career lengths, plus or minus up to 5 races**, clamped between 25 and 40. Peak period is ~60% of career length.

### Rank
The foal's rank is calculated from its base stats at creation and is **permanent** -- it never changes even if you train the foal's stats higher.

## Training Requirement

Foals (racers with a sire) must complete a minimum number of **training sessions** (default: 5) before they can enter races. Your stable view shows training progress for foals that haven't met the threshold yet.

## Breeding Strategy

- **Target high inherited stats** -- if both parents have 28+ in one stat, the foal is guaranteed at least 28 in that stat
- **The other two stats are random** -- you might need to breed multiple foals to get a good roll
- **Females are more valuable** because they can breed AND their foals can breed
- **Males are reusable** -- they have no foal limit, only a cooldown
- **The dam's foal limit (3) is permanent** -- choose breeding partners carefully
- **Higher-rank foals qualify for bigger tournament brackets** with better prizes
