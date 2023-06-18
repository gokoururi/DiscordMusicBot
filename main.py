import asyncio
import discord
from discord import Message
from discord.ext import commands
import os
from dotenv import load_dotenv
import yt_dlp
from typing import Dict, List


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = ""

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return filename, data
        # return cls(await discord.FFmpegOpusAudio.from_probe(filename, **ffmpeg_options), data)


class Session:
    def __init__(self, voice_client):
        print("Session start")
        self.queue: List = []
        self.download_queue: List = []
        self.downloading = False
        self.voice_client: discord.voice_client.VoiceClient = voice_client
        self.maintenance_task = None
        self.last_playing_message = None

    async def add_to_download_queue(self, ctx: discord.ext.commands.context.Context, url):
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

    async def start_download(self, ctx: discord.ext.commands.context.Context):
        print("Starting download")
        self.downloading = True
        download = self.download_queue[0]
        await self.download_and_add_to_queue(ctx, download["message"], download["url"])

    async def download_and_add_to_queue(self, ctx: discord.ext.commands.context.Context, message: Message, url):
        filename, data = await YTDLSource.from_url(url, loop=bot.loop)
        await message.add_reaction("☑️")
        await message.remove_reaction("⬇️", bot.user)
        self.queue.append({"filename": filename, "data": data})
        self.download_queue.pop(0)
        self.downloading = False
        print("Download Finished")
        if len(self.download_queue) > 0:
            loop = asyncio.get_event_loop()
            loop.create_task(self.start_download(ctx))

    async def print_playing_and_queue(self, ctx: discord.ext.commands.context.Context):
        message = None
        queue_content = ""
        embed = discord.Embed(title=None, description=None, color=discord.Color.teal())
        embed.set_thumbnail(url=THUMBNAIL_URL)
        for index, song in enumerate(self.queue):
            if index == 0:
                embed.add_field(name="Now playing", value=f"{song['data']['title']}", inline=False)
                continue
            queue_content += f"\n〖{index}〗 {song['data']['title']}"
        if len(self.queue) > 1:
            embed.add_field(name="Queue", value=queue_content, inline=False)

        if self.last_playing_message:
            message = self.last_playing_message
        ctx.channel.history()
        last_channel_message: Message = await (ctx.channel.history(limit=1)).__anext__()
        if last_channel_message.author.id == bot.user.id:
            message = last_channel_message

        if message:
            await self.last_playing_message.edit(embed=embed)
        else:
            self.last_playing_message = await ctx.send(embed=embed)

    async def start_playing(self, ctx: discord.ext.commands.context.Context):
        song = self.queue[0]
        loop = asyncio.get_event_loop()
        self.voice_client.play(
            discord.FFmpegPCMAudio(executable=FFMPEG_EXECUTABLE, source=song['filename']),
            after=lambda e=None: loop.create_task(self.after_play(ctx, e)))
        await self.print_playing_and_queue(ctx)
        if not self.maintenance_task:
            self.maintenance_task = loop.create_task(self.maintenance())

    async def after_play(self, ctx: discord.ext.commands.context.Context, error):
        if error:
            raise error
        if not self.voice_client.is_connected():
            return
        if len(self.queue) <= 0:
            return

        self.queue.pop(0)
        song_list = []
        for i in self.queue:
            song_list.append(i['data']['title'])
        songs = ', '.join(song_list)
        print(f"Queue: {songs}")
        loop = asyncio.get_event_loop()
        # loop.create_task(ctx.send(f"Queue: {songs}"))
        # await ctx.send(f"Queue: {songs}")
        await self.print_playing_and_queue(ctx)
        if len(self.queue) > 0:
            song = self.queue[0]
            self.voice_client.play(
                discord.FFmpegPCMAudio(executable=FFMPEG_EXECUTABLE, source=song["filename"]),
                after=lambda e=None: loop.create_task(self.after_play(ctx, e)))
            pass

    async def maintenance(self):
        while True:
            await asyncio.sleep(10)
            print(f"Currently in {self.voice_client.channel.name}; Status: Playing {self.voice_client.is_playing()}; "
                  f"Members in VC: {len(self.voice_client.channel.members)}")
            if len(self.voice_client.channel.members) <= 1:
                print(f"Nobody in VC {self.voice_client.channel.name}. Disconnecting.")
                self.voice_client.stop()
                await self.voice_client.disconnect()
                self.maintenance_task = None
                break


load_dotenv()
DISCORDTOKEN = os.getenv("discord_token")
FFMPEG_EXECUTABLE = os.getenv("ffmpeg_executable")
COMMAND_PREFIX = os.getenv("command_prefix")
SAVE_PATH = os.getenv("save_path")
THUMBNAIL_URL = os.getenv("thumbnail_url")

intents = discord.Intents().all()
client = discord.Client(intents=intents)
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)


@bot.command(name='join', help='Join channel')
async def join(ctx: discord.ext.commands.context.Context):
    if not ctx.message.author.voice:
        await ctx.send(f"${ctx.message.author}...you're not connected to a voice channel?? I don't now where to go.")
        return
    else:
        channel = ctx.message.author.voice.channel
    await channel.connect()
    await ctx.guild.change_voice_state(channel=channel, self_mute=False, self_deaf=True)


@bot.command(name='leave', help='Leave channel')
async def leave(ctx: discord.ext.commands.context.Context):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send("I don't feel like it.")


@bot.command(name='play')
async def play(ctx: discord.ext.commands.context.Context, url):
    server_id = ctx.guild.id
    session = None

    if not ctx.author.voice:
        await ctx.send("You need to join a voice channel first.")
        return

    if server_id not in sessions:
        voice_client = await ctx.author.voice.channel.connect()
        if voice_client.is_connected():
            session = Session(voice_client)
            sessions[server_id] = session
    else:
        session = sessions[server_id]
        if not session.voice_client.is_connected():
            channel = ctx.author.voice.channel
            voice_client = await channel.connect()
            session.voice_client = voice_client
        elif session.voice_client.channel != ctx.author.voice.channel:
            await session.voice_client.move_to(ctx.author.voice.channel)

    # await session.add_to_queue(ctx, url)
    await session.add_to_download_queue(ctx, url)


@bot.command(name='pause')
async def pause(ctx: discord.ext.commands.context.Context):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_playing():
        voice_client.pause()
    else:
        await ctx.send("Whats wrong with you?")


@bot.command(name='resume')
async def resume(ctx: discord.ext.commands.context.Context):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_paused():
        voice_client.resume()
    else:
        await ctx.send("You're not very bright, are you?")


@bot.command(name='skip')
async def skip(ctx: discord.ext.commands.context.Context, *, song=None):
    server_id = ctx.guild.id
    if server_id not in sessions:
        await ctx.send("You're not in any voice channel")
        return
    session = sessions[server_id]
    sessions[server_id].last_playing_message = None
    if not session.voice_client.is_playing():
        await ctx.send("I'm not currently playing naything")
        return
    if not song:
        session.voice_client.stop()
    else:
        session.queue.pop(int(song))
    await ctx.message.add_reaction("☑️")


@bot.command(name='queue')
async def queue(ctx: discord.ext.commands.context.Context):
    server_id = ctx.guild.id
    if server_id not in sessions:
        await ctx.send("I can't do that")
        return
    sessions[server_id].last_playing_message = None
    await sessions[server_id].print_playing_and_queue(ctx)


@bot.command(name='stop')
async def stop(ctx: discord.ext.commands.context.Context):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_playing():
        sessions[ctx.guild.id].queue = []
        voice_client.stop()
    else:
        await ctx.send("I'll stop your existence.")


if __name__ == '__main__':
    yt_dlp.utils.bug_reports_message = lambda: ''
    ytdlFormatOptions = {
        'format': 'bestaudio/best',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': False,
        'no_warnings': False,
        'default_search': 'auto',
        'source_address': '0.0.0.0',  # bind to ipv4 since ipv6 addresses cause issues sometimes
        'outtmpl': {
            "default": f"{SAVE_PATH}/%(title)s.%(ext)s"
        }
    }
    ffmpeg_options = {
        'options': '-vn'
    }
    ytdl = yt_dlp.YoutubeDL(ytdlFormatOptions)
    sessions: Dict[int, Session] = {}
    bot.run(DISCORDTOKEN)
