from __future__ import annotations

from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

from derby import repositories as repo


class Reports(commands.Cog, name="reports"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="reports", description="Bot usage analytics (owner only)")
    @commands.is_owner()
    async def reports_group(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send(
                "Specify a subcommand: `usage`, `activity`, `trends`",
                ephemeral=True,
            )

    @reports_group.command(name="usage", description="Command usage stats")
    @app_commands.describe(days="Number of days to look back (default 7)")
    async def report_usage(self, context: Context, days: int = 7) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        since = datetime.utcnow() - timedelta(days=days)
        async with self.bot.scheduler.sessionmaker() as session:
            rows = await repo.get_command_usage(session, guild_id, since)

        if not rows:
            await context.send(
                f"No command usage recorded in the last {days} days.",
                ephemeral=True,
            )
            return

        lines = []
        for cmd, count, users in rows:
            lines.append(f"`/{cmd}` \u2014 **{count}** uses ({users} user{'s' if users != 1 else ''})")

        embed = discord.Embed(
            title=f"\U0001f4ca Command Usage (last {days} days)",
            description="\n".join(lines[:25]),
            color=0x3498DB,
        )
        total_commands = sum(r[1] for r in rows)
        embed.set_footer(text=f"Total: {total_commands} commands across {len(rows)} unique commands")
        await context.send(embed=embed, ephemeral=True)

    @reports_group.command(name="activity", description="Player activity stats")
    @app_commands.describe(days="Number of days to look back (default 7)")
    async def report_activity(self, context: Context, days: int = 7) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        since = datetime.utcnow() - timedelta(days=days)
        async with self.bot.scheduler.sessionmaker() as session:
            rows = await repo.get_player_activity(session, guild_id, since)
            player_lines = []
            for user_id, count in rows:
                top_cmd = await repo.get_player_top_command(
                    session, guild_id, user_id, since
                )
                player_lines.append((user_id, count, top_cmd))

        if not player_lines:
            await context.send(
                f"No player activity recorded in the last {days} days.",
                ephemeral=True,
            )
            return

        lines = []
        for user_id, count, top_cmd in player_lines:
            top_str = f" (most used: `/{top_cmd}`)" if top_cmd else ""
            lines.append(f"<@{user_id}> \u2014 **{count}** commands{top_str}")

        embed = discord.Embed(
            title=f"\U0001f465 Player Activity (last {days} days)",
            description="\n".join(lines[:25]),
            color=0x2ECC71,
        )
        total_commands = sum(r[1] for r in player_lines)
        embed.set_footer(
            text=f"Total: {len(player_lines)} active players, {total_commands} commands"
        )
        await context.send(embed=embed, ephemeral=True)

    @reports_group.command(name="trends", description="Weekly usage trends")
    @app_commands.describe(days="Number of days to compare (default 14)")
    async def report_trends(self, context: Context, days: int = 14) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        now = datetime.utcnow()

        half = days // 2
        current_start = now - timedelta(days=half)
        prev_start = now - timedelta(days=days)

        async with self.bot.scheduler.sessionmaker() as session:
            curr_cmds, curr_users = await repo.get_weekly_totals(
                session, guild_id, current_start, now
            )
            prev_cmds, prev_users = await repo.get_weekly_totals(
                session, guild_id, prev_start, current_start
            )
            curr_commands_set = await repo.get_commands_in_period(
                session, guild_id, current_start, now
            )
            prev_commands_set = await repo.get_commands_in_period(
                session, guild_id, prev_start, current_start
            )

        def _pct(current: int, previous: int) -> str:
            if previous == 0:
                return "+\u221e%" if current > 0 else "0%"
            change = ((current - previous) / previous) * 100
            sign = "+" if change >= 0 else ""
            return f"{sign}{change:.0f}%"

        new_cmds = curr_commands_set - prev_commands_set
        dropped = prev_commands_set - curr_commands_set

        lines = [
            f"**Current {half} days:** {curr_cmds} commands, {curr_users} players",
            f"**Previous {half} days:** {prev_cmds} commands, {prev_users} players",
            f"**Change:** {_pct(curr_cmds, prev_cmds)} commands, {_pct(curr_users, prev_users)} players",
            "",
        ]
        if new_cmds:
            lines.append(f"\U0001f195 **New this period:** {', '.join(f'`/{c}`' for c in sorted(new_cmds))}")
        if dropped:
            lines.append(f"\U0001f4a4 **Dropped off:** {', '.join(f'`/{c}`' for c in sorted(dropped))}")
        if not new_cmds and not dropped:
            lines.append("No new or dropped commands between periods.")

        embed = discord.Embed(
            title=f"\U0001f4c8 Usage Trends ({days} day window)",
            description="\n".join(lines),
            color=0x9B59B6,
        )
        await context.send(embed=embed, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Reports(bot))
