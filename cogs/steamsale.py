import asyncio
import time
import json
import os
import re
from typing import Optional
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands
import logging

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "steamsale_config.json"))
CHECK_INTERVAL_SECONDS = 60  # how often the background task checks for updates
YOUTUBE_LINK = "https://www.youtube.com/watch?v=bUo1PgKksgw"


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            # migrate legacy single channel_id to per-guild channels map
            if isinstance(data, dict) and "channel_id" in data:
                cid = data.pop("channel_id")
                channels = data.get("channels") or {}
                # store legacy channel under special key 'legacy' so existing setups continue to receive announcements
                channels.setdefault("legacy", cid)
                data["channels"] = channels
            # ensure keys exist
            if "channels" not in data:
                data["channels"] = {}
            if "manual_next_sale" not in data:
                data["manual_next_sale"] = None
            return data
    except Exception:
        return {"channels": {}, "manual_next_sale": None}


def _save_config(data: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


class SteamSale(commands.Cog):
    """Cog that checks for upcoming sales and announces when one starts."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = _load_config()
        self._task: Optional[asyncio.Task] = None
        self._next_sale = None  # dict with keys: name, start (epoch)
        self._notified_sales = set()
        # tracking to avoid announcing sales that already started while the bot was offline
        self._last_observed_sale_start: Optional[int] = None
        self._last_observed_started: Optional[bool] = None

    async def cog_load(self):
        """Async init hook called when the cog is added to the bot."""
        # start the background loop; it will wait until the bot is ready
        self._task = asyncio.create_task(self._background_loop())

        # start a non-blocking background task to register the slash command
        self._register_task: Optional[asyncio.Task] = asyncio.create_task(self._register_slash_command())

    async def _register_slash_command(self):
        """Create and sync the /steamsale slash command without blocking cog_load."""
        async def _slash_steamsale(interaction: discord.Interaction):
            if not self._next_sale:
                self._next_sale = await self._get_next_sale()
                if not self._next_sale:
                    await interaction.response.send_message("Couldn't fetch Steam sale info right now.", ephemeral=True)
                    return
            name = self._next_sale.get("name")
            start = self._next_sale.get("start")
            timestr = self._format_time_until(start)
            ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(start))
            await interaction.response.send_message(f"Next Steam sale: '{name}' starts in {timestr} (at {ts})")

        try:
            # avoid duplicate registration
            try:
                existing = self.bot.tree.get_command("steamsale")
            except Exception:
                existing = None
            if existing:
                return

            cmd = app_commands.Command(name="steamsale", description="Show time until the next big Steam sale", callback=_slash_steamsale)
            self.bot.tree.add_command(cmd)

            # register slash command to set manual time
            async def _slash_settime(interaction: discord.Interaction, time: str, name: Optional[str] = "Manual Sale"):
                epoch = self._parse_time_string(time)
                if epoch is None:
                    await interaction.response.send_message("Could not parse the time. Use ISO-8601 like 2025-12-20T15:00:00Z or 'YYYY-MM-DD HH:MM' (UTC).", ephemeral=True)
                    return
                self.config["manual_next_sale"] = {"name": name, "start": epoch}
                _save_config(self.config)
                ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(epoch))
                await interaction.response.send_message(f"Manual next sale set: '{name}' at {ts}")

            async def _slash_cleartime(interaction: discord.Interaction):
                self.config["manual_next_sale"] = None
                _save_config(self.config)
                await interaction.response.send_message("Cleared manual next-sale override.")

            set_cmd = app_commands.Command(name="steamsale_settime", description="Set manual next sale time (admin)", callback=_slash_settime)
            clear_cmd = app_commands.Command(name="steamsale_cleartime", description="Clear manual next sale override (admin)", callback=_slash_cleartime)
            self.bot.tree.add_command(set_cmd)
            self.bot.tree.add_command(clear_cmd)

            # Wait asynchronously for application_id to be available; do not block startup.
            waited = 0
            timeout = 300  # seconds to wait for application_id
            while waited < timeout and not getattr(self.bot, "application_id", None):
                await asyncio.sleep(1)
                waited += 1

            if not getattr(self.bot, "application_id", None):
                logger.warning("application_id not set after %s seconds; skipping command sync", timeout)
                return

            try:
                await self.bot.tree.sync()
                logger.info("SteamSale slash command synced")
            except Exception as e:
                logger.warning("Failed to sync application commands: %s", e, exc_info=True)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Unexpected error while registering slash command")

    async def cog_unload(self):
        # cancel background tasks
        if getattr(self, "_register_task", None):
            try:
                self._register_task.cancel()
                await self._register_task
            except Exception:
                pass
            self._register_task = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    async def _get_next_sale(self):
        # Only manual override is supported now
        manual = self.config.get("manual_next_sale")
        if manual and isinstance(manual, dict):
            try:
                return {"name": manual.get("name", "Manual Sale"), "start": int(manual.get("start"))}
            except Exception:
                logger.warning("Invalid manual_next_sale in config; ignoring")
        logger.debug("No manual_next_sale configured; no next sale available")
        return None

    async def _background_loop(self):
        # ensure bot is ready before doing repeated network work
        try:
            await self.bot.wait_until_ready()
        except Exception:
            pass

        while True:
            try:
                next_sale = await self._get_next_sale()
                if next_sale:
                    now = int(time.time())
                    start = int(next_sale["start"])
                    started = now >= start

                    # if we have not observed this sale before, record its state but do NOT announce
                    if self._last_observed_sale_start != start:
                        # new sale observed — set tracking state, reset notified flag for this start
                        self._last_observed_sale_start = start
                        self._last_observed_started = started
                        self._next_sale = next_sale
                        self._notified_sales.discard(start)
                    else:
                        # same sale as last observed — announce only on transition from not-started to started
                        if (not self._last_observed_started) and started and (start not in self._notified_sales):
                            await self._announce_sale(next_sale)
                            self._notified_sales.add(start)
                        # update observed state
                        self._last_observed_started = started
                        self._next_sale = next_sale
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception:
                # swallow and continue
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _announce_sale(self, sale: dict):
        channels = self.config.get("channels", {}) or {}
        if not channels:
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(sale["start"]))
        content = f"Steam Sale '{sale.get('name')}' has started at {ts}!\nWatch: {YOUTUBE_LINK}"
        # send announcement to all configured channel IDs
        for key, cid in list(channels.items()):
            try:
                channel = self.bot.get_channel(int(cid))
                if not channel:
                    channel = await self.bot.fetch_channel(int(cid))
                if not channel:
                    logger.warning("Configured channel id %s (key=%s) not found; skipping", cid, key)
                    continue
                await channel.send(content)
            except Exception:
                logger.exception("Failed to send announcement to channel %s (key=%s)", cid, key)

    def _format_time_until(self, epoch: int) -> str:
        now = int(time.time())
        diff = max(0, epoch - now)
        days, rem = divmod(diff, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds and not parts:
            parts.append(f"{seconds}s")
        return " ".join(parts) if parts else "now"

    def _parse_time_string(self, timestr: str) -> Optional[int]:
        """Parse a time string into epoch seconds (UTC). Accepts ISO-8601 (with Z) or common formats.
        Returns epoch seconds or None on failure.
        """
        if not timestr:
            return None
        s = timestr.strip()
        # support trailing Z (UTC)
        try:
            if s.endswith("Z"):
                s2 = s[:-1] + "+00:00"
                dt = datetime.fromisoformat(s2)
                return int(dt.timestamp())
            # try plain ISO / with offset
            try:
                dt = datetime.fromisoformat(s)
                # if naive, assume UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            except Exception:
                # try common formats
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        dt = datetime.strptime(s, fmt)
                        dt = dt.replace(tzinfo=timezone.utc)
                        return int(dt.timestamp())
                    except Exception:
                        continue
        except Exception:
            return None
        return None

    @commands.command(name="steamsale", help="Show time until the next big Steam sale")
    async def steamsale_cmd(self, ctx: commands.Context):
        if not self._next_sale:
            # try to fetch once
            self._next_sale = await self._get_next_sale()
            if not self._next_sale:
                logger.warning("steamsale command: failed to obtain next sale data")
                await ctx.send("Couldn't fetch Steam sale info right now. Check bot logs for details.")
                return
        name = self._next_sale.get("name")
        start = self._next_sale.get("start")
        timestr = self._format_time_until(start)
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(start))
        await ctx.send(f"Next Steam sale: '{name}' starts in {timestr} (at {ts})")

    @commands.command(name="steamsale_setchannel", help="Set the channel where sale announcements will be posted. Usage: steamsale_setchannel #channel")
    @commands.has_permissions(administrator=True)
    async def set_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        guild_id = str(ctx.guild.id) if ctx.guild else "unknown"
        channels = self.config.get("channels") or {}
        channels[guild_id] = channel.id
        self.config["channels"] = channels
        _save_config(self.config)
        await ctx.send(f"Configured steamsale announcements to channel {channel.mention} for this server")

    @commands.command(name="steamsale_unsetchannel", help="Unset the announcement channel")
    @commands.has_permissions(administrator=True)
    async def unset_channel(self, ctx: commands.Context):
        guild_id = str(ctx.guild.id) if ctx.guild else None
        channels = self.config.get("channels") or {}
        if guild_id and guild_id in channels:
            channels.pop(guild_id, None)
            self.config["channels"] = channels
            _save_config(self.config)
            await ctx.send("Cleared steamsale announcement channel for this server.")
        else:
            await ctx.send("No steamsale announcement channel configured for this server.")

    @commands.command(name="steamsale_settime", help="Set the next Steam sale time manually. Usage: steamsale_settime <time> [Sale Name]. Time may include a space, e.g. 2025-09-29 17:00")
    @commands.has_permissions(administrator=True)
    async def set_time(self, ctx: commands.Context, *, arg: str):
        """Accept the rest of the message, extract a datetime and optional name."""
        if not arg:
            await ctx.send("Usage: steamsale_settime <time> [Sale Name]")
            return
        # look for an ISO-like date/time (e.g. 2025-09-29 or 2025-09-29 17:00 or 2025-09-29T17:00:00Z)
        m = re.search(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+\-]\d{2}:\d{2})?)?", arg)
        if not m:
            # fallback: try the whole string
            time_str = arg.strip().strip('"\'')
            name = "Manual Sale"
        else:
            time_str = m.group(0).strip().strip('"\'')
            # take the remainder after the matched time as the name (preferred)
            name = arg[m.end():].strip()
            # if nothing after, try before the match (in case user wrote 'Name 2025-09-29 17:00')
            if not name:
                name = arg[:m.start()].strip()
            if not name:
                name = "Manual Sale"

        epoch = self._parse_time_string(time_str)
        if epoch is None:
            await ctx.send("Could not parse the time. Use ISO-8601 like 2025-12-20T15:00:00Z or 'YYYY-MM-DD HH:MM' (UTC).")
            return
        # store
        self.config["manual_next_sale"] = {"name": name, "start": epoch}
        _save_config(self.config)
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(epoch))
        await ctx.send(f"Manual next sale set: '{name}' at {ts}")

    @commands.command(name="steamsale_cleartime", help="Clear manual next-sale override")
    @commands.has_permissions(administrator=True)
    async def clear_time(self, ctx: commands.Context):
        self.config["manual_next_sale"] = None
        _save_config(self.config)
        await ctx.send("Cleared manual next-sale override.")


async def setup(bot: commands.Bot):
    """Minimal extension setup: add the cog. The cog's async cog_load handles slash command registration and sync safely."""
    await bot.add_cog(SteamSale(bot))
