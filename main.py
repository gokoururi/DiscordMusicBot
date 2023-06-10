import asyncio
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import youtube_dl
from typing import Dict, List


load_dotenv()
DISCORDTOKEN = os.getenv("discordToken")

intents = discord.Intents().all()
client = discord.Client(intents=intents)
bot = commands.Bot(command_prefix='!', intents=intents)

youtube_dl.utils.bug_reports_message = lambda: ''
ytdlFormatOptions = {
    'format': 'bestaudio/best',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': False,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'  # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {
    'options':  '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdlFormatOptions)


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


@bot.command(name='join', help='Join channel')
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send(f"${ctx.message.author}...you're not connected to a voice channel?? I don't now where to go.")
        return
    else:
        channel = ctx.message.author.voice.channel
    await channel.connect()
    await ctx.guild.change_voice_state(channel=channel, self_mute=False, self_deaf=True)


@bot.command(name='leave', help='Leave channel')
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send("uwaaaah... s-something went wrong")


class Session:
    def __init__(self, voice_client):
        print("Session start")
        self.queue: List = []
        self.voice_client = voice_client

    async def add_to_queue(self, ctx, url):
        message = await ctx.send("Downloading...")
        filename, data = await YTDLSource.from_url(url, loop=bot.loop)
        await message.edit(content="Downloading...done.")
        self.queue.append({"filename": filename, "data": data})

    async def start_playing(self, ctx):
        song = self.queue[0]
        loop = asyncio.get_event_loop()
        async with ctx.typing():
            self.voice_client.play(
                discord.FFmpegPCMAudio(executable="ffmpeg.exe", source=song['filename']),
                after=lambda e=None: loop.create_task(self.after_play(ctx, e)))
        await bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.listening, name=song['data']['title']))
        await ctx.send(f"**Now playing**: {song['data']['title']}")

    async def after_play(self, ctx, error):
        if error:
            raise error
        self.queue.pop(0)
        await bot.change_presence(activity=None)
        song_list = []
        for i in self.queue:
            song_list.append(i['data']['title'])
        songs = ', '.join(song_list)
        print(f"Queue: {songs}")
        loop = asyncio.get_event_loop()
        # loop.create_task(ctx.send(f"Queue: {songs}"))
        await ctx.send(f"Queue: {songs}")
        if len(self.queue) > 0:
            song = self.queue[0]
            self.voice_client.play(
                discord.FFmpegPCMAudio(executable="ffmpeg.exe", source=song["filename"]),
                after=lambda e=None: loop.create_task(self.after_play(ctx, e)))
            pass
        else:
            await bot.change_presence(activity=None)


sessions: Dict[int, Session] = {}


@bot.command(name='play')
async def play(ctx, url):
    server_id = ctx.guild.id
    session = None
    if server_id not in sessions:
        if ctx.author.voice is None:
            await ctx.send("y-you're not in any voice channel...")
            return
        channel = ctx.author.voice.channel
        voice_client = await channel.connect()
        if voice_client.is_connected():
            session = Session(voice_client)
            sessions[server_id] = session
    else:
        session = sessions[server_id]
        if session.voice_client.channel != ctx.author.voice.channel:
            await session.voice_client.move_to(ctx.author.voice.channel)

    await session.add_to_queue(ctx, url)
    if not session.voice_client.is_playing():
        await session.start_playing(ctx)


@bot.command(name='pause')
async def pause(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_playing():
        voice_client.pause()
    else:
        await ctx.send("I-I'm not playing anything r-right now.")


@bot.command(name='resume')
async def resume(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_paused():
        voice_client.resume()
    else:
        await ctx.send("What d-do you mean 'resume'? I wasn't p-playing anything.")


@bot.command(name='stop')
async def stop(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_playing():
        voice_client.stop()
        await bot.change_presence(activity=None)
    else:
        await ctx.send("s-stop? I wasn't doing anything!")

if __name__ == '__main__':
    bot.run(DISCORDTOKEN)
