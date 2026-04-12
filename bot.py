import json
import logging
import os
import platform
from datetime import datetime

import discord
import sentry_sdk
from discord.ext import commands
from discord.ext.commands import Context
from dotenv import load_dotenv

from config import Settings
from derby.scheduler import DerbyScheduler

load_dotenv()
sentry_sdk.init(os.getenv("SENTRY_DSN"))

intents = discord.Intents.default()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "time": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "guild_id") and record.guild_id is not None:
            log_record["guild_id"] = record.guild_id
        if hasattr(record, "race_id") and record.race_id is not None:
            log_record["race_id"] = record.race_id
        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


logger = logging.getLogger("discord_bot")
logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(JsonFormatter())
# File handler
file_handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
file_handler.setFormatter(JsonFormatter())

# Add the handlers
logger.addHandler(console_handler)
logger.addHandler(file_handler)


class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or(os.getenv("PREFIX")),
            intents=intents,
            help_command=None,
        )
        self.logger = logger
        self.bot_prefix = os.getenv("PREFIX")
        self.invite_link = os.getenv("INVITE_LINK")
        self.settings: Settings | None = None
        self.scheduler: DerbyScheduler | None = None

    async def load_cogs(self) -> None:
        """
        The code in this function is executed whenever the bot will start.
        """
        for file in os.listdir(f"{os.path.realpath(os.path.dirname(__file__))}/cogs"):
            if file.endswith(".py"):
                extension = file[:-3]
                try:
                    await self.load_extension(f"cogs.{extension}")
                    self.logger.info(f"Loaded extension '{extension}'")
                except Exception as e:
                    exception = f"{type(e).__name__}: {e}"
                    self.logger.error(
                        f"Failed to load extension {extension}\n{exception}"
                    )

    async def setup_hook(self) -> None:
        """
        This will just be executed when the bot starts the first time.
        """
        self.logger.info(f"Logged in as {self.user.name}")
        self.logger.info(f"discord.py API version: {discord.__version__}")
        self.logger.info(f"Python version: {platform.python_version()}")
        self.logger.info(
            f"Running on: {platform.system()} {platform.release()} ({os.name})"
        )
        self.logger.info("-------------------")
        self.settings = Settings.from_yaml()
        await self.load_cogs()
        guild_ids = os.getenv("SYNC_GUILD_IDS", "")
        for gid in guild_ids.split(","):
            gid = gid.strip()
            if gid:
                guild = discord.Object(id=int(gid))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
        self.scheduler = DerbyScheduler(self)
        await self.scheduler.start()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Initialize guild settings when the bot joins a new server."""
        self.logger.info(
            f"Joined guild {guild.name} (ID: {guild.id})",
            extra={"guild_id": guild.id},
        )
        if self.scheduler:
            from derby import repositories as repo

            async with self.scheduler.sessionmaker() as session:
                existing = await repo.get_guild_settings(session, guild.id)
                if existing is None:
                    await repo.create_guild_settings(
                        session, guild_id=guild.id
                    )
            await self.scheduler._replenish_pool(guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Log when the bot is removed from a server.  Data is kept intact
        in case the bot is re-invited later."""
        self.logger.info(
            f"Removed from guild {guild.name} (ID: {guild.id})",
            extra={"guild_id": guild.id},
        )

    async def on_message(self, message: discord.Message) -> None:
        """
        The code in this event is executed every time someone sends a message, with or without the prefix

        :param message: The message that was sent.
        """
        if message.author == self.user or message.author.bot:
            return
        await self.process_commands(message)

    async def on_command_completion(self, context: Context) -> None:
        """
        The code in this event is executed every time a normal command has been *successfully* executed.

        :param context: The context of the command that has been executed.
        """
        full_command_name = context.command.qualified_name
        split = full_command_name.split(" ")
        executed_command = str(split[0])
        if context.guild is not None:
            self.logger.info(
                f"Executed {executed_command} command in {context.guild.name} (ID: {context.guild.id}) by {context.author} (ID: {context.author.id})",
                extra={"guild_id": context.guild.id},
            )
            # Persist to database for analytics
            try:
                if self.scheduler:
                    from derby import repositories as repo

                    async with self.scheduler.sessionmaker() as session:
                        await repo.log_command(
                            session,
                            guild_id=context.guild.id,
                            user_id=context.author.id,
                            command=full_command_name,
                            cog=context.command.cog_name or "unknown",
                        )
            except Exception:
                self.logger.debug("Failed to log command to DB", exc_info=True)
        else:
            self.logger.info(
                f"Executed {executed_command} command by {context.author} (ID: {context.author.id}) in DMs"
            )

    async def on_command_error(self, context: Context, error) -> None:
        """
        The code in this event is executed every time a normal valid command catches an error.

        :param context: The context of the normal command that failed executing.
        :param error: The error that has been faced.
        """
        if isinstance(error, commands.CommandOnCooldown):
            minutes, seconds = divmod(error.retry_after, 60)
            hours, minutes = divmod(minutes, 60)
            hours = hours % 24
            embed = discord.Embed(
                description=f"**Please slow down** - You can use this command again in {f'{round(hours)} hours' if round(hours) > 0 else ''} {f'{round(minutes)} minutes' if round(minutes) > 0 else ''} {f'{round(seconds)} seconds' if round(seconds) > 0 else ''}.",
                color=0xE02B2B,
            )
            await context.send(embed=embed, ephemeral=True)
        elif isinstance(error, commands.NotOwner):
            embed = discord.Embed(
                description="You are not the owner of the bot!", color=0xE02B2B
            )
            await context.send(embed=embed, ephemeral=True)
            if context.guild:
                self.logger.warning(
                    f"{context.author} (ID: {context.author.id}) tried to execute an owner only command in the guild {context.guild.name} (ID: {context.guild.id}), but the user is not an owner of the bot.",
                    extra={"guild_id": context.guild.id},
                )
            else:
                self.logger.warning(
                    f"{context.author} (ID: {context.author.id}) tried to execute an owner only command in the bot's DMs, but the user is not an owner of the bot."
                )
        elif isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                description="You are missing the permission(s) `"
                + ", ".join(error.missing_permissions)
                + "` to execute this command!",
                color=0xE02B2B,
            )
            await context.send(embed=embed, ephemeral=True)
        elif isinstance(error, commands.BotMissingPermissions):
            embed = discord.Embed(
                description="I am missing the permission(s) `"
                + ", ".join(error.missing_permissions)
                + "` to fully perform this command!",
                color=0xE02B2B,
            )
            await context.send(embed=embed, ephemeral=True)
        elif isinstance(error, commands.CheckFailure):
            # Channel restriction check already sends its own message
            if "wrong channel" in str(error):
                return
            embed = discord.Embed(
                description="You don't have permission to do that!",
                color=0xE02B2B,
            )
            await context.send(embed=embed, ephemeral=True)
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="Error!",
                # We need to capitalize because the command arguments have no capital letter in the code and they are the first word in the error message.
                description=str(error).capitalize(),
                color=0xE02B2B,
            )
            await context.send(embed=embed, ephemeral=True)
        else:
            # Suppress interaction-expired (10062) and already-acknowledged (40060)
            # errors — these are harmless Discord API quirks with hybrid commands.
            # HybridCommandError wraps CommandInvokeError wraps the real exception,
            # so we walk the full .original chain to find the root cause.
            inner = error
            while hasattr(inner, "original"):
                inner = inner.original
            if isinstance(inner, discord.HTTPException) and inner.code in (
                10062,
                40060,
            ):
                return
            self.logger.error(
                "Unhandled command error",
                exc_info=error,
                extra={"guild_id": context.guild.id if context.guild else None},
            )
            sentry_sdk.capture_exception(error)
            try:
                await context.send("An unexpected error occurred.", ephemeral=True)
            except discord.HTTPException:
                pass  # Interaction may have expired


if __name__ == "__main__":
    bot = DiscordBot()
    bot.run(os.getenv("TOKEN"))
