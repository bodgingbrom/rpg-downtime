"""Auto-apply per-mini-game pytest markers based on test file path/name.

Lets the suite be sliced with ``pytest -m <marker>`` without decorating
every individual test. Directory-based tagging handles the common case
(anything under ``tests/<game>/`` gets the ``<game>`` marker). Top-level
``tests/test_*.py`` files are tagged by a short keyword scan on the
filename.

Conventions — see pytest.ini for the full marker list. Changes that cross
game boundaries (economy, wallet, scheduler, db) should run the full
suite (``pytest``) rather than a scoped one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Directory name → marker name. Keep in sync with pytest.ini::markers.
_DIR_MARKERS = {
    "brewing": "brewing",
    "derby": "derby",
    "dungeon": "dungeon",
    "economy": "economy",
    "fishing": "fishing",
    "rpg": "rpg",
}

# Filename keyword → marker for top-level tests/test_*.py files. Checked
# in order — first match wins.
_FILENAME_MARKERS = [
    ("daily", "economy"),
    ("digest", "economy"),
    ("training", "derby"),
    ("stable", "derby"),
    ("derby", "derby"),
    ("npc", "derby"),
    ("admin", "admin"),
    ("report", "admin"),
]


def pytest_collection_modifyitems(config, items):  # noqa: D401
    """Auto-apply directory + filename markers to each collected test."""
    for item in items:
        path = Path(str(item.fspath))

        # Directory-based tagging — the first parent that matches wins.
        applied = False
        for part in path.parts:
            marker = _DIR_MARKERS.get(part.lower())
            if marker is not None:
                item.add_marker(getattr(pytest.mark, marker))
                applied = True
                break
        if applied:
            continue

        # Top-level files fall back to filename keyword scan.
        name = path.stem.lower()
        for keyword, marker in _FILENAME_MARKERS:
            if keyword in name:
                item.add_marker(getattr(pytest.mark, marker))
                break
