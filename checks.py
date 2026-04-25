from __future__ import annotations

from discord.ext import commands

from config import resolve_guild_setting

# Per-game channel setting keys and their defaults
GAME_CHANNEL_KEYS = (
    "derby_channel",
    "brewing_channel",
    "fishing_channel",
    "dungeon_channel",
)


def has_role(role_name: str) -> commands.Check:
    async def predicate(ctx: commands.Context) -> bool:
        roles = getattr(getattr(ctx, "author", None), "roles", [])
        for role in roles:
            if getattr(role, "name", None) == role_name:
                return True
        return False

    return commands.check(predicate)


async def _load_guild_settings(ctx: commands.Context):
    """Load GuildSettings for the current guild, or None.

    Uses the scheduler's GuildSettingsResolver cache — channel checks fire
    on every command, so a per-command DB hit would be wasteful.
    """
    scheduler = getattr(ctx.bot, "scheduler", None)
    if scheduler is None:
        return None
    guild = ctx.guild
    if guild is None:
        return None
    resolver = getattr(scheduler, "guild_settings", None)
    if resolver is None:
        return None
    return await resolver.get(guild.id)


async def in_bot_channel(ctx: commands.Context, channel_key: str | None = None) -> bool:
    """Return True if the command was invoked in the correct game channel.

    Parameters
    ----------
    channel_key:
        The per-game setting key (e.g. ``"derby_channel"``).  When
        ``None``, falls back to the legacy ``channel_name`` setting.
    """
    bot = ctx.bot
    if ctx.guild is None:
        return True

    gs = await _load_guild_settings(ctx)
    if gs is None and getattr(bot, "scheduler", None) is None:
        return True

    # Resolve the expected channel name
    if channel_key is not None:
        channel_name = resolve_guild_setting(gs, bot.settings, channel_key)
    else:
        channel_name = resolve_guild_setting(gs, bot.settings, "channel_name")

    if not channel_name:
        return True

    if getattr(ctx.channel, "name", None) == channel_name:
        return True

    # Send a helpful redirect and suppress the default error
    await ctx.send(
        f"Please use this command in **#{channel_name}**.",
        ephemeral=True,
    )
    raise commands.CheckFailure("wrong channel")


async def in_any_game_channel(ctx: commands.Context) -> bool:
    """Return True if the command was invoked in *any* configured game channel.

    Used for shared commands (economy) that should work in all game channels.
    """
    bot = ctx.bot
    if ctx.guild is None:
        return True

    gs = await _load_guild_settings(ctx)
    if gs is None and getattr(bot, "scheduler", None) is None:
        return True

    current = getattr(ctx.channel, "name", None)

    # Collect all configured game channels
    allowed: list[str] = []
    for key in GAME_CHANNEL_KEYS:
        name = resolve_guild_setting(gs, bot.settings, key)
        if name:
            allowed.append(name)

    # Legacy fallback
    legacy = resolve_guild_setting(gs, bot.settings, "channel_name")
    if legacy and legacy not in allowed:
        allowed.append(legacy)

    # If nothing is configured, allow everywhere
    if not allowed:
        return True

    if current in allowed:
        return True

    channels_str = ", ".join(f"**#{c}**" for c in sorted(set(allowed)))
    await ctx.send(
        f"Please use this command in one of the game channels: {channels_str}.",
        ephemeral=True,
    )
    raise commands.CheckFailure("wrong channel")
