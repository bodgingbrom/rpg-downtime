# Monster Mash — Dungeon Overhaul Design

This is the canonical design document for the next-generation dungeon
system ("v2"). It captures the design decisions made during planning so
that engineering and writing agents can build against a stable spec
without re-deriving philosophy from scratch.

> **Status:** Pre-implementation. The current dungeon system (v1) ships
> alongside v2 during development; v2 dungeons are admin-gated until the
> overhaul is content-complete, then we cut over and remove v1.

---

## 1. Vision

The current dungeon game is *click-through-attack*. There are stats,
races, and gear, but the only meaningful decision per room is "do I
have HP to keep going." That's a resource-management game pretending
to be an RPG.

The overhaul makes Monster Mash an **RPG-lite** — a slower,
atmosphere-heavy crawler where rooms are spaces to explore, not
encounters to resolve. **Depth over speed.** A delve takes 15-25 minutes
of focused play (longer if the player thoroughly searches everything).
Players can pause at any tick and resume hours or days later without
losing context.

**Pillars:**

1. **Ambiguity at the threshold.** When you arrive at a room, you don't
   know what's there. Information-gathering is the first action.
2. **Decisions beyond fight/run.** Look, Listen, Investigate, retreat,
   pick which exit. Most rooms have *several* meaningful choices.
3. **Time is risk.** Every action you take has a chance of provoking a
   wandering encounter, an ambush, or a notice-roll from sleeping
   monsters. Lingering is dangerous.
4. **Surprise and reward.** Rooms can be empty, dangerous, rewarding,
   or all three. Searching is gambled work; sometimes you find
   nothing, sometimes a fragment of a forgotten story.
5. **The dungeon feels authored.** Procedurally assembled rooms drawn
   from a hand-authored pool, narrated by an LLM that *paints
   atmosphere but never invents game state*.

**What we're NOT building:** A "quick mode" escape hatch. The current
v1 game IS the quick mode. If players want fast click-fests we'll ship
a different mini-game (a battle arena) eventually. The RPG should feel
like an old-school RPG. Clicking attack repeatedly is not a "role."

---

## 2. The Room Loop

The atomic unit of play is the room interaction. Combat is *embedded
inside* the room loop, not parallel to it.

### Entering a room

The player picks an exit and steps into the next room. They are
presented with:

- A short prose description (3-4 lines) — LLM-rendered or authored
  fallback. Mentions plain-sight features and ambient details.
- A list of action buttons derived **strictly from the room's authored
  data**:
  - **Look Around** *(1 tick)* — surfaces concealed details via passive
    perception (DEX + racial bonuses + future gear).
  - **Investigate \<feature\>** *(2-3 ticks)* — focused search of one
    interactable feature. Each visible/concealed feature with hidden
    contents becomes its own button.
  - **Listen** *(1 tick)* — atmospheric awareness of nearby threats.
  - **Move on \<exit\>** — leave through a discovered exit. Forfeits
    anything not yet found.
  - **Context-sensitive**: Pray (shrine), Rest (safe spot), Read
    (inscription), Force (stuck door), etc. Authored per room.

### Visibility tiers (per feature)

| Tier | Surfaced when | Use for |
|------|---------------|---------|
| `passive` | On entry, in the description | Plot-relevant or unmissable details ("a body slumps near the wall"). No button needed. |
| `visible` | On entry, as a button | Obvious furniture/objects: a coffin, a shrine, a fountain. Player decides whether to Investigate. |
| `concealed` | After Look Around (passive perception roll vs `perception_dc`) | Subtle details: scratched message, draft from a hidden crack, unusual stain. |
| `secret` | After Investigating a related feature, OR specific conditions | Hidden levers, false doors, things that need a key. Authored via `revealed_by`. |

### Investigate granularity

When the player clicks Investigate, they pick *which* feature to
investigate from a list. Exception: if there's only one investigable
feature, the system picks it for them. Don't make the player click
twice when there's no decision to make.

### Tick consequences

Every action is "1 tick" or "N ticks." Each tick rolls against the
floor's danger table. Possible outcomes:

- **Nothing** — most common, especially early.
- **Wandering encounter** — combat with a monster pulled from the
  floor's roving pool. Generic, less polished than authored encounters.
- **Authored monster wakes / notices you** — if the room contains a
  sleeping/passive enemy, lingering wakes it. This is the *avoidable*
  encounter pattern: you can sneak through, but searching means
  fighting.
- **Ambush triggers** — if the room has an armed ambush, it fires
  (free first action for the monster). Combat starts.
- **Trap fires** — same as above for traps.

Different actions have different tick cost (see action list above) AND
different relative noise. Investigating a corpse in a crypt is louder
than glancing around the room. Authors can override per-action noise
on a per-room or per-feature basis (e.g., "investigating the magical
inscription doubles the danger roll because reading it aloud
*summons*"). Reasonable defaults exist; overrides are rare and
flavor-driven.

The danger meter is **never shown numerically**. Players get *tells*:

- *"You hear shuffling somewhere close."* (wandering encounter
  imminent)
- *"The ogre stirs in its sleep."* (authored monster about to wake)
- *"A faint scrape from behind the bench."* (ambush armed)

These tells are derived from the meter and the room state, but the
prose is authored. The character can *plausibly sense* what they're
warned about; we don't expose system meta.

### Combat interrupts the action mid-flow

When a wandering monster (or a triggered ambush) starts combat
mid-search, the action the player was taking is **cancelled, not
completed**. Combat resolves. When combat ends, the player is back in
the room with the same buttons available (the search ungone). The room
remembers what's already been searched, so completed searches don't
reset.

---

## 3. The Floor as a Graph

Floors are **procedurally-assembled graphs** with hand-authored
**anchor rooms** at fixed positions.

### Layout philosophy

- **Linear with branches** for v2 launch. Most floors are a main path
  with 1-3 short side branches. Each branch is 1-2 rooms long, often
  ending in a dead-end with a reward (shrine, treasure, lore room).
- The boss is always at the deepest node.
- Anchor rooms (boss room, signature plot rooms) are placed first by
  the generator; pool rooms fill the rest.
- The generator must guarantee connectivity — every room reachable
  from the entrance.

### Procedural assembly with anchors

Each dungeon's YAML defines:

- A **room pool**: 15-20 rooms per floor, each with weight and variants.
- **Anchors**: 1-3 rooms per floor that always appear at specific
  positions (entrance, boss, mid-floor signature room).
- **Layout config**: rooms-per-run range, branch count, branch length.

A delve generates a fresh graph each time. Anchor rooms appear at
their authored positions; pool rooms are drawn (without replacement)
to fill the rest.

### Backtracking

The map remembers visited rooms and unexplored exits. Players can
return to earlier rooms — for example, after finding a key in room 5,
they can backtrack to room 2 to open a previously-locked door. This is
a meaningful design tool, not a shortcut around content.

### Map render (fog-of-war)

The map shows discovered rooms only. Rendered as a small Unicode
grid in the combat embed:

```
🟫━🚪━🟫
 ┃     
🟫━🟫━🟥
       ┃
       ⬛
```

- `🟫` cleared room
- `🟥` current room
- `⬛` known-but-unentered (a discovered exit that hasn't been taken)
- `🚪` doorway / connector
- `━`, `┃` explicit connectors between rooms (so non-grid graphs render correctly)
- Connector glyphs need monospace, so the map lives in a code block in the embed

The exact glyph palette will be tuned during PR2 (map render). Vertical
connections in particular need attention — emoji widths can be
inconsistent.

The map is a render of `floor_state_json`, not a separate source of
truth.

---

## 4. State Persistence

Two separate state blobs on `DungeonRun`:

### `combat_state_json` (existing, v1)

Lifecycle: initialized when combat starts, cleared when combat ends.
Holds turn counter, phase index, picked variant, picked description,
active effects, summoner adds. **No change in v2.**

### `floor_state_json` (new in v2)

Lifecycle: initialized when the player enters a floor, persists across
the entire floor traversal, cleared when the player descends to the
next floor or completes the dungeon.

Holds:

```json
{
  "graph": {
    "rooms": [
      {"id": "r0", "room_def_id": "entrance_chamber", "variant": "default", "exits": ["r1", "r2"]},
      ...
    ],
    "current": "r3"
  },
  "discovered": ["r0", "r1", "r2", "r3"],
  "room_states": {
    "r0": {"searched": ["bookshelf"], "found": [], "ambush_triggered": false},
    "r1": {"searched": [], "found": ["loose_stone"], "ambush_triggered": true},
    ...
  },
  "danger": {
    "ticks_since_last_event": 3,
    "armed_ambushes": ["r4"],
    "next_wandering_pool": ["bone_rat", "goblin_archer"]
  },
  "round_state": "exploring"
}
```

**Every player action commits to DB.** Tick-level actions (Look,
Investigate) write back to floor_state_json before responding to the
player. There are no in-memory game states that aren't durable.

### Resume behavior

The player can leave at any tick. When they return:

- The resume embed renders the **current room** with full context: its
  description, what's been searched, what's been found here, which
  exits are known, which buttons are currently available. The map
  renders below.
- If combat was ongoing when they paused, combat resumes intact.
- If they paused mid-action (between button click and result), they
  see the *state before the action* — they need to re-click. Don't
  silently resume mid-tick; the player needs to consciously continue.

### No timers

No wall-clock timers expire anything. The danger meter advances on
**player action**, never on real time. A player who walks away mid-room
returns to the same danger level they left at.

---

## 5. Authoring Schema (YAML)

The room is the authoring unit. Rooms live in dungeon files alongside
the existing `floors`/`monsters`/`bosses` structure, but the floor's
`monsters` array is replaced by a **room pool** plus **anchors**.

### Sketch

```yaml
floors:
  - floor: 1
    theme: the_margins
    background: "..."
    layout:
      rooms_per_run: [8, 10]
      branches: [1, 3]
      branch_length: [1, 2]
    anchors:
      - {position: entrance, room_id: pencil_alcove}
      - {position: boss, room_id: scale_bar_chamber}
    room_pool:
      - id: pencil_alcove
        weight: 100   # 100 = always available; lower weights = rarer
        # variants: see below
        # features, exits, ambient_pool, etc.

      - id: drafting_table_room
        weight: 80
        variants:
          - {key: empty, weight: 60}
          - {key: scribbled_notes, weight: 35, hidden_features: [...]}
          - {key: ink_specter, weight: 5, ambush: ink_specter}
        ...
```

### A room definition

```yaml
- id: drafting_table_room
  weight: 80
  description_pool:
    - "A long drafting table runs the length of the room, papers scattered as if abandoned mid-thought."
    - "Drafting tables, ink-stained and disused, line one wall."
  ambient_pool:        # 0-2 picked per render; LLM may elaborate
    - "A faint scratching sound, as if pen on paper, drifts from nowhere."
    - "The smell of old ink hangs thick."
    - "A draft stirs the loose pages."
  features:
    - id: drafting_table
      name: "drafting table"
      visibility: visible
      investigate_label: "Examine the drafting table"
      noise: 1               # tick danger weight; 1 = normal
      content:               # what investigate reveals (may be empty for fluff features)
        - {type: lore_fragment, fragment_id: 7, perception_dc: 0}
        - {type: gold, amount: [3, 8], chance: 0.4}
    - id: scratched_initials
      name: "scratched initials"
      visibility: concealed
      perception_dc: 12
      investigate_label: "Read the initials"
      content:
        - {type: lore_fragment, fragment_id: 8}
    - id: hidden_compartment
      name: "hidden compartment"
      visibility: secret
      revealed_by: drafting_table     # revealed by Investigate of drafting_table
      content:
        - {type: gear_drop, item_id: shortsword_plus1}
  exits:
    - {id: north, label: "north passage", to_anchor: scale_bar_chamber}
    - {id: east, label: "east doorway"}
  ambush:
    armed: false                      # default; some variants set true
    creature: ink_specter
    flavor: "An inkwash specter pours itself out from beneath the table!"
  variants:                           # rolled at room-pick time; override fields above
    - key: default
      weight: 60
    - key: scribbled_notes
      weight: 35
      features_add:
        - id: ink_pot
          name: "broken ink pot"
          visibility: visible
          investigate_label: "Inspect the ink pot"
          content: [{type: lore_fragment, fragment_id: 9}]
    - key: ink_specter
      weight: 5
      ambush: {armed: true}
```

### Field reference

| Field | Required | Notes |
|-------|----------|-------|
| `id` | yes | Unique within dungeon |
| `weight` | yes | Probability of inclusion in a run; 100 = always |
| `description_pool` | yes | At least one entry; LLM picks one and may elaborate |
| `ambient_pool` | optional | Atmospheric details; LLM may pick 0-2 |
| `features` | optional | Interactable things; each becomes a button when surfaced |
| `exits` | yes | At least one exit (the boss room is the exception — it has no forward exit, only "Move on" back to entrance after victory) |
| `ambush` | optional | If armed, fires on first action in the room |
| `variants` | optional | If present, exactly one is picked at room-pick time. Variants can `_add` to the room or override fields directly. |

### Ambient vs feature: the critical distinction

**Ambient details** (in `ambient_pool` and prose) are *flavor only*.
They never have buttons. The player cannot interact with them. The LLM
can mention them freely.

**Features** (in `features`) are *interactable*. Each becomes a
button. The LLM may reference them by their authored `name`.

The LLM is told this constraint explicitly in its system prompt. If
it's tempted to mention something interactable that isn't authored, it
must rephrase as ambient or omit.

---

## 6. The LLM DM

### Allowed scope

- **Atmospheric paint.** Render authored facts in the dungeon's
  `tone` and `style_notes` voice. Add sensory and aesthetic detail to
  things the player encounters.
- **Reactive narration.** Narrate the *outcome* of player actions
  (successful searches, failed searches, found items, fired traps).
  The outcome itself is decided by code; the LLM is the prose layer.
- **Authored-thing elaboration.** Given an authored item ("a bronze
  key"), the LLM may add tasteful detail ("tarnished, edges worn
  smooth, the head shaped like an oak leaf"). This elaboration is
  generated **once at item-instance creation** and persists with the
  player's instance for the rest of the run. Re-renders of the same
  item don't re-roll its description.

### Disallowed scope

- **Inventing interactable things.** The LLM cannot mention objects,
  exits, monsters, or anything the player could attempt to click. If
  the YAML has no chest, the LLM may not write "an open chest sits
  beside the brazier."
- **Deciding combat outcomes.** All HP, damage, and kill resolution
  is code. The LLM only narrates the result.
- **Inventing rewards.** Rewards are authored. The LLM describes
  found items but does not decide what's found.

### System prompt skeleton

The LLM's per-call system prompt includes:

- The dungeon's `background` block (tone, lore, dm_hooks,
  style_notes) — already authored in v1.
- The current room's authored description and feature list (via tool
  call or context).
- The action being narrated (entering, search-result, combat-line).
- The hard rules: "may not invent things; must keep authored names
  recognizable; may add sensory detail."

The dungeon's `background` is **prefix-cached** so cost per call is
low. We're already paying the system-prompt cost once; the marginal
per-room cost is small.

### Graceful fallback

The LLM is **mandatory enhancement, not mandatory infrastructure**.
Every room must have an authored fallback in `description_pool` that
plays cleanly without LLM elaboration. If the API is unreachable,
slow, or rate-limited, the system uses authored prose directly.
Players experience a less-decorated dungeon, but a fully playable one.

Long-term: the bot is expected to migrate from the Anthropic API to
a self-hosted local LLM. The interface should not assume a specific
provider; abstract over the LLM call so we can swap backends.

---

## 7. Lore Fragments — the Long-Tail Goal

Each dungeon has **12-20 lore fragments** numbered 1..N, telling a
story that only fully resolves once all are collected.

### Authoring rules

- Each fragment is short (1-3 paragraphs).
- **Each fragment must be readable in isolation** — it should make
  some kind of sense even if found alone.
- **Fragments enrich each other.** Fragment 17 referring to events
  hinted in fragment 5 is a *reward*: the player who finds 17 first
  reads it confused, then finds 5 later and gets the *aha*.
- **Numbering implies a single intended ordering** (the "book"). The
  player may discover fragments out of order — that's the design.
- A few fragments may have **dependencies**: fragment 11 only spawns
  in a room if fragment 10 has been collected, or fragment 7 only
  spawns adjacent to a shrine. Use sparingly — most fragments should
  be findable independently.
- **No fragment is gated behind RNG-only.** A rare-drop fragment is
  acceptable, but it must not be the *last* one a player needs. Either
  it's an early/mid fragment (so missing it just delays completion),
  or there's a pity-timer on the rare drop.

### Display

`/dungeon lore <dungeon>` renders the player's collection as a "book":

```
─── The Cartographer's Folly: Alaric's Journals ───

[1] On the matter of latitude, my apprentices remain
    insufficient. The world refuses to hold still long
    enough to be measured truly...

[2] ░░░ unread ░░░

[3] Today, by candlelight, I record what the priests
    forbid. The maps obey if drawn with conviction...

[4] ░░░ unread ░░░
[5] ░░░ unread ░░░

[6] Margaret has not returned from the survey. I told
    her not to draw the eastern hills. She drew them...

──────── 3 / 18 fragments ────────
```

Found fragments display in full prose. Unfound show as `░░░ unread ░░░`
or similar visual gap. The collection feels like a real book
being assembled, not a checklist.

### Persistence

Lore fragments persist on the player profile, **across delves and
across deaths.** Once you've found fragment 7 of the Folly, you keep
it forever. Lore is the meta-progression goal. (Run gear and gold are
lost on death as today.)

### Completion reward

Collecting all fragments for a dungeon unlocks a **dungeon-specific
legendary item** — a unique piece of gear, narratively tied to the
lore. Examples:

- Cartographer's Folly → **Alaric's Quill** (some flavorful weapon or
  accessory, slightly mechanically unique)
- Undercrypt → **Phylactery Shard**
- Goblin Warrens → **The Goblin King's Crown** (cheekier, fits the tone)

The unlock mechanism: a hidden chest in the dungeon, or boss-only
drop, that *only spawns/gives the item* once all fragments are owned.
Authored per dungeon.

---

## 8. Variant System

Within a single delve, rooms vary; across delves, room contents vary;
across runs, encounter mixes vary. All variety lives in YAML.

### Sources of variety (in priority order)

1. **Room pool sampling.** 15-20 rooms per floor, 7-10 picked per run.
   Most rooms appear most runs; some are 1-in-5.
2. **Room variants.** A picked room may have variant content (empty
   coffin / sleeping ghoul / wraith), rolled at room-pick time. Same
   room, different content.
3. **Hidden feature rolls.** Whether the loose flagstone is in this
   instance of this room is a separate roll. Two players in the same
   room can have different things to find.
4. **Wandering encounter pool.** Floor-specific roving pool drives
   tick-based encounters. Different from authored room monsters.
5. **Lore fragment scattering.** Which fragments are present in this
   run are weighted; players who run the dungeon often see different
   ones, building the book over many runs.
6. **Description / ambient pools.** Each room has multiple base
   descriptions and an ambient pool; LLM picks per render. Same room
   reads different on visit 1 vs visit 8.

### NOT a source of variety

- **Dungeon-wide "mood."** Considered and rejected — too big a swing
  outside player control.
- **Race-specific room/feature variants.** Reserved for future
  consideration. Not in v2 launch.
- **Class-specific anything.** Classes don't exist yet.

### Variant authoring

```yaml
variants:
  - {key: default, weight: 60}
  - {key: chest, weight: 25, features_add: [...]}
  - {key: legendary, weight: 5, features_add: [...], ambush: {armed: true, creature: skullbinder_revenant}}
```

A `weight` of 0 disables a variant. Authors are expected to tune
weights per-room based on intended pacing. The system does not try to
balance variant tables automatically.

---

## 9. Combat in v2

Combat *mechanics* don't change — the system shipped in PRs 193 and
194 (effects, resolver, phases, abilities, multi-phase bosses,
summoner target-swap) is preserved as-is.

What changes is **when and how** combat starts:

- **Authored encounters.** Some rooms have a fixed monster (e.g., the
  boss room). Combat starts on entry or via a specific trigger.
- **Ambushes.** A room with an armed ambush triggers combat on first
  action (free monster action first; this is what `on_taken_hit`
  reserved trigger is for, eventually).
- **Wandering encounters.** Tick-based; a roving monster from the
  floor's wandering pool catches the player mid-search.
- **Provoked encounters.** Investigating the wrong feature can wake
  or summon a monster. Authored per-feature.
- **Avoidable encounters.** A room may have a sleeping/passive enemy
  the player can sneak past with the right action. Searching wakes it.

Combat embeds as today. After combat, the player returns to the room
they were in, with the room's state intact (what was already searched
remains searched).

### No surprise rounds / sneak attacks (yet)

Reserved for future. v2 launches with the existing combat balance.
Specific monster abilities (per-monster) might grant a surprise round
in the future via the existing ability schema, but it's not a global
mechanic.

---

## 10. Death and Recovery

### On death

- Run gold and run XP: **forfeit**.
- Run gear drops: **forfeit**.
- Lore fragments collected this run: **kept** (already on profile).
- Player corpse seeded for next attempt.

### Corpse persistence (one per (player, dungeon))

When a player dies in a dungeon, a **corpse entry** is recorded with
the floor of death and a snapshot of *some* of their lost run loot
(not all — we want death to sting).

When the player next enters the same dungeon, the corpse is seeded
into a random room on the same floor (or, if the new run doesn't
reach that floor, the corpse persists unseen until they do). If the
floor isn't deep enough or wasn't reached this delve, the corpse
remains. New deaths overwrite the old corpse — only one per
(player, dungeon) at a time.

Finding the corpse rewards the player with the snapshotted loot.
Failing to find it before dying again (overwriting it) loses it.

The corpse placement is **abstract** — we don't try to match the
exact death room. A "you find a body slumped against the wall — your
own gear, by the look of it" reveal works narratively across
procedural floor differences.

### Implementation note

A new table or JSON column on the player profile:
`(user_id, guild_id, dungeon_id) → {floor, loot_snapshot}`. One row
per dungeon per player. Cheap. New deaths in the same dungeon
upsert/overwrite.

---

## 11. Admin Gating During Development

v2 dungeons are restricted during development to the **Race Admin**
role (the existing role used for derby admin commands). This lets
admins playtest the new system while regular players continue with v1
unaffected.

Implementation:

- New optional dungeon YAML field: `min_role: "Race Admin"`.
- The `/dungeon delve` picker filters out dungeons the user doesn't
  qualify for.
- Direct access via `/dungeon delve <name>` returns a permissions
  error if the dungeon is gated and the user lacks the role.

This gating mechanism is **reusable** — future dungeons can soft-launch
to a beta cohort the same way.

At cutover (PR 8), the gating is lifted on v2 and v1 dungeons are
removed.

### Future: more role-based content

Out of scope for v2. But worth noting that this gating system makes
future "Goblin Slayer" / "Crypt Walker" achievement roles trivially
implementable — a role granted by Discord (manually, or by future
automation) unlocks specific content.

---

## 12. Action Economy & Pacing Targets

### Per-room action counts (rough averages)

| Room type | Actions | Notes |
|-----------|---------|-------|
| Empty room | 0-1 | Look around, see exits, leave. |
| Plain combat | 1 + combat | Enter, fight, leave. |
| Furnished, no hidden | 2-3 | Look, maybe Investigate, find nothing of interest, leave. |
| Furnished, hidden content | 3-5 | Multiple investigations, find things, possibly trigger an ambush or wandering. |
| Boss room | 1 + epic combat | Same as today. |

### Per-delve pacing target

A 10-room floor at 2.5 actions per room plus combat ≈ 25-30 button
presses per floor. At 30 seconds per press for a slow player, that's
12-15 minutes per floor. Three floors = 35-45 minute delve for
thorough play, ~20-25 for hurried play. Pause-and-resume covers any
real-world interruption.

### Tuning levers (for post-launch balancing)

- **Tick danger curve.** The probability of a tick-event is
  per-floor and tunable. v2 ships with conservative defaults; we
  expect to tune after playtesting.
- **Wandering pool size and weights.** Per-floor.
- **Variant weights.** Per-room.
- **Action noise.** Per-action defaults; per-room/per-feature
  overrides for special cases.

We will not try to balance these algorithmically. They're hand-tuned
based on play.

---

## 13. PR Sequence (recommended)

The overhaul is large enough that we plan to ship in PRs against `main`
without releasing to production. Production cuts over only when all
PRs land.

| PR | Scope | Verifies |
|----|-------|----------|
| 1 | Floor graph + tick system + Look/Investigate/Move buttons. Admin-gated v2 dungeon with **placeholder content** (3 rooms, empty/combat/boss). | Skeleton works end-to-end before content authoring begins. |
| 2 | Map render (fog-of-war, Unicode connectors). | Map looks good in Discord. |
| 3 | Hidden content schema (visibility tiers, perception checks, features/exits/ambient pools). Author 1-2 real rooms. | Schema feels right to author. |
| 4 | LLM DM integration (cached system prompt, graceful fallback to authored prose). | LLM enhances without breaking when down. |
| 5 | Lore fragments + collection display + legendary completion reward + corpse persistence. | Meta-progression loop works. |
| 6 | **Cartographer's Folly v2** — full content authoring (room pool, variants, lore, ambient, all features). | First ship-quality v2 dungeon. |
| 7 | **Goblin Warrens v2** + **Undercrypt v2** content authoring. May be split into 7a/7b. | All three dungeons authored. |
| 8 | Cutover: remove v1 dungeons, lift admin gate on v2. | Production release. |

Each PR is small enough to review meaningfully and is shipped to
`main` with v2 admin-gated. Players see no change until PR 8.

---

## 14. Decisions Locked

These are explicitly settled. Future me / future agents should not
re-litigate without good reason:

| Decision | Locked As |
|----------|-----------|
| Pause/resume is first-class | DB is source of truth, every action commits, resume embed is generous with context |
| No timers | Danger meter advances on action only, never wall-clock |
| Floor state in `floor_state_json` | Separate from `combat_state_json` |
| Procedural with anchors | Option B from planning; not hand-authored fixed layouts |
| Linear-with-branches at launch | Hubs and loops considered for future, not v2 |
| Buttons only, no freeform input | Free text reserved for future social mini-games |
| Action grammar | Look Around / Listen / Investigate / Move on / context-sensitive |
| Visibility tiers | passive / visible / concealed / secret |
| Investigate picks the feature when only one option | Don't make players click twice |
| Danger meter tells, not numbers | Player infers via in-character cues |
| Combat interrupts mid-action | Action cancelled, room state preserved, resume after combat |
| LLM scope | Atmospheric paint + reactive narration only; never invents game state |
| LLM is enhancement, not infrastructure | Authored fallback always works |
| Lore fragments are numbered books per dungeon | 12-20 per dungeon, scribbled-out gaps in display |
| Legendary item per dungeon as completion reward | Authored, narratively tied |
| One corpse per (player, dungeon) | Floor-only placement, overwritten on new death |
| No race/class-specific content in v2 | Reserved for future |
| No quick mode | The current v1 game IS the quick mode. We may ship a different fast game later. |
| No surprise rounds / sneak attacks globally | Per-monster abilities only via existing schema |
| Admin gating during dev | Race Admin role; via `min_role` YAML field |
| LLM elaboration of authored items | Generated once at instance-creation, persisted to instance |

---

## 15. Open Questions (post-launch)

These are out of scope for v2 launch but flagged for future
discussion:

- **Per-race / per-class room variants.** When classes ship, dungeons
  may want class-specific content (a paladin sees a dead colleague's
  symbol; a rogue spots a hidden lock). Not in v2.
- **Achievement roles.** "Goblin Slayer," "Crypt Walker," etc. —
  Discord roles granted for completion. Could unlock cosmetic flair
  or future content. Reuses the `min_role` gating system.
- **A faster mini-game.** If players miss the click-through pace,
  ship a separate battle-arena mini-game rather than retrofitting
  v2. Different game, same player base.
- **Per-feature freeform input** for social or puzzle moments. Reserved.
- **Hub/loop floor topologies.** Once linear-with-branches is
  validated, more complex layouts become possible.
- **Local LLM.** Long-term migration off the Anthropic API. The
  abstraction layer landed in PR 4 should make this a backend swap.

---

*This document is the source of truth for v2 design. Engineering and
writing agents should reference it directly. Updates require explicit
discussion — don't drift the design without flagging it.*
