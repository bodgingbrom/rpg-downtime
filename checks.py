from __future__ import annotations

from discord.ext import commands

from config import resolve_guild_setting


def has_role(role_name: str) -> commands.Check:
    async def predicate(ctx: commands.Context) -> bool:
        roles = getattr(getattr(ctx, "author", None), "roles", [])
        for role in roles:
            if getattr(role, "name", None) == role_name:
                return True
        return False

    return commands.check(predicate)


async def in_bot_channel(ctx: commands.Context) -> bool:
    """Return True if the command was invoked in the configured bot channel.

    When no channel_name is configured the check always passes.
    """
    bot = ctx.bot
    scheduler = getattr(bot, "scheduler", None)
    if scheduler is None:
        return True

    guild = ctx.guild
    if guild is None:
        return True

    gs = None
    sessionmaker = getattr(scheduler, "sessionmaker", None)
    if sessionmaker is not None:
        from derby import repositories as repo
        async with sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild.id)

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
