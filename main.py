import asyncio
import logging
import os
from typing import Dict, List, Optional

import discord
from discord import Message
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp


# ---------------------------
# Configuration & Logging
# ---------------------------
load_dotenv()
DISCORD_TOKEN = os.getenv("discord_token")
FFMPEG_EXECUTABLE = os.getenv("ffmpeg_executable")
COMMAND_PREFIX = os.getenv("command_prefix", "!")
SAVE_PATH = os.getenv("save_path", ".")
THUMBNAIL_URL = os.getenv("thumbnail_url", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

intents = discord.Intents().all()
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)


# ---------------------------
# YTDL wrapper
# ---------------------------
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = ""

    @classmethod
    async def from_url(cls, ytdl: yt_dlp.YoutubeDL, url: str, *, loop: Optional[asyncio.AbstractEventLoop] = None, stream: bool = False):
        """Download (or prepare streaming) for a given URL using the provided ytdl instance.

        Returns (filename, data) where filename is a path or direct URL depending on `stream`.
        """
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return filename, data


# ---------------------------
# Session (per-guild music state)
# ---------------------------
class Session:
    def __init__(self, bot: commands.Bot, voice_client: discord.VoiceClient):
        self.bot = bot
        self.queue: List[Dict] = []
        self.download_queue: List[Dict] = []
        self.downloading: bool = False
        self.voice_client: discord.VoiceClient = voice_client
        self.maintenance_task: Optional[asyncio.Task] = None
        self.last_playing_message: Optional[Message] = None
        self.loop_mode: Optional[str] = None
        logger.info(f"Session started for guild={voice_client.guild.id}")

    async def add_to_download_queue(self, ctx: commands.Context, url: str):
        message: Message = ctx.message
        await message.add_reaction("⬇️")
        self.download_queue.append({
            "message": message,
            "url": url
        })
        self.last_playing_message = None
        if not self.downloading:
            await self.start_download(ctx)
            if not self.voice_client.is_playing():
                await self.start_playing(ctx)

    async def start_download(self, ctx: commands.Context):
        if not self.download_queue:
            return
        logger.info("Starting download task")
        self.downloading = True
        download = self.download_queue[0]
        await self.download_and_add_to_queue(ctx, download["message"], download["url"])

    async def download_and_add_to_queue(self, ctx: commands.Context, message: Message, url: str):
        filename, data = await YTDLSource.from_url(self.bot.ytdl, url, loop=self.bot.loop)
        await message.add_reaction("☑️")
        await message.remove_reaction("⬇️", self.bot.user)
        self.queue.append({"filename": filename, "data": data})
        self.download_queue.pop(0)
        self.downloading = False
        logger.info("Download finished: %s", data.get('title'))
        if len(self.download_queue) > 0:
            # schedule next download
            asyncio.get_event_loop().create_task(self.start_download(ctx))

    async def print_playing_and_queue(self, ctx: commands.Context):
        embed = discord.Embed(color=discord.Color.teal())
        if self.bot.thumbnail_url:
            embed.set_thumbnail(url=self.bot.thumbnail_url)

        now_playing_prefix = "🔁 " if self.loop_mode == "one" else ""

        queue_content = ""
        for index, song in enumerate(self.queue):
            if index == 0:
                embed.add_field(name="Now playing", value=f"{now_playing_prefix}{song['data']['title']}", inline=False)
                continue
            queue_content += f"\n〖{index}〗 {song['data']['title']}"

        if len(self.queue) > 1:
            embed.add_field(name="Queue", value=queue_content, inline=False)
        if self.loop_mode:
            embed.add_field(name="Loop mode", value=self.loop_mode)

        # Try to reuse last bot message if it is still the last message in channel
        message = self.last_playing_message
        last_channel_message: Message = await (ctx.channel.history(limit=1)).__anext__()
        if last_channel_message.author.id == self.bot.user.id:
            message = last_channel_message

        if message:
            await message.edit(embed=embed)
            self.last_playing_message = message
        else:
            self.last_playing_message = await ctx.send(embed=embed)

    async def start_playing(self, ctx: commands.Context):
        if not self.queue:
            return
        song = self.queue[0]
        loop = asyncio.get_event_loop()
        self.voice_client.play(
            discord.FFmpegPCMAudio(executable=self.bot.ffmpeg_executable, source=song['filename']),
            after=lambda e=None: loop.create_task(self.after_play(ctx, e)))
        await self.print_playing_and_queue(ctx)
        if not self.maintenance_task:
            self.maintenance_task = loop.create_task(self.maintenance())

    async def after_play(self, ctx: commands.Context, error):
        if error:
            # Let discord.py's error handling surface this in logs; avoid crashing the task
            logger.exception("Error during playback: %s", error)
            return
        if not self.voice_client.is_connected():
            return

        if self.loop_mode == "all":
            self.queue.append(self.queue[0])
            self.queue.pop(0)
        elif not self.loop_mode:
            self.queue.pop(0)

        if len(self.queue) <= 0:
            return

        await self.print_playing_and_queue(ctx)
        loop = asyncio.get_event_loop()
        if len(self.queue) > 0:
            song = self.queue[0]
            self.voice_client.play(
                discord.FFmpegPCMAudio(executable=self.bot.ffmpeg_executable, source=song["filename"]),
                after=lambda e=None: loop.create_task(self.after_play(ctx, e)))

    async def maintenance(self):
        while True:
            await asyncio.sleep(10)
            try:
                members = len(self.voice_client.channel.members)
                logger.debug(
                    "Guild %s | Channel %s | Playing=%s | Members=%s",
                    self.voice_client.guild.id,
                    self.voice_client.channel.name,
                    self.voice_client.is_playing(),
                    members,
                )
                if members <= 1:
                    logger.info("Nobody left in VC %s. Disconnecting.", self.voice_client.channel.name)
                    self.voice_client.stop()
                    await self.voice_client.disconnect()
                    self.maintenance_task = None
                    break
            except Exception:
                logger.exception("Error during maintenance task")
                break


# ---------------------------
# Bot setup: attach helpers to bot instance
# ---------------------------

def setup_bot(bot: commands.Bot):
    # ytdl options
    ytdl_format_options = {
        'format': 'bestaudio/best',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': False,
        'no_warnings': False,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
        'outtmpl': {"default": f"{SAVE_PATH}/%(title)s.%(ext)s"}
    }
    bot.ytdl = yt_dlp.YoutubeDL(ytdl_format_options)
    bot.ffmpeg_options = {'options': '-vn'}
    bot.ffmpeg_executable = FFMPEG_EXECUTABLE
    bot.save_path = SAVE_PATH
    bot.thumbnail_url = THUMBNAIL_URL
    bot.sessions = {}


# ---------------------------
# Commands
# ---------------------------
@bot.command(name='join', help='Join channel')
async def join(ctx: commands.Context):
    if not ctx.author.voice:
        await ctx.send(f"{ctx.author}...you're not connected to a voice channel.")
        return
    channel = ctx.author.voice.channel
    await channel.connect()
    await ctx.guild.change_voice_state(channel=channel, self_mute=False, self_deaf=True)


@bot.command(name='leave', help='Leave channel')
async def leave(ctx: commands.Context):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send("I'm not connected to a voice channel.")


@bot.command(name='playlocal')
async def playlocal(ctx: commands.Context, filename: str):
    server_id = ctx.guild.id
    session = ctx.bot.sessions.get(server_id)
    if not session:
        await ctx.send("No active session. Use play to create one.")
        return
    session.voice_client.play(discord.FFmpegPCMAudio(executable=ctx.bot.ffmpeg_executable, source=filename))


@bot.command(name='play')
async def play(ctx: commands.Context, *urls: str):
    if not ctx.author.voice:
        await ctx.send("You need to join a voice channel first.")
        return

    server_id = ctx.guild.id
    session = ctx.bot.sessions.get(server_id)

    if not session:
        voice_client = await ctx.author.voice.channel.connect()
        session = Session(ctx.bot, voice_client)
        ctx.bot.sessions[server_id] = session
    else:
        # ensure voice connection matches author's voice channel
        if not session.voice_client.is_connected():
            voice_client = await ctx.author.voice.channel.connect()
            session.voice_client = voice_client
        elif session.voice_client.channel != ctx.author.voice.channel:
            await session.voice_client.move_to(ctx.author.voice.channel)

    for url in urls:
        await session.add_to_download_queue(ctx, url)


@bot.command(name='pause')
async def pause(ctx: commands.Context):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.pause()
    else:
        await ctx.send("Nothing is playing.")


@bot.command(name='resume')
async def resume(ctx: commands.Context):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_paused():
        voice_client.resume()
    else:
        await ctx.send("Nothing to resume.")


@bot.command(name='skip')
async def skip(ctx: commands.Context, *, song: Optional[int] = None):
    server_id = ctx.guild.id
    session = ctx.bot.sessions.get(server_id)
    if not session:
        await ctx.send("You're not in any voice channel")
        return
    session.last_playing_message = None
    if not session.voice_client.is_playing():
        await ctx.send("I'm not currently playing anything")
        return
    if song is None:
        session.voice_client.stop()
    else:
        try:
            session.queue.pop(int(song))
        except Exception:
            await ctx.send("Invalid queue index")
            return
    await ctx.message.add_reaction("☑️")


@bot.command(name='loop')
async def loop_cmd(ctx: commands.Context, *, mode: str = "one"):
    server_id = ctx.guild.id
    session = ctx.bot.sessions.get(server_id)
    if not session:
        await ctx.send("You're not in any voice channel")
        return
    if mode in ("one", "all"):
        session.loop_mode = mode
    elif mode == "stop":
        session.loop_mode = None
    else:
        await ctx.send(f"Invalid loop mode '{mode}'")
        return
    await ctx.message.add_reaction("☑️")


@bot.command(name='queue')
async def queue_cmd(ctx: commands.Context):
    server_id = ctx.guild.id
    session = ctx.bot.sessions.get(server_id)
    if not session:
        await ctx.send("No active session.")
        return
    session.last_playing_message = None
    await session.print_playing_and_queue(ctx)


@bot.command(name='stop')
async def stop(ctx: commands.Context):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        session = ctx.bot.sessions.get(ctx.guild.id)
        if session:
            session.queue = []
            session.loop_mode = None
        voice_client.stop()
    else:
        await ctx.send("Nothing to stop.")


# ---------------------------
# Startup
# ---------------------------

if __name__ == '__main__':
    setup_bot(bot)
    bot.run(DISCORD_TOKEN)
