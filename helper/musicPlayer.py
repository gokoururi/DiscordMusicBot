import asyncio
from typing import Any, Coroutine
from discord.interactions import Interaction
import yt_dlp
import os
from datetime import timedelta
from discord import enums, errors, ui, voice_client, ButtonStyle, Color, Embed, FFmpegPCMAudio, Guild, Interaction, Message, PCMVolumeTransformer, TextChannel
from discord.ext import commands
from tinydb import TinyDB, Query

class YTDLSource(PCMVolumeTransformer):
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

class Video():
    def __init__(self, filename: str, data: dict):
        self.filename = filename
        self.data = data

class VideoRequest():
    def __init__(self, interaction: Interaction, url: str):
        self.interaction = interaction
        self.url = url

class ControlsButtonStop(ui.Button):
    def __init__(self):
        super().__init__(style=ButtonStyle.red, label="", emoji="◼️")
    
    async def callback(self, interaction: Interaction) -> Coroutine[Any, Any, Any]:
        await interaction.response.send_message("Stop", ephemeral=True)

class ControlsButtonPause(ui.Button):
    def __init__(self):
        super().__init__(style=ButtonStyle.gray, label="", emoji="⏸️")
    
    async def callback(self, interaction: Interaction) -> Coroutine[Any, Any, Any]:
        await interaction.response.send_message("Pause", ephemeral=True)

class ControlsButtonLastTrack(ui.Button):
    def __init__(self):
        super().__init__(style=ButtonStyle.gray, label="", disabled=True, emoji="⏮️")
    
    async def callback(self, interaction: Interaction) -> Coroutine[Any, Any, Any]:
        await interaction.response.send_message("Last Track", ephemeral=True)

class ControlsButtonNextTrack(ui.Button):
    def __init__(self):
        super().__init__(style=ButtonStyle.gray, label="", emoji="⏭️")
    
    async def callback(self, interaction: Interaction) -> Coroutine[Any, Any, Any]:
        await interaction.response.send_message("Next Track", ephemeral=True)

class Controls():
    def __init__(self, player, db: TinyDB, guild: Guild):
        self.db = db
        self.guild = guild
        self.player: Player = player
    
    async def draw(self):
        infoChannel = self.getInfoChannel()
        if not infoChannel:
            print("Cannot draw controls: infoChannel undefined.")
        print(f"Drawing controls to channel '{infoChannel.name}'")

        embed = Embed(
            title="Music Player",
            description=None,
            color=Color.brand_green()
        )
        self.addNowPlayingToEmbed(embed)
        self.addQueueToEmbed(embed)
            
        view = ui.View()
        view.add_item(ControlsButtonLastTrack())
        view.add_item(ControlsButtonPause())
        view.add_item(ControlsButtonStop())
        view.add_item(ControlsButtonNextTrack())

        myLastMessage = await self.getMyLastMessage(infoChannel)
        channelLastMessage = await self.getChannelLastMessage(infoChannel)
        if channelLastMessage and myLastMessage and channelLastMessage.id == myLastMessage.id:
            await myLastMessage.edit(content=None, embed=embed, view=view, suppress=False)
            return

        if myLastMessage:
            await myLastMessage.delete()
        message = await infoChannel.send(content=None, embed=embed, view=view, suppress_embeds=False)
        self.db.update({'lastMessage': message.id}, Query().id == self.guild.id)

    def addNowPlayingToEmbed(self, embed: Embed) -> None: 
        embed.add_field(name="🎶 Now playing", value="", inline=False)

        if len(self.player.queue) <= 0:
            embed.add_field(name="Track", value=f"-", inline=True)
            embed.add_field(name="By", value="-", inline=True)
            embed.add_field(name="Duration", value="-", inline=True)
            return

        video = self.player.queue[0].data
        embed.add_field(name="Track", value=f"[{video['title']}](https://www.youtube.com/watch?v={video['id']})", inline=True)
        embed.add_field(name="By", value="<username>", inline=True)
        embed.add_field(name="Duration", value=self.getVideoDuration(video), inline=True)

    def addQueueToEmbed(self, embed: Embed) -> None:
        if len(self.player.queue) < 2:
            return

        tracks = []
        users = []
        durations = []
        for index, item in enumerate(self.player.queue):
            if index == 0:
                continue
            video = item.data
            tracks.append(f"[{video['title']}](https://www.youtube.com/watch?v={video['id']})")
            users.append("<username>")
            durations.append(self.getVideoDuration(video))

        embed.add_field(name='\u200b', value="")
        embed.add_field(name="🗒️ Queue", value="", inline=False)
        embed.add_field(name="Track", value="\n".join(tracks), inline=True)
        embed.add_field(name="By", value="\n".join(users), inline=True)
        embed.add_field(name="Duration", value="\n".join(durations), inline=True)
    
    def getVideoDuration(self, video) -> str:
        try:
            duration = str(timedelta(seconds=float(video["formats"][0]["fragments"][0]["duration"])))
        except:
            print("Failed to get video duration")
            duration = 0
        return duration

    async def getChannelLastMessage(self, channel: TextChannel) -> Message: 
        try:
            return await channel.fetch_message(channel.last_message_id)
        except errors.NotFound:
            return None
    
    async def getMyLastMessage(self, channel: TextChannel) -> Message:
        result = self.db.search(Query().id == self.guild.id)
        if not result or 'lastMessage' not in result[0]:
            return None
        lastMessageID = int(result[0]["lastMessage"])
        try:
            return await channel.fetch_message(lastMessageID)
        except errors.NotFound:
            return None


    def getInfoChannel(self) -> TextChannel | None:
        result = self.db.search(Query().id == self.guild.id)
        if not result or 'infoChannel' not in result[0]:
            return None
        infoChannelName = result[0]["infoChannel"]
        for channel in self.guild.channels:
            if channel.type == enums.ChannelType.text and channel.name == infoChannelName:
                return channel
        return None
    
class Player():
    bot: commands.Bot
    voiceClient: voice_client.VoiceClient = None
    db: TinyDB = None
    downloadQueue: list[VideoRequest] = []
    queue: list[Video] = []
    isDownloading = False
    isPlaying = False
    controls: Controls = None
    executable: str

    def __init__(self, executable: str, bot: commands.Bot, db: TinyDB, guild: Guild):
        self.executable = executable
        self.bot = bot
        self.db = db
        self.controls = Controls(self, db, guild)
    
    async def addVideo(self, interaction: Interaction, url: str):
        if not await self.validUrl(interaction, url):
            return

        if not await self.connectToVoice(interaction):
            return

        await interaction.response.send_message(f"Understood. I will begin downloading '{url}'.", ephemeral=True)
        self.downloadQueue.append(VideoRequest(interaction, url))
        await self.download()
    
    async def download(self):
        if self.isDownloading:
            return

        while self.downloadQueue:
            self.isDownloading = True
            video = self.downloadQueue[0]
            filename, data = await YTDLSource.from_url(video.url, loop=self.bot.loop)
            await video.interaction.edit_original_response(content=f"Successfully downloaded '{video.url}'")
            self.queue.append(Video(filename, data))
            self.downloadQueue.pop(0)
            await self.play()
        self.isDownloading = False
    
    async def play(self):
        await self.controls.draw()
        if self.isPlaying:
            return
        self.isPlaying = True
        song = self.queue[0]
        loop = asyncio.get_event_loop()
        self.voiceClient.play(
            FFmpegPCMAudio(executable=self.executable, source=song.filename),
            after =lambda e=None: loop.create_task(self.after_play(e))
        )
    
    async def after_play(self, error):
        print("I've done it. I played a song")
        if error:
            raise error
        print("No error during playback")

        self.queue.pop(0)
        await self.controls.draw()
        self.isPlaying = False
        if len(self.queue) <= 0 or not self.voiceClient.is_connected():
            print("Stopping playback")
            return

        await self.play()

    async def validUrl(self, interaction: Interaction, url: str) -> bool:
        if not url.startswith("https://www.youtube.com/watch?v="):
            await interaction.response.send_message(
                f"Invalid URL. Expecting URL in format `https://www.youtube.com/watch?v=<VIDEO_ID>`",
                ephemeral=True, suppress_embeds=True)
            return False
        return True


    async def connectToVoice(self, interaction) -> bool:
        if not interaction.user.voice:
            await interaction.response.send_message(f"You are not in a voice channel.", ephemeral=True)
            return False

        if not self.voiceClient or not self.voiceClient.is_connected():
            self.voiceClient = await interaction.user.voice.channel.connect()
            print("Connected to voice client")
        
        return True

SAVE_PATH = os.getenv("save_path")
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
ytdl = yt_dlp.YoutubeDL(ytdlFormatOptions)
players: dict[int, Player] = {}

def getPlayer(executable:str, guild: Guild, bot: commands.Bot, db: TinyDB) -> Player:
    if not guild.id in players:
        print(f"Creating Player for guild '{guild.name}'")
        players[guild.id] = Player(executable, bot, db, guild)
    return players[guild.id]