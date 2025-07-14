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

- `bot.py` – Entry point for the Discord bot.
- `cogs/` – Discord command modules. New Downtime Derby commands should go here.
- `derby/` – Core data models and repositories for the mini-game.
- `database/` – Database helpers and session management.
- `tests/` – Pytest suites. Add tests for new features here.

## Coding Standards

1. **Python version**: Target Python 3.12. Use `async`/`await` patterns and type hints everywhere.
2. **Formatting**: Run `black` on all Python files and `isort` for import sorting. Use Prettier for any non‑Python files.
3. **Linting & Style**: Follow PEP 8 conventions. Keep naming consistent with existing code and use type hints throughout.
4. **Database access**: Use SQLAlchemy's asynchronous APIs. Keep repository functions in `derby/repositories.py`.
5. **Commit messages**: Use the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification.

## Testing & Validation

Before committing any change:

1. Install dependencies with `python -m pip install -r requirements.txt`.
2. Run unit tests via `pytest` from the repository root. All tests should pass.
3. Ensure new features include accompanying tests.

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
