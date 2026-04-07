from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

from brewing import potions as brew_potions
from brewing import repositories as brew_repo
from derby import repositories as derby_repo


async def _potion_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete showing player's owned potions."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    guild_id = interaction.guild.id if interaction.guild else 0
    user_id = interaction.user.id

    async with sessionmaker() as session:
        potions = await brew_repo.get_player_potions(session, user_id, guild_id)

    current_lower = current.lower()
    choices = []
    for p in potions:
        if current_lower in p.potion_name.lower():
            choices.append(
                app_commands.Choice(name=p.potion_name, value=str(p.id))
            )
        if len(choices) >= 25:
            break
    return choices


async def _target_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Context-dependent autocomplete: racers or ingredients based on potion type."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    guild_id = interaction.guild.id if interaction.guild else 0
    user_id = interaction.user.id

    # Try to determine potion type from the already-filled potion field
    potion_value = interaction.namespace.potion
    potion_type = None
    if potion_value:
        try:
            async with sessionmaker() as session:
                potion_obj = await brew_repo.get_player_potion(
                    session, int(potion_value)
                )
                if potion_obj:
                    potion_type = potion_obj.potion_type
        except (ValueError, TypeError):
            pass

    current_lower = current.lower()
    choices = []

    if potion_type in brew_potions.INGREDIENT_POTIONS:
        # Show unrevealed ingredients
        async with sessionmaker() as session:
            all_ingredients = await brew_repo.get_all_ingredients(session)
            revealed = await brew_repo.get_revealed_ingredients(
                session, user_id, guild_id
            )
        revealed_ids = {r.ingredient_id for r in revealed}
        for ing in all_ingredients:
            if ing.id not in revealed_ids and current_lower in ing.name.lower():
                choices.append(
                    app_commands.Choice(name=ing.name, value=str(ing.id))
                )
            if len(choices) >= 25:
                break
    elif potion_type in brew_potions.NO_TARGET_POTIONS:
        # No target needed
        pass
    else:
        # Default: show owned racers
        async with sessionmaker() as session:
            racers = await derby_repo.get_owned_racers(
                session, user_id, guild_id
            )
        for r in racers:
            if current_lower in r.name.lower():
                choices.append(
                    app_commands.Choice(
                        name=f"{r.name} (#{r.id})", value=str(r.id)
                    )
                )
            if len(choices) >= 25:
                break

    return choices


class TemperamentSelect(discord.ui.Select):
    """Dropdown for picking a temperament after using a Stripping potion."""

    def __init__(
        self,
        choices: list[str],
        racer_id: int,
        potion_id: int,
        sessionmaker,
    ):
        options = [
            discord.SelectOption(label=t, value=t) for t in choices
        ]
        super().__init__(
            placeholder="Choose a new temperament...",
            options=options,
        )
        self.racer_id = racer_id
        self.potion_id = potion_id
        self.sessionmaker = sessionmaker

    async def callback(self, interaction: discord.Interaction) -> None:
        chosen = self.values[0]
        async with self.sessionmaker() as session:
            racer = await derby_repo.get_racer(session, self.racer_id)
            if racer is None:
                await interaction.response.send_message(
                    "Racer not found.", ephemeral=True
                )
                return
            old_temp = racer.temperament
            racer.temperament = chosen
            await session.commit()

        await interaction.response.send_message(
            f"**{racer.name}**'s temperament changed from "
            f"**{old_temp}** to **{chosen}**!",
            ephemeral=True,
        )
        self.view.stop()


class Potions(commands.Cog, name="potions"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    potion = commands.hybrid_group(
        name="potion", description="Potion Panic potion commands"
    )

    @potion.command(name="list", description="View your potion inventory")
    async def potion_list(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            potions = await brew_repo.get_player_potions(session, user_id, guild_id)

        if not potions:
            await context.send(
                "You don't have any potions. Brew with 100+ potency to create one!",
                ephemeral=True,
            )
            return

        # Group by potion type
        grouped: dict[str, list] = {}
        for p in potions:
            grouped.setdefault(p.potion_type, []).append(p)

        embed = discord.Embed(
            title="\U0001f9ea Potion Inventory",
            color=0x9B59B6,
        )

        for ptype, plist in grouped.items():
            desc = brew_potions.POTION_DESCRIPTIONS.get(ptype, "")
            names = [p.potion_name for p in plist]
            name_counts: dict[str, int] = {}
            for n in names:
                name_counts[n] = name_counts.get(n, 0) + 1
            display = []
            for name, count in name_counts.items():
                if count > 1:
                    display.append(f"{name} x{count}")
                else:
                    display.append(name)

            embed.add_field(
                name=", ".join(display),
                value=desc,
                inline=False,
            )

        await context.send(embed=embed)

    @potion.command(name="use", description="Use a potion from your inventory")
    @app_commands.describe(
        potion="The potion to use",
        target="Racer or ingredient (depends on potion type)",
    )
    @app_commands.autocomplete(potion=_potion_autocomplete, target=_target_autocomplete)
    async def potion_use(
        self, context: Context, potion: str, target: str = None
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            # Resolve potion from inventory
            try:
                potion_id = int(potion)
            except ValueError:
                await context.send("Invalid potion selection.", ephemeral=True)
                return

            potion_obj = await brew_repo.get_player_potion(session, potion_id)
            if potion_obj is None or potion_obj.user_id != user_id or potion_obj.guild_id != guild_id:
                await context.send(
                    "Potion not found in your inventory.", ephemeral=True
                )
                return

            ptype = potion_obj.potion_type
            effect = potion_obj.effect_value

            # --- Stat buff potions (create RacerBuff) ---
            if ptype in brew_potions.STAT_BUFF_MAP:
                if target is None:
                    await context.send(
                        "You must specify a racer to use this potion on.",
                        ephemeral=True,
                    )
                    return
                racer = await derby_repo.get_racer(session, int(target))
                if racer is None or racer.owner_id != user_id or racer.guild_id != guild_id:
                    await context.send("Racer not found.", ephemeral=True)
                    return

                buff_type = brew_potions.STAT_BUFF_MAP[ptype]
                await derby_repo.create_racer_buff(
                    session,
                    racer_id=racer.id,
                    guild_id=guild_id,
                    buff_type=buff_type,
                    value=effect,
                )
                await brew_repo.delete_player_potion(session, potion_obj.id)

                if buff_type == "all_stats":
                    stat_text = f"Speed, Cornering, and Stamina +{effect}"
                elif buff_type == "mood":
                    stat_text = f"Mood +{effect}"
                else:
                    stat_text = f"{buff_type.capitalize()} +{effect}"

                await context.send(
                    f"**{racer.name}** drinks the **{potion_obj.potion_name}**! "
                    f"({stat_text} for their next race)",
                    ephemeral=True,
                )
                return

            # --- Healing ---
            if ptype == "healing":
                if target is None:
                    await context.send(
                        "You must specify a racer to heal.", ephemeral=True
                    )
                    return
                racer = await derby_repo.get_racer(session, int(target))
                if racer is None or racer.owner_id != user_id or racer.guild_id != guild_id:
                    await context.send("Racer not found.", ephemeral=True)
                    return
                if racer.injury_races_remaining <= 0:
                    await context.send(
                        f"**{racer.name}** isn't injured.", ephemeral=True
                    )
                    return

                old_remaining = racer.injury_races_remaining
                racer.injury_races_remaining = max(
                    0, racer.injury_races_remaining - effect
                )
                if racer.injury_races_remaining == 0:
                    racer.injuries = ""
                healed = old_remaining - racer.injury_races_remaining
                await session.commit()
                await brew_repo.delete_player_potion(session, potion_obj.id)

                if racer.injury_races_remaining == 0:
                    await context.send(
                        f"**{racer.name}** is fully healed! "
                        f"(reduced by {healed} races)",
                        ephemeral=True,
                    )
                else:
                    await context.send(
                        f"**{racer.name}** healed {healed} races of injury. "
                        f"({racer.injury_races_remaining} remaining)",
                        ephemeral=True,
                    )
                return

            # --- Fertility ---
            if ptype == "fertility":
                if target is None:
                    await context.send(
                        "You must specify a female racer.", ephemeral=True
                    )
                    return
                racer = await derby_repo.get_racer(session, int(target))
                if racer is None or racer.owner_id != user_id or racer.guild_id != guild_id:
                    await context.send("Racer not found.", ephemeral=True)
                    return
                if racer.gender != "F":
                    await context.send(
                        f"**{racer.name}** is not female. "
                        "Fertility potions only work on female racers.",
                        ephemeral=True,
                    )
                    return
                if racer.foal_count <= 0:
                    await context.send(
                        f"**{racer.name}** already has all breeding slots available.",
                        ephemeral=True,
                    )
                    return

                old_count = racer.foal_count
                racer.foal_count = max(0, racer.foal_count - effect)
                restored = old_count - racer.foal_count
                await session.commit()
                await brew_repo.delete_player_potion(session, potion_obj.id)

                await context.send(
                    f"**{racer.name}** has {restored} breeding slot(s) restored! "
                    f"(foals: {racer.foal_count}/3)",
                    ephemeral=True,
                )
                return

            # --- Longevity ---
            if ptype == "longevity":
                if target is None:
                    await context.send(
                        "You must specify a racer.", ephemeral=True
                    )
                    return
                racer = await derby_repo.get_racer(session, int(target))
                if racer is None or racer.owner_id != user_id or racer.guild_id != guild_id:
                    await context.send("Racer not found.", ephemeral=True)
                    return

                peak_extension = round(
                    effect * (racer.peak_end / racer.career_length)
                )
                racer.career_length += effect
                racer.peak_end += peak_extension
                await session.commit()
                await brew_repo.delete_player_potion(session, potion_obj.id)

                await context.send(
                    f"**{racer.name}**'s career extended by {effect} races! "
                    f"(Career: {racer.career_length}, Peak: {racer.peak_end})",
                    ephemeral=True,
                )
                return

            # --- Stripping (temperament reroll with choices) ---
            if ptype == "stripping":
                if target is None:
                    await context.send(
                        "You must specify a racer.", ephemeral=True
                    )
                    return
                racer = await derby_repo.get_racer(session, int(target))
                if racer is None or racer.owner_id != user_id or racer.guild_id != guild_id:
                    await context.send("Racer not found.", ephemeral=True)
                    return

                choices = brew_potions.generate_stripping_choices(
                    racer.temperament, effect, seed=potion_obj.id
                )

                # Delete potion now (consumed on use)
                await brew_repo.delete_player_potion(session, potion_obj.id)

            # Send the select menu outside the session context
            if ptype == "stripping":
                view = discord.ui.View(timeout=60)
                view.add_item(
                    TemperamentSelect(
                        choices=choices,
                        racer_id=racer.id,
                        potion_id=potion_obj.id,
                        sessionmaker=self.bot.scheduler.sessionmaker,
                    )
                )
                await context.send(
                    f"**{racer.name}** (currently **{racer.temperament}**) — "
                    f"choose a new temperament:",
                    view=view,
                    ephemeral=True,
                )
                return

            # --- Mutation ---
            if ptype == "mutation":
                if target is None:
                    await context.send(
                        "You must specify a racer.", ephemeral=True
                    )
                    return
                async with self.bot.scheduler.sessionmaker() as session:
                    racer = await derby_repo.get_racer(session, int(target))
                    if racer is None or racer.owner_id != user_id or racer.guild_id != guild_id:
                        await context.send("Racer not found.", ephemeral=True)
                        return

                    stat_name, old_val, new_val = brew_potions.apply_mutation(
                        racer.speed, racer.cornering, racer.stamina,
                        effect, seed=random.randint(0, 2**31),
                    )
                    setattr(racer, stat_name, new_val)
                    await session.commit()
                    await brew_repo.delete_player_potion(session, potion_id)

                change = new_val - old_val
                arrow = "\u2191" if change > 0 else ("\u2193" if change < 0 else "\u2194")
                await context.send(
                    f"**{racer.name}**'s {stat_name} mutated! "
                    f"{old_val} {arrow} {new_val} ({change:+d})",
                    ephemeral=True,
                )
                return

            # --- Fortification ---
            if ptype == "fortification":
                async with self.bot.scheduler.sessionmaker() as session:
                    await brew_repo.create_player_brew_effect(
                        session,
                        user_id=user_id,
                        guild_id=guild_id,
                        effect_type="fortification",
                        effect_value=effect,
                    )
                    await brew_repo.delete_player_potion(session, potion_id)

                await context.send(
                    f"Your next brew's minimum explosion threshold is now **{effect}**!",
                    ephemeral=True,
                )
                return

            # --- Foresight ---
            if ptype == "foresight":
                async with self.bot.scheduler.sessionmaker() as session:
                    await brew_repo.create_player_brew_effect(
                        session,
                        user_id=user_id,
                        guild_id=guild_id,
                        effect_type="foresight",
                        effect_value=0,
                    )
                    await brew_repo.delete_player_potion(session, potion_id)

                await context.send(
                    "Your next brew will reveal its explosion threshold!",
                    ephemeral=True,
                )
                return

            # --- Revelation ---
            if ptype == "revelation":
                if target is None:
                    await context.send(
                        "You must specify an ingredient to reveal.",
                        ephemeral=True,
                    )
                    return
                async with self.bot.scheduler.sessionmaker() as session:
                    ing = await brew_repo.get_ingredient_by_id(
                        session, int(target)
                    )
                    if ing is None:
                        await context.send(
                            "Ingredient not found.", ephemeral=True
                        )
                        return

                    already = await brew_repo.is_ingredient_revealed(
                        session, user_id, guild_id, ing.id
                    )
                    if already:
                        await context.send(
                            f"You already know **{ing.name}**'s tags!",
                            ephemeral=True,
                        )
                        return

                    await brew_repo.create_revealed_ingredient(
                        session,
                        user_id=user_id,
                        guild_id=guild_id,
                        ingredient_id=ing.id,
                    )
                    await brew_repo.delete_player_potion(session, potion_id)

                await context.send(
                    f"**{ing.name}** revealed: "
                    f"**{ing.tag_1}** / **{ing.tag_2}**",
                    ephemeral=True,
                )
                return

            await context.send(
                f"Unknown potion type: {ptype}", ephemeral=True
            )


async def setup(bot) -> None:
    await bot.add_cog(Potions(bot))
