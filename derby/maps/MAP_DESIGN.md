# Race Map Design Guide

This document describes how to create race maps for Downtime Derby. Each map is a YAML file in this folder that defines a track racers will run through segment by segment.

## File Format

```yaml
name: Track Name
theme: frozen          # flavor word used for commentary (frozen, desert, mountain, urban, etc.)
description: A short description of the track for display to players.
segments:
  - type: straight     # segment type (see below)
    distance: 2        # 1 = short, 2 = medium, 3 = long
    description: The frozen straightaway   # flavor text for this segment
```

## Segment Types

Each segment type determines how much each racer stat matters. Weights range from 0.3 (barely matters) to 1.0 (dominant factor).

| Type | Speed | Cornering | Stamina | Best For |
|------|-------|-----------|---------|----------|
| `straight` | **1.0** | 0.3 | 0.5 | Speed-focused racers |
| `corner` | 0.3 | **1.0** | 0.5 | Agile/cornering racers |
| `climb` | 0.5 | 0.3 | **1.0** | High-stamina racers |
| `descent` | 0.8 | 0.7 | 0.3 | Balanced, slightly speed-favoring |
| `hazard` | 0.4 | 0.6 | 0.8 | Steady, high-stamina racers |

## Distance Scale

Distance affects how much the segment amplifies stat differences and how much stamina matters:

- **1 (short)**: Quick segment, small impact. Factor: 1.0x
- **2 (medium)**: Standard segment. Factor: 1.2x
- **3 (long)**: Extended segment, big stamina drain. Factor: 1.4x

Longer segments make the stat weights matter more AND increase fatigue for low-stamina racers in later segments.

## Design Guidelines

### Balanced Maps (5-7 segments)
- Mix segment types so no single stat dominates
- Include at least one straight and one corner
- Use distance 2 for most segments
- Example: straight(2), corner(1), climb(2), corner(2), straight(1)

### Themed Maps
- **Speed track**: Mostly straights with high distances. Fast racers dominate.
- **Technical track**: Lots of corners and hazards. Cornering and stamina matter.
- **Endurance track**: Long climbs and high distances. Stamina is king.
- **Sprint track**: All short distances (1). Reduces fatigue, favors raw stats.

### Tips
- 5-8 segments is the sweet spot. Fewer feels rushed; more drags out commentary.
- A single distance-3 climb late in the race creates drama (tired racers fade).
- Hazard segments add unpredictability since no single stat dominates them.
- Descriptions should be evocative — they feed into race commentary.
- Theme is just a flavor word. Use anything: "haunted", "volcanic", "underwater", etc.

## Temperament Interactions

Racers have temperaments that modify their stats before the race:

| Temperament | Buffed Stat | Nerfed Stat |
|-------------|-------------|-------------|
| Agile | Speed +10% | Stamina -10% |
| Reckless | Speed +10% | Cornering -10% |
| Tactical | Cornering +10% | Speed -10% |
| Burly | Stamina +10% | Cornering -10% |
| Steady | Stamina +10% | Speed -10% |
| Sharpshift | Cornering +10% | Stamina -10% |
| Quirky | No change | No change |

When designing maps, consider how temperaments interact with segment types. A corner-heavy frozen track would favor Tactical and Sharpshift racers while punishing Reckless and Burly ones.
