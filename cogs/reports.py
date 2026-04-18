from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

from derby import repositories as repo
from fishing import logic as fish_logic
from fishing import repositories as fish_repo


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

    # ------------------------------------------------------------------
    # Fishing LLM event reports (active mode: uncommon / rare / legendary)
    # ------------------------------------------------------------------

    @staticmethod
    def _outcome_emoji(outcome: str) -> str:
        return {
            "caught": "\u2705",   # ✅
            "escaped": "\u274C",  # ❌
            "timeout": "\u23F1",  # ⏱
            "unconvinced": "\u274C",
        }.get(outcome, "\u2754")  # ❔

    @reports_group.command(
        name="fishing-uncommon",
        description="Last 10 uncommon vibe checks with prompts and outcomes",
    )
    @app_commands.describe(days="Number of days to look back (default 7)")
    async def report_fishing_uncommon(
        self, context: Context, days: int = 7
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        since = datetime.now(timezone.utc) - timedelta(days=days)

        async with self.bot.scheduler.sessionmaker() as session:
            rows = await fish_repo.get_recent_active_events(
                session, guild_id, rarity="uncommon", since=since, limit=10,
            )

        if not rows:
            await context.send(
                f"No uncommon vibe checks recorded in the last {days} days.",
                ephemeral=True,
            )
            return

        locations = fish_logic.load_locations()
        blocks: list[str] = []
        for r in rows:
            ts = r.created_at.strftime("%m-%d %H:%M")
            loc = locations.get(r.location_name, {}).get("name", r.location_name)
            emoji = self._outcome_emoji(r.outcome)
            word = r.player_response or "(no response)"
            # Truncate long passages for display
            passage = r.prompt_text.replace("\n", " ")
            if len(passage) > 150:
                passage = passage[:147] + "..."
            blocks.append(
                f"`{ts}` <@{r.user_id}> @ {loc} \u2014 {r.fish_species}\n"
                f"  *\u201c{passage}\u201d*\n"
                f"  Word: **{word}** \u2192 {emoji} {r.outcome.upper()}"
            )

        embed = discord.Embed(
            title=f"\U0001F3A3 Uncommon Vibe Checks \u2014 last {len(rows)} ({days}d)",
            description="\n\n".join(blocks),
            color=0x3498DB,
        )
        await context.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions(users=False),
        )

    @reports_group.command(
        name="fishing-rare",
        description="Last 10 rare haiku attempts with openings and closing lines",
    )
    @app_commands.describe(days="Number of days to look back (default 7)")
    async def report_fishing_rare(
        self, context: Context, days: int = 7
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        since = datetime.now(timezone.utc) - timedelta(days=days)

        async with self.bot.scheduler.sessionmaker() as session:
            rows = await fish_repo.get_recent_active_events(
                session, guild_id, rarity="rare", since=since, limit=10,
            )

        if not rows:
            await context.send(
                f"No rare haiku attempts recorded in the last {days} days.",
                ephemeral=True,
            )
            return

        locations = fish_logic.load_locations()
        blocks: list[str] = []
        for r in rows:
            ts = r.created_at.strftime("%m-%d %H:%M")
            loc = locations.get(r.location_name, {}).get("name", r.location_name)
            emoji = self._outcome_emoji(r.outcome)
            # prompt_text is the full 3-line haiku with `_______________` in
            # the slot the player filled (or filled for legacy 2-line rows).
            displayed = " / ".join(
                ln.strip() for ln in r.prompt_text.splitlines() if ln.strip()
            ) or r.prompt_text
            response = r.player_response or "(no response)"
            blocks.append(
                f"`{ts}` <@{r.user_id}> @ {loc} \u2014 {r.fish_species}\n"
                f"  *{displayed}*\n"
                f"  Response: **{response}** \u2192 {emoji} {r.outcome.upper()}"
            )

        embed = discord.Embed(
            title=f"\U0001F3A3 Rare Haikus \u2014 last {len(rows)} ({days}d)",
            description="\n\n".join(blocks),
            color=0x9B59B6,
        )
        await context.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions(users=False),
        )

    @reports_group.command(
        name="fishing-legendary",
        description="Last 10 legendary encounters with outcomes and summaries",
    )
    @app_commands.describe(days="Number of days to look back (default 7)")
    async def report_fishing_legendary(
        self, context: Context, days: int = 7
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        since = datetime.now(timezone.utc) - timedelta(days=days)

        async with self.bot.scheduler.sessionmaker() as session:
            rows = await fish_repo.get_recent_guild_encounters(
                session, guild_id, since=since, limit=10,
            )

        if not rows:
            await context.send(
                f"No legendary encounters recorded in the last {days} days.",
                ephemeral=True,
            )
            return

        locations = fish_logic.load_locations()
        blocks: list[str] = []
        outcome_titles = {
            "caught": "\U0001F3C6 CAUGHT",
            "unconvinced": "\u274C UNCONVINCED",
            "escaped": "\U0001F30A ESCAPED",
        }
        for enc, leg in rows:
            ts = enc.created_at.strftime("%m-%d %H:%M")
            loc = locations.get(leg.location_name, {}).get(
                "name", leg.location_name
            )
            outcome_label = outcome_titles.get(
                enc.outcome, enc.outcome.upper()
            )
            summary = enc.dialogue_summary
            if len(summary) > 250:
                summary = summary[:247] + "..."
            blocks.append(
                f"`{ts}` <@{enc.user_id}> vs. **{leg.name}** @ {loc}\n"
                f"  Outcome: {outcome_label}\n"
                f"  Summary: *{summary}*"
            )

        embed = discord.Embed(
            title=f"\U0001F451 Legendary Encounters \u2014 last {len(rows)} ({days}d)",
            description="\n\n".join(blocks),
            color=0xF1C40F,
        )
        await context.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions(users=False),
        )

    @reports_group.command(
        name="fishing-rates",
        description="Active fishing success rates across rarities",
    )
    @app_commands.describe(days="Number of days to look back (default 7)")
    async def report_fishing_rates(
        self, context: Context, days: int = 7
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        since = datetime.now(timezone.utc) - timedelta(days=days)

        async with self.bot.scheduler.sessionmaker() as session:
            active_counts = await fish_repo.get_active_event_counts(
                session, guild_id, since,
            )
            legendary_counts = await fish_repo.get_legendary_outcome_counts(
                session, guild_id, since,
            )

        def _stats(rarity: str) -> tuple[int, int]:
            """Return (caught, total) for uncommon/rare from the grouped dict."""
            caught = active_counts.get((rarity, "caught"), 0)
            total = sum(
                c for (r, _o), c in active_counts.items() if r == rarity
            )
            return caught, total

        un_caught, un_total = _stats("uncommon")
        ra_caught, ra_total = _stats("rare")
        leg_caught = legendary_counts.get("caught", 0)
        leg_total = sum(legendary_counts.values())

        def _line(label: str, caught: int, total: int) -> str:
            if total == 0:
                return f"{label}: no attempts"
            pct = int(round((caught / total) * 100))
            return f"{label}: **{caught}/{total}** ({pct}%)"

        total_attempts = un_total + ra_total + leg_total
        total_caught = un_caught + ra_caught + leg_caught
        overall_pct = (
            int(round((total_caught / total_attempts) * 100))
            if total_attempts else 0
        )

        lines = [
            _line("Uncommon (Vibe Check)", un_caught, un_total),
            _line("Rare (Haiku)", ra_caught, ra_total),
            _line("Legendary (Convincing)", leg_caught, leg_total),
            "",
            f"Total attempts: **{total_attempts}** \u00B7 "
            f"Caught: **{total_caught}** ({overall_pct}%)",
            "*Commons: always pass (not tracked).*",
        ]

        embed = discord.Embed(
            title=f"\U0001F3A3 Fishing LLM Success Rates \u2014 last {days} days",
            description="\n".join(lines),
            color=0x2ECC71,
        )
        await context.send(embed=embed, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Reports(bot))
