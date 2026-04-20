from __future__ import annotations

import discord
from discord.ext import commands
from discord.ext.commands import Context

import checks
from derby import repositories as repo
from config import resolve_guild_setting


# ---------------------------------------------------------------------------
# Help content — one dict per game
# ---------------------------------------------------------------------------

DERBY_HELP_CATEGORIES = {
    "Getting Started": (
        "New here? Start with these:\n"
        "`/wallet` — Check your balance (auto-creates on first use)\n"
        "`/stable browse` — See racers for sale\n"
        "`/stable buy <racer>` — Purchase a racer\n"
        "`/race upcoming` — See the next race and odds"
    ),
    "Racing & Betting": (
        "Races run on a schedule. Bet before they start!\n"
        "`/race upcoming` — See next race, racers, and odds\n"
        "`/race bet` — Open the interactive betting slip\n"
        "Or click the quick-bet buttons on the race announcement!\n"
        "`/race history` — Recent race results\n"
        "*Broke? Bet with amount 0 for a free house bet!*"
    ),
    "Your Stable": (
        "Manage your racers and make them competitive.\n"
        "`/stable` — View your owned racers\n"
        "`/stable manage <racer>` — **Interactive panel** to train, rest, feed, sell, or rename\n"
        "`/stable view <racer>` — Full profile with training costs\n"
        "`/stable upgrade` — Buy an extra stable slot\n"
        "*Rest can be used once per race cycle. Training is limited per cycle too.*"
    ),
    "Breeding": (
        "Breed racers to produce foals with inherited traits.\n"
        "`/stable breed <male> <female>` — Breed two racers (25 coins)\n"
        "Foals inherit 1 stat from parents, 2 are random.\n"
        "Females can produce up to 3 foals. Males have no limit.\n"
        "Foals need training sessions before they can race."
    ),
    "Tournaments": (
        "Weekly brackets by rank with big prizes.\n"
        "`/tournament register <racer>` — Enter next tournament\n"
        "`/tournament cancel <racer>` — Withdraw registration\n"
        "`/tournament list` — See pending tournaments\n"
        "Ranks: D, C, B, A, S — train stats to rank up!"
    ),
}

FISHING_HELP_CATEGORIES = {
    "AFK Fishing": (
        "Cast a line, walk away, come back to coins. The original way.\n"
        "`/fish start <location> [bait]` — Begin an AFK session\n"
        "`/fish stop` — End early and refund unused bait\n"
        "`/fish status` — Check your current session\n"
        "Catches happen automatically every 10-35 minutes "
        "(faster with better rods, bait, and skill). "
        "No legendaries in AFK \u2014 those only appear when you're watching."
    ),
    "Active Fishing": (
        "Sit at the water, actually. Bites come every 30-90 seconds, "
        "and each one is a different little moment.\n"
        "`/fish active <location> [bait]` — Begin an active session "
        "(mutually exclusive with AFK)\n"
        "\u2022 **Common** catches whisper a weird secret\n"
        "\u2022 **Uncommon** catches ask for a one-word vibe match\n"
        "\u2022 **Rare** catches hand you a haiku with one line blanked\n"
        "\u2022 **Legendary** catches are unique characters with memory "
        "\u2014 up to 3 rounds of conversation to convince them\n"
        "Miss a vibe check or haiku and the fish slips away (bait burned). "
        "Requires an LLM configured on the bot."
    ),
    "Gear & Bait": (
        "Upgrade your rod and stock up on bait before heading out.\n"
        "`/fish gear` — Your rod, bait inventory, and skill level\n"
        "`/fish shop` — Available bait and rod upgrades\n"
        "`/fish buy-bait <type> <amount>` — Stock up\n"
        "`/fish upgrade-rod` — Buy the next rod tier\n"
        "Better rods reduce trash and boost rare odds. "
        "Better bait reduces cast time."
    ),
    "Locations & Progression": (
        "Earn XP with every catch to unlock harder locations.\n"
        "`/fish locations` — All spots and their unlock status\n"
        "Skill-gated: Calm Pond (Lv1) \u2192 River Rapids (Lv2) \u2192 "
        "Deep Lake (Lv3) \u2192 Misty Grove (Lv4) \u2192 Cloudmere (Lv5). "
        "Over-leveling a spot gives a small cast-speed bonus."
    ),
    "Collection": (
        "Track every species, earn trophies, keep your haikus.\n"
        "`/fish log [location]` — Species caught, records, missing\n"
        "`/fish trophies` — Per-location completion progress\n"
        "`/fish haiku mine` — Your saved rare-catch haikus\n"
        "`/fish haiku random` — Post a random guild haiku (public!)\n"
        "Catch every non-trash species at a location to earn a "
        "**trophy**: +10% cast speed there, permanently."
    ),
}

BREWING_HELP_CATEGORIES = {
    "The Basics": (
        "Combine ingredients in a cauldron to build potency — "
        "but push too far and it explodes!\n"
        "`/brew start` — Light the cauldron and begin a brew\n"
        "`/brew add <ingredient>` — Toss an ingredient in\n"
        "`/brew cashout` — Finish and collect your reward\n"
        "`/brew status` — Check your current brew"
    ),
    "Ingredients": (
        "Every ingredient has hidden tags that affect your brew.\n"
        "`/ingredients` — View your ingredient inventory\n"
        "`/ingredients shop` — Buy ingredients from the daily shop\n"
        "Some ingredients are free, others cost coins. "
        "The shop rotates daily."
    ),
    "Brewing Tips": (
        "Each ingredient adds **potency** (good) and **instability** (risky). "
        "If instability hits the cauldron's hidden threshold — boom!\n"
        "You can only add each ingredient once per brew.\n"
        "`/brew journal` — Review your past brews\n"
        "`/brew analyze <ingredient>` — See an ingredient's brew history\n"
        "*Experiment and keep notes — the best brewers learn from every batch.*"
    ),
}

DUNGEON_HELP_CATEGORIES = {
    "Getting Started": (
        "Delve into dungeons, fight monsters, and keep what you find.\n"
        "`/dungeon delve` — Pick a dungeon and start a run (private thread)\n"
        "`/dungeon delve <name>` — Jump straight into a specific dungeon\n"
        "`/dungeon stats` — View your character sheet (add `show:True` to share)\n"
        "`/dungeon abandon` — Abandon your current run (lose all loot)\n"
        "**Dungeons:** The Goblin Warrens, The Undercrypt"
    ),
    "Combat & Exploration": (
        "Rooms are revealed one at a time — no peeking ahead!\n"
        "**Combat** — Attack, Defend, Use Item, or Flee each round\n"
        "**Treasure** — Free gold, scaled by dungeon tier\n"
        "**Traps** — DEX check to avoid damage\n"
        "**Rest Shrines** — Heal 30% of your max HP\n"
        "**Bosses** — Tougher monsters guarding the floor exit\n"
        "Defeat the boss to descend or retreat with your loot."
    ),
    "Stats & Leveling": (
        "Earn XP from kills to level up and gain stat points.\n"
        "`/dungeon allocate <stat>` — Spend a stat point on STR, DEX, or CON\n"
        "**STR** — Melee damage bonus\n"
        "**DEX** — Dodge, trap avoidance, flee chance\n"
        "**CON** — Max HP (HP = CON \u00d7 2 + accessory bonus)"
    ),
    "Gear & Shop": (
        "Buy gear with gold and manage your loadout.\n"
        "`/dungeon shop` — Browse weapons, armor, accessories, and consumables\n"
        "`/dungeon inventory` — Equip, unequip, and view your stash\n"
        "Gear tiers unlock as you level: common (Lv1), uncommon (Lv3), "
        "rare (Lv5), epic (Lv8).\n"
        "Enchanted gear (+1/+2) drops from bosses and requires STR/DEX to equip.\n"
        "Gear found in dungeons is kept on safe return, lost on death."
    ),
    "Death & Rewards": (
        "**Safe return** — Keep all gold and found gear/items\n"
        "**Death** — Lose 50% of run gold and all found gear. XP is kept.\n"
        "Gold is deposited into your shared wallet on return.\n"
        "Consumables from your inventory persist between runs — "
        "found consumables are added to your stash on return.\n"
        "\U0001f3a3 Fishing bait and \U0001f9ea brewing ingredients found in dungeons are "
        "awarded instantly and kept even on death."
    ),
}

RACES_HELP_CATEGORIES = {
    "Choosing a Race": (
        "Each race gives unique passives across all mini-games.\n"
        "`/player choose` — Pick your race (free, one-time)\n"
        "`/player info [race]` — View passives and flaw\n"
        "`/player change <race>` — Switch race (escalating gold cost)"
    ),
    "The Races": (
        "**Human** — +15% XP everywhere, +10% brew payout\n"
        "**Dwarf** — Stoneblood (survive one killing blow), +40% rest heal\n"
        "**Elf** — Twin Cast (10% double fish), crits on 19-20\n"
        "**Halfling** — Lucky (double treasure/loot rolls), +15% bet payouts\n"
        "**Orc** — Bloodrage (damage advantage below 50% HP), 2.25\u00d7 dungeon HP"
    ),
}


# ---------------------------------------------------------------------------
# Help Cog
# ---------------------------------------------------------------------------


class Help(commands.Cog, name="help"):
    """Unified help system — works in any game channel."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        return await checks.in_any_game_channel(ctx)

    def _racer_emoji(self, gs=None) -> str:
        return resolve_guild_setting(gs, self.bot.settings, "racer_emoji")

    @commands.hybrid_group(name="help", description="Show help for any mini-game")
    async def help_command(self, context: Context) -> None:
        if context.invoked_subcommand is not None:
            return
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild_id)
        emoji = self._racer_emoji(gs)
        embed = discord.Embed(
            title="Downtime Help",
            description=(
                "Pick a topic to learn more:\n\n"
                f"{emoji} `/help derby` — Racing, betting, stables, breeding, and tournaments\n"
                "\U0001f9ea `/help brewing` — Potion Panic ingredient brewing\n"
                "\U0001f3a3 `/help fishing` — Lazy Lures AFK fishing and progression\n"
                "\U0001f9df `/help dungeon` — Monster Mash dungeon crawling\n"
                "\U0001f9d9 `/help races` — Player races and passives"
            ),
            color=0x3498DB,
        )
        await context.send(embed=embed, ephemeral=True)

    @help_command.command(name="derby", description="Show Downtime Derby commands and tips")
    async def help_derby(self, context: Context) -> None:
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild_id)
        emoji = self._racer_emoji(gs)
        embed = discord.Embed(
            title=f"{emoji} Downtime Derby — Help",
            description="Everything you need to race, bet, train, breed, and compete.",
            color=0x3498DB,
        )
        for category, text in DERBY_HELP_CATEGORIES.items():
            embed.add_field(name=category, value=text, inline=False)
        embed.set_footer(text="Use /stable manage <racer> to train, rest, feed, sell, and rename in one place.")
        await context.send(embed=embed, ephemeral=True)

    @help_command.command(name="brewing", description="Show Potion Panic brewing commands and tips")
    async def help_brewing(self, context: Context) -> None:
        embed = discord.Embed(
            title="\U0001f9ea Potion Panic — Help",
            description="Brew ingredients, build potency, and see what you can create.",
            color=0x9B59B6,
        )
        for category, text in BREWING_HELP_CATEGORIES.items():
            embed.add_field(name=category, value=text, inline=False)
        embed.set_footer(text="What happens at higher potency? There's only one way to find out...")
        await context.send(embed=embed, ephemeral=True)

    @help_command.command(name="fishing", description="Show Lazy Lures fishing commands and tips")
    async def help_fishing(self, context: Context) -> None:
        embed = discord.Embed(
            title="\U0001f3a3 Lazy Lures — Help",
            description=(
                "Two ways to fish. AFK is set-and-forget. "
                "Active is strange little moments with the water."
            ),
            color=0x3498DB,
        )
        for category, text in FISHING_HELP_CATEGORIES.items():
            embed.add_field(name=category, value=text, inline=False)
        embed.set_footer(
            text="Use /fish notify to toggle DM alerts for catches."
        )
        await context.send(embed=embed, ephemeral=True)

    @help_command.command(name="dungeon", description="Show Monster Mash dungeon crawling commands and tips")
    async def help_dungeon(self, context: Context) -> None:
        embed = discord.Embed(
            title="\U0001f9df Monster Mash — Help",
            description="Fight monsters, dodge traps, and loot dungeons — solo and turn-by-turn.",
            color=0xE74C3C,
        )
        for category, text in DUNGEON_HELP_CATEGORIES.items():
            embed.add_field(name=category, value=text, inline=False)
        embed.set_footer(text="Each run is played in a private thread. Good luck in there!")
        await context.send(embed=embed, ephemeral=True)

    @help_command.command(name="races", description="Show player race info and passives")
    async def help_races(self, context: Context) -> None:
        embed = discord.Embed(
            title="\U0001f9d9 Player Races — Help",
            description="Choose a race for unique passives across every mini-game.",
            color=0x2ECC71,
        )
        for category, text in RACES_HELP_CATEGORIES.items():
            embed.add_field(name=category, value=text, inline=False)
        embed.set_footer(text="Use /player info <race> to see full details on any race.")
        await context.send(embed=embed, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Help(bot))
