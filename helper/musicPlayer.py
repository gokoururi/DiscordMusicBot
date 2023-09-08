import asyncio
import yt_dlp
import os
from discord import voice_client, Guild, Interaction, PCMVolumeTransformer
from discord.ext import commands
from tinydb import TinyDB

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

    
class Player():
    bot: commands.Bot
    voiceClient: voice_client.VoiceClient = None
    db: TinyDB = None
    downloadQueue: list[VideoRequest] = []
    queue: list[Video] = []
    isDownloading = False

    def __init__(self, bot: commands.Bot, db: TinyDB):
        self.bot = bot
        self.db = db
    
    async def play(self, interaction: Interaction, url: str):
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
        self.isDownloading = False
    
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

def getPlayer(guild: Guild, bot: commands.Bot, db: TinyDB) -> Player:
    if not guild.id in players:
        print(f"Creating Player for guild '{guild.name}'")
        players[guild.id] = Player(bot, db)
    return players[guild.id]