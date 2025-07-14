from __future__ import annotations

from discord.ext import commands


def has_role(role_name: str) -> commands.Check:
    async def predicate(ctx: commands.Context) -> bool:
        roles = getattr(getattr(ctx, "author", None), "roles", [])
        for role in roles:
            if getattr(role, "name", None) == role_name:
                return True
        return False

    return commands.check(predicate)
