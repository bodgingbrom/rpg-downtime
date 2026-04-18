# Getting Started

Welcome to Lazy Lures. Here's the shortest path from zero to a full bait box.

## Step 1: Get Some Bait

Every session burns one bait per catch. Start with the cheap stuff.

```
/fish buy-bait type: Worm quantity: 50
```

Worms cost 2 coins each, so 50 worms = 100 coins. That's roughly half your starting wallet. Plenty for your first few sessions.

## Step 2: Start an AFK Session

Your only unlocked location to begin with is **Calm Pond** (the Lv1 gated spots unlock as you level up).

```
/fish start location: Calm Pond
```

The bot will reserve all your worms for the session, announce publicly that you've started fishing, and schedule your first catch. You'll get an ephemeral "Started fishing at Calm Pond" message.

## Step 3: Wait

Catches happen every ~15 minutes with basic gear at Calm Pond. They happen in the background — you don't need to do anything. Each catch:

- Credits coins to your wallet immediately
- Awards XP (more for rarer fish)
- Adds to your fish log
- Consumes one bait

Your session ends automatically when bait runs out, or you can end it early with:

```
/fish stop
```

Any unused bait is refunded. Unlike wet bait in the real world, it stores forever.

## Step 4: Check Your Haul

Anytime during or after a session:

```
/fish gear       # rod, bait inventory, XP/level
/fish log        # species caught across all locations
/fish status     # your current session
```

## How Coins Flow

| Activity | Approximate Yield |
|----------|-------------------|
| AFK at Calm Pond with basic gear | ~50 coins/day net |
| Oak Rod + Insects @ Calm Pond | ~75 coins/day net |
| Steel Rod + Shiny Lure @ River Rapids | ~120 coins/day net |
| Master Rod + Premium @ Deep Lake | ~180 coins/day net |

The math:
- **Gross** = catches × average fish value (varies by location)
- **Net** = gross − (bait used × bait cost)
- Trash pulls are a net loss, so reducing trash (via better rods) matters

Fishing is meant to be **supplemental income** — a steady trickle that tops up your wallet while you focus on other mini-games. It's not a path to becoming rich quickly.

## Try Active Mode

Once you've got a feel for AFK, try the interactive version:

```
/fish active location: Calm Pond
```

Bites come much faster (30-90 sec) and each one is a little moment — a fish whispering a secret, a one-word vibe check, a haiku, or a conversation with a legendary character. See [Active Mode](active-mode.md) for the full rundown.

## Tips

- **Buy more bait than you think you need** — a session ending early because you ran out is a pain
- **Use `/fish status`** to see your session's progress and time until the next catch
- **Don't skip trophies** — completing a location grants a permanent 10% cast-speed bonus there
- **Watch your level** — over-leveling a location gives a small speed bonus (2% per level above the requirement)
- **Notifications** — toggle DMs with `/fish notify` if you want a ping for each catch
