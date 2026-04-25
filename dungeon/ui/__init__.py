"""UI helpers (embed builders, view classes) for the dungeon cog.

Extracted out of ``cogs/dungeon.py`` so the cog file can focus on slash
commands and orchestration. Currently only embed builders live here; the
view classes remain in cogs/dungeon.py because they're tightly coupled
to action handlers in the same file.
"""
