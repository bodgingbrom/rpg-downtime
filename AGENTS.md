# Development Guidelines for Downtime Derby

Downtime Derby is a Discord mini-game extension that runs scheduled animal races, allows betting with in-server currency and provides live commentary. The code base here derives from a Python Discord bot template and follows modern Python development practices. Use this document to keep efforts consistent and plan for future enhancements.

## Key Features of the MVP

- Autonomous league system where racers age, retire and newcomers join automatically.
- Stat-driven simulation with racer attributes (speed, cornering, stamina, temperament) interacting with map segments (straights, curves, hazards).
- Betting and bankroll mechanics with automated payouts and history tracking.
- Rich Discord UX using slash commands, embeds and announcer messages during races.
- Admin console for managing racers, controlling races and viewing debug data.

The end goal is a self-contained mini-game that runs daily and keeps downtime lively. Future features may include tournaments, weather effects, player-owned stables and racer training.

## Repository Structure

- `bot.py` – Entry point. Loads cogs, owns the global `DerbyScheduler`.
- `core/` – Cross-game models (`GuildSettings`, `CommandLog`) + their repos.
  Anything that no single mini-game owns belongs here.
- `cogs/` – Discord-facing slash commands. Files starting with `_` (e.g.
  `_autocomplete.py`) are private helpers — `bot.load_cogs` skips them.
- `derby/`, `dungeon/`, `fishing/`, `brewing/`, `rpg/`, `economy/` – Per-game
  modules. Each typically has `models.py`, `repositories.py`, `logic.py`,
  and game-specific extras (e.g. `dungeon/ui/`, `fishing/handlers/`).
  `derby/` also owns the `DerbyScheduler` (background loop) and the
  cross-game `GuildSettingsResolver`.
- `database/` – SQLite file lives here at runtime (auto-created).
- `tests/` – Pytest suites; one directory per mini-game. See "Scoped test
  suites" below.

## Coding Standards

1. **Python version**: Target Python 3.12. Use `async`/`await` patterns and type hints everywhere.
2. **Formatting**: Run `black` on all Python files and `isort` for import sorting. Use Prettier for any non‑Python files.
3. **Linting & Style**: Follow PEP 8 conventions. Keep naming consistent with existing code and use type hints throughout.
4. **Database access**: Use SQLAlchemy's asynchronous APIs. Cross-game
   models + repos live in `core/`; per-game models + repos live in
   `<game>/repositories.py`. Don't add a new `GuildSettings` column to
   `derby/`.
5. **Commit messages**: Use the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification.

## Conventions

A handful of patterns emerged from the refactor work and are now load-bearing.
When adding a new feature, follow the existing pattern unless you have a
specific reason not to — and if you depart from it, leave a comment.

### Reading guild settings

Use the cached resolver on the scheduler, not direct `repo.get_guild_settings`
calls:

```python
# Single key (most common):
val = await self.bot.scheduler.guild_settings.resolve(guild_id, "bet_window")

# Multiple keys from the same row (avoid N cache lookups):
gs = await self.bot.scheduler.guild_settings.get(guild_id)
base = resolve_guild_setting(gs, self.bot.settings, "racer_buy_base")
mult = resolve_guild_setting(gs, self.bot.settings, "racer_buy_multiplier")
```

Admin commands that mutate settings must call
`self.bot.scheduler.guild_settings.bust(guild_id)` after the write so the
next read sees the new value (the cache TTL is 5s otherwise).

### Fire-and-forget background tasks

Never call `asyncio.create_task` directly from scheduler/cog code. Use
`DerbyScheduler._spawn_background(coro, name=...)` — it holds a strong
reference (asyncio's bookkeeping is weak), logs uncaught exceptions via
`bot.logger`, and lets `close()` cancel cleanly on shutdown.

```python
self._spawn_background(
    self._regenerate_npc_quips(npc, flavor, "win"),
    name=f"regen_quips:{npc.id}:win",
)
```

### Slash-command autocomplete

Every autocomplete that filters items by case-insensitive substring
should route through `cogs._autocomplete.filter_choices`. Each callback
shrinks from a ~10-line `for/if/append/break` loop to a single call:

```python
return filter_choices(
    racers,
    current,
    label=lambda r: f"{r.name} (#{r.id})",
    value=lambda r: r.id,
    match=lambda r: r.name,  # optional — separate haystack
)
```

The helper handles Discord's 25-choice limit and 100-char label cap.

### Cog vs. logic separation

Cogs in `cogs/` should be **thin Discord wrappers**. Game logic — race
simulation, brewing chemistry, dungeon combat, etc. — lives in
`<game>/logic.py` (or split across `<game>/`). Cogs assemble inputs,
call logic, render embeds. If you find yourself writing meaningful
algorithm in a cog, push it down.

Private cog helpers (autocomplete utilities, embed builders shared
across cogs) get a `_` prefix in `cogs/` so `bot.load_cogs` skips them.

### Randomness in scheduler / logic

**Never** call module-global `random.choice/sample/randint` from logic
that can be exercised by tests. Either:

- Accept an optional `rng: random.Random | None = None` parameter and
  default to `random.Random()` (a fresh, isolated stream). Pattern used
  by `logic.check_injury_risk`, `logic.breed_racer`, and
  `DerbyScheduler._pick_competitive_field`.
- Or accept a seed and instantiate `random.Random(seed)` explicitly,
  like `logic.simulate_race`.

Tests can then pin behavior with `rng=random.Random(0)` instead of
hoping global state is what they expect.

### UI surgery for large cogs

When a cog file gets unwieldy:

- Pure embed builders → `<game>/ui/embeds.py` (see `dungeon/ui/embeds.py`).
- Per-rarity / per-mode handlers → `<game>/handlers/<key>.py` (see
  `fishing/handlers/{common,uncommon,rare,legendary}.py`). Each handler
  takes the runner instance as its first arg and accesses shared
  helpers via that ref.
- Re-export from the cog file if call sites would otherwise need
  rewriting — but don't leave shims in place forever (PR #209 removed
  the `derby.models` re-export shim once the import-site migration was
  complete).

### Discord interaction context

Cog commands typically use `commands.hybrid_command` so they work as
both slash commands and prefix commands. Two interaction objects are
floating around:

- `Context` (from `discord.ext.commands`) — for hybrid commands. Use
  `context.author`, `context.guild`, `await context.send(...)`.
- `discord.Interaction` — for `app_commands.Group` subcommands and
  autocomplete callbacks. Use `interaction.user`, `interaction.guild_id`,
  `interaction.client.scheduler` etc.

Don't mix them within one callback.

## Testing & Validation

Before committing any change:

1. Install dependencies with `python -m pip install -r requirements.txt`.
2. Run the **scoped** test suite for the area you changed. All tests should pass.
3. Ensure new features include accompanying tests.

### Scoped test suites

Tests are auto-tagged by directory via `tests/conftest.py`. Anything
under `tests/<game>/` gets the `<game>` marker. Run only what's
relevant to your change:

```bash
pytest -m fishing              # Lazy Lures changes
pytest -m derby                # Downtime Derby changes
pytest -m brewing              # Potion Panic changes
pytest -m dungeon              # Monster Mash changes
pytest -m rpg                  # player race / cross-game rpg changes
pytest -m "derby or economy"   # multi-tag selection
pytest -m admin                # reports / admin tooling
```

Run the **full suite** (`pytest`) when your change touches anything
cross-cutting — the scheduler, the economy/wallet, the daily digest, the
db schema, or shared config. When in doubt, run everything.

Markers are listed in `pytest.ini`. New mini-game tests go under
`tests/<game>/` and pick up the marker automatically — no conftest edit
needed. The handful of genuinely cross-cutting top-level tests (e.g.
`tests/test_admin_report.py`) declare their own marker via
`pytestmark = pytest.mark.<name>`.

## Running Locally

You can start the bot with Docker:

```bash
docker compose up
```

Or run it directly after installing the requirements and configuring `.env` variables.

## Planning for Growth

Structure new features so they can be expanded later. Keep simulation logic and Discord interactions decoupled. Document assumptions in code comments and tests. Whenever possible, prefer small reusable functions over large monolithic ones.

---
This `AGENTS.md` applies to the entire repository. Keep the focus on building a cohesive Downtime Derby mini-game while following industry-standard practices.
