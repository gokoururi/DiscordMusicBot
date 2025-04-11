import asyncio
import discord
from discord import Message
from discord.ext import commands
import os
from dotenv import load_dotenv
import yt_dlp
from typing import Dict, List, Optional, Any # Added Optional and Any

# --- Load Environment Variables ---
load_dotenv()
DISCORDTOKEN = os.getenv("discord_token")
FFMPEG_EXECUTABLE = os.getenv("ffmpeg_executable") # Keep FFMPEG path global or pass it down
COMMAND_PREFIX = os.getenv("command_prefix") or "!"
SAVE_PATH = os.getenv("save_path") or "./downloads"
THUMBNAIL_URL = os.getenv("thumbnail_url") # Keep Thumbnail URL global or pass it down

# Create save path directory if it doesn't exist
if SAVE_PATH and not os.path.exists(SAVE_PATH):
    try:
        os.makedirs(SAVE_PATH)
        print(f"Created save directory: {SAVE_PATH}")
    except OSError as e:
        print(f"Error creating directory {SAVE_PATH}: {e}")
        # Decide how to handle this - maybe exit or use a default path?
        SAVE_PATH = "." # Fallback to current directory

# --- Setup ytdl and ffmpeg options ---
yt_dlp.utils.bug_reports_message = lambda: ''
ytdlFormatOptions = {
    'format': 'bestaudio/best',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False, # Set to True to skip unavailable videos
    'logtostderr': False,
    'quiet': True, # Set quiet: True for less console spam during extraction
    'no_warnings': True, # Suppress warnings
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'outtmpl': {
        # Ensure SAVE_PATH is valid before using it here
        "default": os.path.join(SAVE_PATH, '%(title)s.%(ext)s')
    },
    # Request metadata
    'extract_flat': False, # Ensure we get full metadata
    'forcejson': False,
    'dump_single_json': False,
}
ffmpeg_options = {
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5' # Useful for streams
}

# Global ytdl instance (used by the Cog)
try:
    ytdl = yt_dlp.YoutubeDL(ytdlFormatOptions)
except Exception as e:
    print(f"Error initializing yt_dlp: {e}")
    # Handle this critical error, maybe exit
    exit()


# --- Bot Intents and Instantiation ---
intents = discord.Intents.default()
intents.message_content = True # Required for commands
intents.voice_states = True    # Required for voice channel operations
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)


# --- Classes (YTDLSource, Session) ---

class YTDLSource(discord.PCMVolumeTransformer):
    """
    Represents a YouTube DL audio source.
    This class handles extracting info and preparing the stream/file.
    It subclasses PCMVolumeTransformer for potential volume control later.
    """
    def __init__(self, source: discord.AudioSource, *, data: Dict[str, Any], volume: float = 0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', 'Unknown Title')
        # Store more useful metadata if needed
        self.uploader = data.get('uploader', 'Unknown Uploader')
        self.duration = data.get('duration') # Duration in seconds
        self.url = data.get('webpage_url', '') # Original URL

    @classmethod
    async def from_url(cls, url: str, *, loop: asyncio.AbstractEventLoop, ytdl_instance: yt_dlp.YoutubeDL, stream: bool = False) -> Optional[Dict[str, Any]]:
        """
        Class method to extract video info using yt-dlp.
        Accepts the asyncio loop and the ytdl instance.
        Returns the extracted data dictionary or None on failure.
        """
        loop = loop or asyncio.get_event_loop()
        try:
            # Run blocking ytdl extract_info in an executor
            data = await loop.run_in_executor(None, lambda: ytdl_instance.extract_info(url, download=not stream))
            if not data:
                 print(f"yt-dlp extract_info returned None for URL: {url}")
                 return None

            if 'entries' in data:
                # Take first item from a playlist
                data = data['entries'][0]

            # Add filename info to data (needed whether streaming or downloading)
            # ytdl.prepare_filename is safe to call even if not downloading
            data['prepared_filename'] = ytdl_instance.prepare_filename(data)

            return data # Return the whole data dictionary

        except yt_dlp.utils.DownloadError as e:
            print(f"YTDL DownloadError for '{url}': {e}")
            return None
        except Exception as e:
            print(f"Error extracting video info for '{url}': {e}")
            return None

    @classmethod
    async def create_source(cls, ctx: commands.Context, search: str, *, loop: asyncio.AbstractEventLoop, ytdl_instance: yt_dlp.YoutubeDL, stream: bool = False) -> Optional['YTDLSource']:
        """
        Searches for a song, creates the appropriate discord.AudioSource,
        and returns an instance of YTDLSource.
        """
        data = await cls.from_url(search, loop=loop, ytdl_instance=ytdl_instance, stream=stream)
        if not data:
            return None

        # Determine the actual source path/URL
        filename = data['url'] if stream else data['prepared_filename']

        try:
            # Create the audio source using FFmpeg
            audio_source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)
            return cls(audio_source, data=data)
        except discord.ClientException as e:
             print(f"Error creating FFmpegPCMAudio source: {e}")
             await ctx.send(f"Error creating audio source: {e}")
             return None
        except Exception as e:
             print(f"Unexpected error creating audio source: {e}")
             await ctx.send(f"Unexpected error creating audio source: {e}")
             return None


class Session:
    """Holds the state for a music session in a single guild."""
    def __init__(self, cog_instance: commands.Cog, voice_client: discord.VoiceClient):
        print(f"Session start for guild {voice_client.guild.id}")
        # Store references passed from the Cog
        self.cog = cog_instance # Reference to the parent Cog
        self.bot: commands.Bot = cog_instance.bot # Reference to the bot instance
        self.ytdl: yt_dlp.YoutubeDL = cog_instance.ytdl # Reference to the ytdl instance
        self.ffmpeg_executable = cog_instance.ffmpeg_executable # Path to ffmpeg
        self.thumbnail_url = cog_instance.thumbnail_url # Thumbnail for embeds

        self.voice_client: discord.VoiceClient = voice_client
        self.current_source: Optional[YTDLSource] = None # Store the current YTDLSource object
        self.queue: asyncio.Queue[YTDLSource] = asyncio.Queue() # Use asyncio.Queue for async operations
        self.next_event = asyncio.Event() # Event to signal when the next song can play
        self.loop_mode: Optional[str] = None # 'one', 'all', or None
        self.last_playing_message: Optional[Message] = None
        self.volume = 0.5 # Default volume

        # Task for processing the queue
        self.player_task = self.bot.loop.create_task(self.player_loop())
        # Task for auto-disconnect maintenance (optional, can be started on demand)
        self.maintenance_task: Optional[asyncio.Task] = None

    async def add_to_queue(self, ctx: commands.Context, search: str):
        """Adds a song (found by search/URL) to the queue."""
        message = ctx.message
        await message.add_reaction("⏳") # Indicate processing

        source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop, ytdl_instance=self.ytdl, stream=True) # Stream by default

        if source:
            await self.queue.put(source)
            await message.remove_reaction("⏳", self.bot.user)
            await message.add_reaction("✅") # Indicate success
            await ctx.send(f"Added to queue: **{source.title}**", delete_after=15)
            # print(f"Queue size: {self.queue.qsize()}")
            # No need to manually trigger player_loop, it waits on the queue
        else:
            await message.remove_reaction("⏳", self.bot.user)
            await message.add_reaction("❌") # Indicate failure
            await ctx.send(f"Could not process '{search}'. It might be unavailable or an invalid link.", delete_after=15)

    def play_next_song(self, error: Optional[Exception] = None):
        """Callback function used by voice_client.play(after=...). Signals the player loop."""
        if error:
            print(f"Player error: {error}")
            # Optionally send a message to the channel about the error
            # coro = self.voice_client.channel.send(f"Playback error: {error}")
            # self.bot.loop.create_task(coro)

        # Signal the player_loop that the current song is finished
        self.next_event.set()

    async def player_loop(self):
        """Main loop that consumes the queue and plays songs."""
        await self.bot.wait_until_ready() # Ensure bot is ready before starting loop
        while True:
            self.next_event.clear() # Reset event for the next song

            # Wait for the next song to be ready to play
            # This happens either when the queue gets the first item,
            # or when the previous song finishes (play_next_song is called)
            if self.current_source: # If a song just finished
                 # Apply looping logic before getting next song
                 if self.loop_mode == 'one':
                      # Re-queue the current song at the front (requires temporary storage)
                      temp_queue = [self.current_source]
                      while not self.queue.empty():
                           temp_queue.append(await self.queue.get())
                      for item in temp_queue:
                           await self.queue.put(item)
                 elif self.loop_mode == 'all':
                      # Put the finished song back at the end of the queue
                      await self.queue.put(self.current_source)

            # Get the next song from the queue, waiting if empty
            try:
                 # Wait indefinitely for an item, or until the task is cancelled
                 self.current_source = await self.queue.get()
                 # print(f"Got from queue: {self.current_source.title}")
            except asyncio.CancelledError:
                 print("Player loop cancelled.")
                 return # Exit loop if task is cancelled

            # Check if voice client is still valid and connected
            if not self.voice_client or not self.voice_client.is_connected():
                 print("Player loop: Voice client disconnected, stopping.")
                 # Clear current source and potentially the queue
                 self.current_source = None
                 # Consider clearing queue or attempting reconnect later
                 return # Exit or wait for reconnection

            # Play the audio source
            self.current_source.volume = self.volume # Apply current volume
            self.voice_client.play(self.current_source, after=self.play_next_song)
            print(f"Playing: {self.current_source.title}")

            # Send or update the 'Now Playing' message
            await self.print_playing_and_queue(self.current_source.title) # Pass title for clarity

            # Wait until the song finishes (signaled by play_next_song)
            await self.next_event.wait()

            # Song finished, clear current source before next loop iteration
            self.current_source = None
            # print("Song finished, looping back.")


    async def print_playing_and_queue(self, current_title: str):
        """Sends or edits a message showing the currently playing song and queue."""
        embed = discord.Embed(title="Music Player", color=discord.Color.teal())
        if self.thumbnail_url:
            embed.set_thumbnail(url=self.thumbnail_url)

        now_playing_prefix = "🔁 " if self.loop_mode == 'one' else ""
        embed.add_field(name="Now Playing", value=f"{now_playing_prefix}{current_title}", inline=False)

        # Build queue string (limited display)
        queue_content = ""
        items_to_show = 5
        # Access queue items without consuming them (requires temporary list)
        temp_list = []
        while not self.queue.empty() and len(temp_list) < items_to_show:
             item = await self.queue.get()
             temp_list.append(item)

        for i, source in enumerate(temp_list):
             queue_content += f"\n`{i+1}`. {source.title}"
        if self.queue.qsize() > 0: # Check if more items were left in queue
             queue_content += f"\n... and {self.queue.qsize()} more"

        # Put items back into the queue
        for item in reversed(temp_list):
             await self.queue.put(item)
        # Note: This queue peeking is slightly complex. A simpler approach might
        # just show queue size if showing items is too difficult with asyncio.Queue.

        if queue_content:
            embed.add_field(name="Up Next", value=queue_content, inline=False)
        else:
             embed.add_field(name="Up Next", value="Queue is empty", inline=False)

        if self.loop_mode:
            embed.add_field(name="Loop Mode", value=self.loop_mode.capitalize(), inline=True)

        embed.add_field(name="Volume", value=f"{int(self.volume * 100)}%", inline=True)

        # --- Message Handling ---
        channel = self.voice_client.channel # Get channel from VC
        if not channel: return # Should not happen if connected

        # Try editing the last message
        if self.last_playing_message:
            try:
                await self.last_playing_message.edit(embed=embed, view=None) # Add view= for buttons later
                return # Success
            except discord.NotFound:
                print("Last playing message not found, sending new one.")
                self.last_playing_message = None # Clear invalid message ID
            except discord.HTTPException as e:
                print(f"Failed to edit message: {e}. Sending new one.")
                self.last_playing_message = None

        # Send a new message if editing failed or no previous message exists
        try:
            # Delete previous bot messages in channel history (optional cleanup)
            # async for msg in channel.history(limit=5):
            #    if msg.author == self.bot.user and msg.id != self.last_playing_message?.id:
            #        await msg.delete()

            self.last_playing_message = await channel.send(embed=embed)
        except discord.HTTPException as e:
            print(f"Failed to send playing message: {e}")
            self.last_playing_message = None


    async def maintenance(self):
        """Task to automatically disconnect if bot is alone."""
        print("Starting maintenance task.")
        while self.voice_client and self.voice_client.is_connected():
            await asyncio.sleep(60) # Check every 60 seconds

            if not self.voice_client or not self.voice_client.is_connected():
                print("Maintenance: VC disconnected, stopping task.")
                break

            # Check if anyone other than the bot is in the channel
            members = self.voice_client.channel.members
            human_members = [m for m in members if not m.bot]

            if not human_members:
                print(f"Maintenance: Bot alone in VC {self.voice_client.channel.name}. Disconnecting.")
                await self.cog.cleanup_session(self.voice_client.guild.id) # Use Cog's cleanup
                break # Exit loop after cleanup is initiated
            # else:
            #    print(f"Maintenance: {len(human_members)} human(s) in VC.")

        print("Maintenance task finished.")
        self.maintenance_task = None # Clear reference

    def stop_tasks(self):
         """Stops player and maintenance tasks."""
         print(f"Stopping tasks for session in guild {self.voice_client.guild.id}")
         self.player_task.cancel()
         if self.maintenance_task and not self.maintenance_task.done():
              self.maintenance_task.cancel()

# --- Music Cog ---
class MusicCog(commands.Cog, name="Music"):
    """Cog for music playback commands."""
    def __init__(self, bot_instance: commands.Bot):
        self.bot = bot_instance
        self.sessions: Dict[int, Session] = {}
        # Store necessary configs accessible to Session instances
        self.ytdl = ytdl # Use global ytdl instance
        self.ffmpeg_executable = FFMPEG_EXECUTABLE
        self.thumbnail_url = THUMBNAIL_URL

    async def get_or_create_session(self, ctx: commands.Context) -> Optional[Session]:
        """Gets the existing session for the guild or creates a new one."""
        guild_id = ctx.guild.id

        # --- Check if user is in a voice channel ---
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("You need to be in a voice channel first.")
            return None
        user_channel = ctx.author.voice.channel

        # --- Session Exists ---
        if guild_id in self.sessions:
            session = self.sessions[guild_id]
            # Check connection status and channel
            if not session.voice_client or not session.voice_client.is_connected():
                 print(f"Session exists but VC disconnected for guild {guild_id}. Attempting reconnect.")
                 try:
                      session.voice_client = await user_channel.connect(timeout=15.0, reconnect=True)
                      print(f"Reconnected to {user_channel.name}")
                      # Restart player loop if needed? Might restart automatically if queue has items.
                 except asyncio.TimeoutError:
                      await ctx.send(f"Failed to reconnect to {user_channel.name}. Timeout.")
                      await self.cleanup_session(guild_id) # Clean up broken session
                      return None
                 except Exception as e:
                      await ctx.send(f"Error reconnecting: {e}")
                      await self.cleanup_session(guild_id)
                      return None
            elif session.voice_client.channel != user_channel:
                 # Move to user's channel if different
                 try:
                      await session.voice_client.move_to(user_channel)
                 except Exception as e:
                      await ctx.send(f"Error moving to your channel: {e}")
                      # Decide if this is critical - maybe just proceed?

            # Deafen after connect/move
            try:
                await ctx.guild.change_voice_state(channel=user_channel, self_mute=False, self_deaf=True)
            except Exception as e:
                print(f"Could not deafen bot: {e}") # Non-critical usually

            return session

        # --- Create New Session ---
        else:
            try:
                voice_client = await user_channel.connect(timeout=15.0, reconnect=True)
                await ctx.guild.change_voice_state(channel=user_channel, self_mute=False, self_deaf=True)

                # Create session, passing self (the Cog instance)
                session = Session(self, voice_client)
                self.sessions[guild_id] = session
                # Start maintenance task for the new session
                session.maintenance_task = self.bot.loop.create_task(session.maintenance())
                return session

            except asyncio.TimeoutError:
                await ctx.send(f"Could not connect to {user_channel.name}. Timeout.")
                return None
            except discord.ClientException as e:
                 await ctx.send(f"Connection error: {e}")
                 return None
            except Exception as e:
                await ctx.send(f"An unexpected error occurred connecting: {e}")
                print(f"Unexpected connection error: {e}")
                return None

    async def cleanup_session(self, guild_id: int):
        """Cleans up resources for a session."""
        if guild_id in self.sessions:
            print(f"Cleaning up session for guild {guild_id}")
            session = self.sessions[guild_id]
            session.stop_tasks() # Stop player loop and maintenance

            if session.voice_client and session.voice_client.is_connected():
                await session.voice_client.disconnect(force=True)

            del self.sessions[guild_id]
            print(f"Session for guild {guild_id} removed.")


    # --- Commands ---
    @commands.command(name='join', aliases=['connect'], help='Joins your current voice channel')
    @commands.guild_only()
    async def join(self, ctx: commands.Context):
        """Joins the voice channel of the command author."""
        session = await self.get_or_create_session(ctx)
        if session:
             await ctx.message.add_reaction("👍")
        # Error messages handled by get_or_create_session

    @commands.command(name='leave', aliases=['disconnect', 'dc'], help='Leaves the voice channel')
    @commands.guild_only()
    async def leave(self, ctx: commands.Context):
        """Leaves the voice channel and cleans up the session."""
        guild_id = ctx.guild.id
        if guild_id in self.sessions:
            await self.cleanup_session(guild_id)
            await ctx.message.add_reaction("👋")
        elif ctx.voice_client and ctx.voice_client.is_connected():
             # If session object is somehow lost but client is connected
             await ctx.voice_client.disconnect(force=True)
             await ctx.message.add_reaction("👋")
             print(f"Cleaned up stray VC connection for guild {guild_id}")
        else:
            await ctx.send("I'm not currently in a voice channel.")


    @commands.command(name='play', aliases=['p'], help='Plays audio from a URL or search query')
    @commands.guild_only()
    async def play(self, ctx: commands.Context, *, query: str):
        """Plays a song from URL or search query. Joins channel if necessary."""
        session = await self.get_or_create_session(ctx)
        if not session:
            return # Error handled

        await session.add_to_queue(ctx, query)

    @commands.command(name='pause', help='Pauses the current song')
    @commands.guild_only()
    async def pause(self, ctx: commands.Context):
        """Pauses playback."""
        session = self.sessions.get(ctx.guild.id)
        if session and session.voice_client and session.voice_client.is_playing():
            session.voice_client.pause()
            await ctx.message.add_reaction("⏸️")
        else:
            await ctx.send("Nothing is currently playing to pause.")

    @commands.command(name='resume', help='Resumes the paused song')
    @commands.guild_only()
    async def resume(self, ctx: commands.Context):
        """Resumes playback."""
        session = self.sessions.get(ctx.guild.id)
        if session and session.voice_client and session.voice_client.is_paused():
            session.voice_client.resume()
            await ctx.message.add_reaction("▶️")
        else:
            await ctx.send("Nothing is currently paused.")

    @commands.command(name='skip', aliases=['s'], help='Skips the current song')
    @commands.guild_only()
    async def skip(self, ctx: commands.Context):
        """Skips the currently playing song."""
        session = self.sessions.get(ctx.guild.id)
        if session and session.voice_client and (session.voice_client.is_playing() or session.voice_client.is_paused()):
             session.voice_client.stop() # Triggers the 'after' callback (play_next_song)
             await ctx.message.add_reaction("⏭️")
        else:
             await ctx.send("Nothing is playing to skip.")

    @commands.command(name='stop', help='Stops playback and clears the queue')
    @commands.guild_only()
    async def stop(self, ctx: commands.Context):
        """Stops playback, clears the queue, and disconnects."""
        guild_id = ctx.guild.id
        if guild_id in self.sessions:
            # Use the cleanup function which handles stopping tasks, clearing queue, and disconnecting
            await self.cleanup_session(guild_id)
            await ctx.message.add_reaction("⏹️")
        else:
            await ctx.send("Nothing is playing to stop.")


    @commands.command(name='queue', aliases=['q'], help='Shows the current music queue')
    @commands.guild_only()
    async def queue(self, ctx: commands.Context):
        """Displays the current song queue."""
        session = self.sessions.get(ctx.guild.id)
        if not session or not session.current_source:
             await ctx.send("The queue is empty and nothing is playing.")
             return

        # Reuse the print function - it handles both current song and queue
        await session.print_playing_and_queue(session.current_source.title)


    @commands.command(name='loop', help='Sets loop mode (one, all, off)')
    @commands.guild_only()
    async def loop(self, ctx: commands.Context, mode: str = None):
        """Sets the loop mode for the queue."""
        session = self.sessions.get(ctx.guild.id)
        if not session:
             await ctx.send("No active music session.")
             return

        mode_str = str(mode).lower() if mode else None

        if mode_str == "one" or mode_str == "single":
            session.loop_mode = "one"
            await ctx.send("🔁 Looping current song.")
        elif mode_str == "all" or mode_str == "queue":
            session.loop_mode = "all"
            await ctx.send("🔁 Looping entire queue.")
        elif mode_str == "off" or mode_str == "stop" or mode_str == "none":
            session.loop_mode = None
            await ctx.send("❌ Loop disabled.")
        else:
             current_mode = session.loop_mode.capitalize() if session.loop_mode else "Off"
             await ctx.send(f"Current loop mode: **{current_mode}**. Use `one`, `all`, or `off`.")
             return # Don't react if just showing status or invalid

        await ctx.message.add_reaction("✅")
        # Update display if playing
        if session.current_source:
             await session.print_playing_and_queue(session.current_source.title)


    @commands.command(name='volume', aliases=['vol'], help='Sets the player volume (0-100)')
    @commands.guild_only()
    async def volume(self, ctx: commands.Context, volume_percent: Optional[int] = None):
        """Sets the playback volume."""
        session = self.sessions.get(ctx.guild.id)
        if not session or not session.voice_client:
             await ctx.send("Not connected to a voice channel.")
             return

        if volume_percent is None:
             await ctx.send(f"Current volume: **{int(session.volume * 100)}%**")
             return

        if not 0 <= volume_percent <= 100:
             await ctx.send("Volume must be between 0 and 100.")
             return

        session.volume = volume_percent / 100.0
        if session.current_source and session.voice_client.source:
             # discord.PCMVolumeTransformer allows changing volume dynamically
             session.voice_client.source.volume = session.volume

        await ctx.send(f"Volume set to **{volume_percent}%**")
        await ctx.message.add_reaction("🔊")
        # Update display if playing
        if session.current_source:
             await session.print_playing_and_queue(session.current_source.title)


    # --- Listeners for Auto Cleanup ---
    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Cleans up session when bot is removed from a guild."""
        await self.cleanup_session(guild.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Listener for voice state changes, used for session cleanup."""
        if member.id == self.bot.user.id and before.channel and not after.channel:
            # Bot was disconnected (or left)
            guild_id = before.channel.guild.id
            print(f"Bot disconnected via voice state update for guild {guild_id}")
            await self.cleanup_session(guild_id)
        # Note: Maintenance task handles the 'bot alone' scenario


# --- Event Handlers ---
@bot.event
async def on_ready():
    """Called when the bot is ready."""
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print(f'discord.py version: {discord.__version__}')
    print('------')
    print('Loading Cogs...')
    try:
        await bot.add_cog(MusicCog(bot)) # Pass bot instance to Cog
        print(f'MusicCog loaded successfully.')
    except Exception as e:
        print(f'Error loading MusicCog: {e}')
    print('------')
    print('Bot is ready and online.')


# --- Run the Bot ---
if __name__ == '__main__':
    if not DISCORDTOKEN:
         print("CRITICAL ERROR: Discord token not found in environment variables.")
         print("Please set the 'discord_token' in your .env file.")
    else:
         try:
             # bot.run handles the event loop directly
             bot.run(DISCORDTOKEN)
         except discord.LoginFailure:
             print("CRITICAL ERROR: Invalid Discord token.")
         except Exception as e:
             # Catch other potential exceptions during startup or runtime
             print(f"An error occurred: {e}")
