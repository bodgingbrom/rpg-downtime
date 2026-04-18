# Lazy Lures

Lazy Lures is a fishing mini-game with two modes — one passive, one strange.

- **AFK Fishing** (`/fish start`) is exactly what it sounds like. Commit your bait, walk away, come back to coins. Catches happen every 10-35 minutes on a timer.
- **Active Fishing** (`/fish active`) is the weird one. Bites come every 30-90 seconds and each rarity is a different little interaction — a whispered secret, a one-word vibe match, a haiku you finish, or a conversation with a fish that remembers you.

The two modes share your bait, your gear, your XP, and your fish log. You just can't run both at the same time.

## How It Works

1. **Buy bait** with `/fish buy-bait` (worms are cheap, premium bait is slow to pay off but catches bigger stuff)
2. **Start a session** with `/fish start <location>` or `/fish active <location>`
3. **Stop early** with `/fish stop` — unused bait is refunded
4. **Check progress** with `/fish gear`, `/fish log`, or `/fish trophies`
5. **Upgrade your rod** with `/fish upgrade-rod` once you've saved up

## Pages

| Page | Description |
|------|-------------|
| [Getting Started](getting-started.md) | Your first cast, how bait works, earning your first coins |
| [Gear & Bait](gear.md) | All four rod tiers, all four bait types, cast speed math |
| [Locations](locations.md) | Calm Pond, River Rapids, Deep Lake — the fish at each |
| [Progression](progression.md) | XP, levels, fish log, trophies |
| [Active Mode](active-mode.md) | Whispers, vibe checks, haikus, and legendary encounters |

## Commands at a Glance

| Command | Description |
|---------|-------------|
| `/fish start <location> [bait]` | Start an AFK session (default 10-35 min per catch) |
| `/fish active <location> [bait]` | Start an active session (30-90 sec per bite, interactive) |
| `/fish stop` | End your current session and refund unused bait |
| `/fish status` | Check your current session |
| `/fish shop` | View bait prices and the next rod upgrade |
| `/fish buy-bait <type> <amount>` | Purchase bait |
| `/fish upgrade-rod` | Buy the next rod tier |
| `/fish gear` | Your rod, bait inventory, skill level, XP |
| `/fish locations` | All fishing spots, unlock status, trophies |
| `/fish log [location]` | Species you've caught, records, missing species |
| `/fish trophies` | Per-location completion and bonuses |
| `/fish haiku mine` | Your saved haikus from rare active catches |
| `/fish haiku random` | Post a random guild haiku in the channel |
| `/fish notify` | Toggle DM alerts for each catch |
