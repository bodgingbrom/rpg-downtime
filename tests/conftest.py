"""Auto-apply per-mini-game pytest markers based on test file path.

Anything under ``tests/<game>/`` is tagged with the ``<game>`` marker,
so the suite can be sliced with ``pytest -m <marker>``. Top-level
``tests/test_*.py`` files (genuinely cross-cutting tests like the admin
reporting suite or the autocomplete helper) declare their own marker via
``pytestmark = pytest.mark.<name>`` if they need one.

Conventions — see pytest.ini for the full marker list. Changes that
cross game boundaries (economy, wallet, scheduler, db) should run the
full suite (``pytest``) rather than a scoped one.
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


def pytest_collection_modifyitems(config, items):  # noqa: D401
    """Auto-apply directory markers to each collected test."""
    for item in items:
        path = Path(str(item.fspath))
        for part in path.parts:
            marker = _DIR_MARKERS.get(part.lower())
            if marker is not None:
                item.add_marker(getattr(pytest.mark, marker))
                break
