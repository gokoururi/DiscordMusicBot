import discord
import os
from tinydb import TinyDB, Query
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from helper import musicPlayer
from helper.infoChannel import InfoChannel

load_dotenv()
DISCORDTOKEN = os.getenv("discord_token")
FFMPEG_EXECUTABLE = os.getenv("ffmpeg_executable")
COMMAND_PREFIX = os.getenv("command_prefix")
THUMBNAIL_URL = os.getenv("thumbnail_url")

databaseDir = f"{os.path.dirname(__file__)}/database"
if not os.path.exists(databaseDir):
    os.makedirs(databaseDir)
db = TinyDB(f'{databaseDir}/guild.json')

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=discord.Intents.all())

@bot.event
async def on_ready():
    print("Bot is running")
    for guild in bot.guilds:
        await musicPlayer.getPlayer(FFMPEG_EXECUTABLE, guild, bot, db).controls.draw()

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)

@bot.tree.command(name="setinfo", description="Select to which channel I will output information")
async def setinfo(interaction: discord.Interaction):
    await InfoChannel(db).set(interaction)

@bot.tree.command(name="play", description="Play youtube video in voice channel")
@app_commands.describe(youtube_url = "URL to youtube video you want to play")
async def play(interaction: discord.Interaction, youtube_url: str):
    player: musicPlayer.Player = musicPlayer.getPlayer(FFMPEG_EXECUTABLE, interaction.guild, bot, db)
    await player.addVideo(interaction, youtube_url)

bot.run(DISCORDTOKEN)