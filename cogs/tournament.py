from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

import checks
from cogs.derby import owned_racer_autocomplete
from derby import logic, models
from derby import repositories as repo


def _next_tournament_time(rank: str) -> datetime | None:
    """Return the next UTC datetime when a tournament of this rank fires."""
    from derby.scheduler import TOURNAMENT_SCHEDULE

    now = datetime.now(timezone.utc)
    matches = [(wd, h, m) for wd, h, m, r in TOURNAMENT_SCHEDULE if r == rank]
    if not matches:
        return None

    candidates = []
    for wd, h, m in matches:
        days_ahead = (wd - now.weekday()) % 7
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        candidate = candidate + timedelta(days=days_ahead)
        if candidate <= now:
            candidate = candidate + timedelta(days=7)
        candidates.append(candidate)

    return min(candidates)


class Tournament(commands.Cog, name="tournament_cog"):
    """Tournament registration and listing commands."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        return await checks.in_bot_channel(ctx, "derby_channel")

    tournament = app_commands.Group(
        name="tournament", description="Tournament commands"
    )

    @tournament.command(name="register", description="Register a racer for the next tournament")
    @app_commands.describe(racer="The racer to register")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def tournament_register(
        self, interaction: discord.Interaction, racer: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id or 0
        user_id = interaction.user.id

        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None:
                await interaction.followup.send("Racer not found.", ephemeral=True)
                return

            if racer_obj.owner_id != user_id:
                await interaction.followup.send(
                    "You don't own that racer.", ephemeral=True
                )
                return

            if racer_obj.retired:
                await interaction.followup.send(
                    "Retired racers can't compete in tournaments.", ephemeral=True
                )
                return

            rank = racer_obj.rank
            if rank is None:
                rank = logic.assign_rank_if_needed(racer_obj)
                await session.commit()

            if rank is None:
                await interaction.followup.send(
                    "This racer has no rank assigned.", ephemeral=True
                )
                return

            # Find or create pending tournament for this guild+rank
            tournament = await repo.get_pending_tournament(session, guild_id, rank)
            if tournament is None:
                tournament = await repo.create_tournament(
                    session, guild_id=guild_id, rank=rank
                )

            # Check for duplicate registration in this bracket
            existing = await repo.get_player_tournament_entry(
                session, tournament.id, user_id
            )
            if existing is not None:
                await interaction.followup.send(
                    f"You already have a racer registered in the {rank}-Rank tournament.",
                    ephemeral=True,
                )
                return

            await repo.create_tournament_entry(
                session,
                tournament_id=tournament.id,
                racer_id=racer_obj.id,
                owner_id=user_id,
                is_pool_filler=False,
            )

        # Find next scheduled time for this rank
        next_time = _next_tournament_time(rank)
        time_str = f"<t:{int(next_time.timestamp())}:R>" if next_time else "soon"

        await interaction.followup.send(
            f"✅ **{racer_obj.name}** registered for the **{rank}-Rank** tournament! "
            f"Next run: {time_str}",
            ephemeral=True,
        )

    @tournament.command(name="cancel", description="Cancel a tournament registration")
    @app_commands.describe(racer="The racer to unregister")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def tournament_cancel(
        self, interaction: discord.Interaction, racer: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id or 0
        user_id = interaction.user.id

        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None:
                await interaction.followup.send("Racer not found.", ephemeral=True)
                return

            rank = racer_obj.rank
            if rank is None:
                await interaction.followup.send(
                    "This racer has no rank.", ephemeral=True
                )
                return

            tournament = await repo.get_pending_tournament(session, guild_id, rank)
            if tournament is None:
                await interaction.followup.send(
                    "No pending tournament for this rank.", ephemeral=True
                )
                return

            entry = await repo.get_player_tournament_entry(
                session, tournament.id, user_id
            )
            if entry is None or entry.racer_id != racer_obj.id:
                await interaction.followup.send(
                    "That racer isn't registered in this tournament.", ephemeral=True
                )
                return

            await session.delete(entry)
            await session.commit()

        await interaction.followup.send(
            f"❌ **{racer_obj.name}** has been withdrawn from the **{rank}-Rank** tournament.",
            ephemeral=True,
        )

    @tournament.command(name="list", description="Show pending tournaments")
    async def tournament_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        guild_id = interaction.guild_id or 0

        lines = []
        async with self.bot.scheduler.sessionmaker() as session:
            for rank in ["S", "A", "B", "C", "D"]:
                tournament = await repo.get_pending_tournament(session, guild_id, rank)
                if tournament is None:
                    continue
                entries = await repo.get_tournament_entries(session, tournament.id)
                player_entries = [e for e in entries if not e.is_pool_filler]
                if not player_entries:
                    continue

                racer_names = []
                for entry in player_entries:
                    racer = await session.get(models.Racer, entry.racer_id)
                    name = racer.name if racer else f"Racer {entry.racer_id}"
                    racer_names.append(f"**{name}** (<@{entry.owner_id}>)")

                next_time = _next_tournament_time(rank)
                time_str = f"<t:{int(next_time.timestamp())}:R>" if next_time else "TBD"

                lines.append(
                    f"**{rank}-Rank** — {len(player_entries)} registered | Next: {time_str}\n"
                    + "\n".join(f"  • {n}" for n in racer_names)
                )

        if not lines:
            await interaction.followup.send(
                "No tournaments with registered players. "
                "Use `/tournament register` to enter one!"
            )
            return

        embed = discord.Embed(
            title="🏟️ Pending Tournaments",
            description="\n\n".join(lines),
            color=0x9B59B6,
        )
        await interaction.followup.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Tournament(bot))
