import logging
import os
from dotenv import load_dotenv
from discord.ext import commands
import yt_dlp
import discord

load_dotenv()
DISCORD_TOKEN = os.getenv("discord_token")
COMMAND_PREFIX = os.getenv("command_prefix", "!")
SAVE_PATH = os.getenv("save_path", ".")
FFMPEG_EXECUTABLE = os.getenv("ffmpeg_executable")
THUMBNAIL_URL = os.getenv("thumbnail_url", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

intents = discord.Intents().all()
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)


def setup_bot(bot: commands.Bot):
    """Attach runtime helpers and configuration to the bot object."""
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


if __name__ == '__main__':
    import asyncio

    async def main():
        setup_bot(bot)
        try:
            from cogs.music import Music
            await bot.add_cog(Music(bot))
            logger.info("Music cog loaded")
        except Exception as e:
            logger.exception("Failed to load Music cog: %s", e)
        await bot.start(DISCORD_TOKEN)

    asyncio.run(main())
