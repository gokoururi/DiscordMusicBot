import asyncio

import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import youtube_dl
import re


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
    'no_warnings': False,
    'default_search': 'auto',
    'source_address': '0.0.0.0'  # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpegOptions = {
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
        filename = data['title'] if stream else ytdl.prepare_filename(data)
        return filename


def get_song_name(filename):
    song = re.search('(.*)-.*', filename)
    if song:
        return re.sub('_', " ", song.group(1))
    else:
        return f"Wakarimasen! {filename}"


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


@bot.command(name='play')
async def play(ctx, url):
    try:
        server = ctx.message.guild
        voice_channel = server.voice_client
        async with ctx.typing():
            filename = await YTDLSource.from_url(url, loop=bot.loop)
            voice_channel.play(discord.FFmpegPCMAudio(executable="ffmpeg.exe", source=filename))
        await ctx.send(f"**p-playing** :sparkles:{get_song_name(filename)}:sparkles:")
    except Exception as err:
        await ctx.send("Not connected to voice channel")
        print(f"Error: {err=}")


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
    else:
        await ctx.send("s-stop? I wasn't doing anything!")

if __name__ == '__main__':
    bot.run(DISCORDTOKEN)
