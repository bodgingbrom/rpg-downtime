"""Per-rarity event handlers for active fishing.

Each handler module owns its rarity's interaction flow (the modal/view
classes the player sees and the function that drives them). The runner
in ``fishing/active.py`` dispatches to ``handle_<rarity>(runner, fs,
catch, location_data)`` which returns ``True`` if the catch succeeded.
"""

from .common import handle_common
from .legendary import handle_legendary
from .rare import handle_rare
from .uncommon import handle_uncommon

__all__ = [
    "handle_common",
    "handle_legendary",
    "handle_rare",
    "handle_uncommon",
]
