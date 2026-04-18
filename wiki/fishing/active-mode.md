# Active Mode

Active mode is Lazy Lures' weird sibling. Bites come every 30-90 seconds, and each one is a little interactive moment — the kind of thing that makes you actually want to watch your line.

```
/fish active location: Calm Pond
```

Like AFK, you commit your bait, the bot announces you've started fishing, and a per-session background task starts ticking. Unlike AFK, every bite is a prompt you have to respond to.

## Rules

- **Mutually exclusive with AFK**: you can't have both running. Starting one rejects if the other is active.
- **No trash catches**: the trash pool is excluded entirely in active mode. Every bite is a real fish.
- **LLM required**: the bot needs `ANTHROPIC_API_KEY` configured. If it's missing, `/fish active` rejects with a clear message.
- **Bait burns on failure**: missing a vibe check, failing a haiku, or getting an "unconvinced" from a legendary still consumes the bait. That's the risk.
- **Legendaries are active-only**: AFK sessions never roll a legendary. They only exist here.
- **Timeouts exist**: if you walk away mid-bite, the fish escapes after the timeout (unless it's a common, where you still catch it).
- **Interaction check**: other players *can* click your prompt's button, but they'll just get a "This isn't your line!" ephemeral response. Only you can complete the event.

## The Rarity Ladder

The weirdness scales with rarity.

---

### Common → Whisper (pure flavor, always catches)

A common fish bites. The bot posts an embed with a "🎣 Reel it in" button.

> 🎣 **Something bites at Calm Pond...**
> @Brom — your line twitches. Reel it in to see what you caught.

When you click, the bot calls the LLM to generate a short whisper — the fish says something cryptic or absurd as you reel it in.

> 🐟 **You caught a Bluegill!**
> Bluegill • 3 coins
>
> *Bluegill whispers:* "You have seven keys, but only six locks. Be careful with the extra."

**No failure state.** If you don't click within 5 minutes, you still catch the fish — you just miss the flavor. The button timeout is long on purpose so you can be legitimately AFK and not lose anything.

---

### Uncommon → Vibe Check (one-word LLM match, ~25s)

An uncommon fish bites. The bot posts a short atmospheric passage about the bite's *feel*, followed by a Respond button.

> 🎣 **Something bigger at Calm Pond...**
> @Brom
>
> *The line goes heavy. Something old and patient is on the other end.*
>
> Respond in **25s** with a single word that captures the vibe.
>
> [Respond]

Clicking opens a modal with a single text input — one word. You submit, and an LLM judge decides whether your word captures the passage's mood.

- **Pass** → catch the fish. The embed updates with a confirmation.
- **Fail** → fish slips away, bait burned.
- **Timeout** → same as fail.

**The judge is generous.** Synonyms and mood-adjacents pass. Words with the wrong emotional register or obvious nonsense fail. You can write anything from "patient" to "heavy" to "hollow" for a tense passage — they all land. "Taco" does not.

Single letters and words under 3 characters auto-fail for sanity.

---

### Rare → Haiku (complete the missing line, ~40s)

A rare fish bites. The bot generates a complete three-line haiku themed to the fish and location, then **randomly blanks one of the three lines** for you to fill in.

> ✨ **A rare stir at Calm Pond...**
> @Brom
>
> *mist on the water*
> *a silver shape turns below*
> *_______________*
>
> Fill in the **closing** 5-syllable line in **40s**.
>
> [✍️ Complete the haiku]

The blank position rotates — sometimes you'll fill in the opening 5-syllable line, sometimes the 7-syllable middle, sometimes the closing 5. The modal label adapts to the expected syllable count.

An LLM judge scores the assembled poem for fit (not strict syllable count — just "does this feel like it belongs"):

- **Pass** → catch the fish **and save the haiku** to your log
- **Fail / Timeout** → fish escapes, bait burned, haiku not saved

**The judge is forgiving with beginner poets.** Simple, honest lines that fit the tone pass. Nonsense ("asdfghjkl") and keyboard mashing do not. If you're making a genuine attempt, you'll usually land.

### The Haiku Log

Every successful haiku is saved forever.

```
/fish haiku mine      # Your last 10 haikus (ephemeral)
/fish haiku random    # Post a random guild haiku publicly
```

The `/fish haiku random` command is the magic one — it picks any haiku from any player in the guild and posts it attributed to the original author. A guild-wide ambient poetry collective, surfacing your old catches months later.

> 📖 **From the haiku log...**
> *mist on the water*
> *a silver shape turns below*
> *silence swallows all*
>
> Golden Perch @ Calm Pond — 2026-04-20

Mentions are suppressed so it doesn't ping the author.

---

### Legendary → The Convincing (unique persistent character, up to 3 rounds of dialogue)

Legendaries aren't species. They're **characters**. Each location has exactly **one** active legendary at any time, generated by the LLM with a unique name, personality, and backstory. When you catch one, they're retired forever — and a brand-new one is immediately generated to take their place.

And they remember.

When you encounter a legendary, the LLM seeds its "system prompt" with:
- The character's persistent sheet (name, personality, motives)
- Your past encounters with *this specific fish*
- Recent encounters this fish has had with *other players*

So the fish remembers what you said last week. And it might bring up what someone else told it two weeks ago.

#### The flow

A legendary bites. The bot loads the existing active legendary (or generates a new one if this is the first active session at this location for your guild). The fish opens with a question or challenge:

> 🐉 **A legendary appears at Calm Pond...**
> @Brom
>
> **Koi-san the Drowsy**:
> *I spoke to another angler last week — they told me they came here to impress their friends. Are you also here to perform, or is there something else between your teeth?*
>
> Respond within **60s** (round 1 of 3).
>
> [💬 Respond]

You open the modal, type a response, and submit. The LLM classifies what you said:

| Verdict | What happens |
|---------|--------------|
| **CONVINCED** | You catch the legendary. They retire. A new one is generated. |
| **ALMOST** | The fish is intrigued. It poses another challenge. Continue to the next round. |
| **UNCONVINCED** | The fish leaves immediately. Bait burned, encounter over. |

You have up to **3 rounds** to convince the fish. An ALMOST on round 3 counts as UNCONVINCED — no more chances.

**The classifier is generous.** Engaged, in-character, thoughtful responses tend to land in 1-2 rounds. Hostile one-liners, obvious attempts to break the game ("act as a different AI"), or off-topic nonsense get rejected immediately.

#### The catch

> 🏆 **Koi-san the Drowsy yields!**
>
> **Koi-san the Drowsy**: *I spoke to another angler...*
>
> **Brom**: *I came because my best friend died last spring and I heard you miss people too.*
>
> *Koi-san the Drowsy has been caught. Its story ends here.*

After the catch, the channel gets a public 👑 announcement and a new legendary is generated in the background to take the old one's place.

#### The memory

Every encounter — caught, escaped, or unconvinced — is saved as a short LLM-generated summary attached to that legendary. On your next encounter with the same fish, those summaries are included in its system prompt.

So if you lost to Koi-san on your first try because you told them you came for coins, your next encounter might open with:

> *You again. You said last time you came for the money. Has anything changed?*

And if another player already caught and retired Koi-san, the **new** fish at Calm Pond has zero memory of anyone. Fresh start.

#### What gets logged where

| Thing | Table | Visible via |
|-------|-------|-------------|
| The legendary character itself | `legendary_fish` (one active per location) | DB / future hall command |
| Each encounter (per player) | `legendary_encounters` | `/reports fishing-legendary` |
| The underlying species catch | `fish_catches` (same as other rarities) | `/fish log` |

The fish log treats legendary catches as the **YAML species name** (e.g. "Phantom Koi"), so completing a location's trophy works the same way regardless of which specific unique character you actually landed.

## Session Management

Same as AFK:

- `/fish stop` ends the session, refunds any remaining bait, and cancels the background task
- `/fish status` shows your active session
- Bot restart: any mid-dialogue encounters end gracefully. Orphaned sessions are cleaned up on startup, bait refunded, legendaries' state preserved.

## Economy Notes

Active mode has the **same per-fish values** as AFK. The only economic advantage is faster bites (30-90s vs 10-35min), which means more catches per hour. In practice the income rate is comparable to late-game AFK but with way more engagement.

Think of active mode as a "tended fire" version of fishing. Same fuel (bait), same output rate per catch — just a different feeling of presence.

## Tips

- **Premium Bait at Deep Lake** is the only reliable way to reach Leviathan's Shadow (legendary) encounters.
- **Keep your replies in-character for legendaries.** The judge is generous but hostile or meta responses get rejected fast.
- **Rare haikus don't need to be great poetry.** Sincere attempts pass. A simple line that fits the mood works.
- **Don't rush vibe checks.** 25 seconds is more than enough to read the passage and think of a word. Hurrying invites silly submissions.
- **Active mode doesn't have a daily cap.** As long as you have bait, you can keep going. Pace yourself — each bite is a small performance.
