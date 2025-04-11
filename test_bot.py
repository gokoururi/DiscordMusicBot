import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import yt_dlp
import os
from dotenv import load_dotenv

# Assuming YTDLSource and MusicCog are in main.py
from main import YTDLSource, MusicCog, Session, bot

@pytest.fixture
def mock_guild():
    guild = MagicMock()
    guild.id = 1234567890
    return guild

@pytest.fixture
def mock_author():
    author = MagicMock()
    author.voice = MagicMock()
    author.voice.channel = MagicMock()
    return author

@pytest.fixture
def mock_voice_client():
    voice_client = MagicMock()
    voice_client.is_connected = MagicMock(return_value=True)
    voice_client.move_to = AsyncMock()
    voice_client.disconnect = AsyncMock()
    return voice_client

@pytest.fixture
def mock_ctx(mock_guild, mock_author, mock_voice_client):
    ctx = MagicMock()
    ctx.guild = mock_guild
    ctx.author = mock_author
    ctx.voice_client = mock_voice_client
    ctx.send = AsyncMock()
    ctx.message = AsyncMock()  # Add a mock message object
    ctx.message.add_reaction = AsyncMock()
    return ctx

@pytest.mark.asyncio
async def test_ytdlsource_from_url(mocker):
    # Mock yt_dlp.YoutubeDL.extract_info
    mock_extract_info = mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"title": "Test Song"})

    # Call the method
    loop = asyncio.get_event_loop()
    data = await YTDLSource.from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ", loop=loop, ytdl_instance=yt_dlp.YoutubeDL())

    # Assertions
    assert data is not None
    assert data["title"] == "Test Song"
    mock_extract_info.assert_called_once_with("https://www.youtube.com/watch?v=dQw4w9WgXcQ", download=False)


@pytest.mark.asyncio
async def test_musiccog_join(mock_ctx, mocker):
    # Mock necessary objects and methods
    mock_voice_client = MagicMock()
    mock_voice_client.is_connected.return_value = False
    mock_ctx.author.voice.channel.connect = AsyncMock(return_value=mock_voice_client)
    mock_ctx.guild.change_voice_state = AsyncMock()

    # Create a MusicCog instance
    music_cog = MusicCog(bot_instance=MagicMock())

    # Invoke the join command
    await music_cog.join(mock_ctx)

    # Assertions
    mock_ctx.author.voice.channel.connect.assert_called_once()
    mock_ctx.guild.change_voice_state.assert_called_once_with(channel=mock_ctx.author.voice.channel, self_mute=False, self_deaf=True)
    mock_ctx.message.add_reaction.assert_called_once_with("👍")


@pytest.mark.asyncio
async def test_musiccog_leave(mock_ctx):
    # Create a MusicCog instance and a mock session
    music_cog = MusicCog(bot_instance=MagicMock())
    mock_session = MagicMock()
    mock_session.voice_client = mock_ctx.voice_client  # Use the mock_voice_client
    music_cog.sessions[mock_ctx.guild.id] = mock_session

    # Invoke the leave command
    await music_cog.leave(mock_ctx)

    # Assertions
    mock_session.stop_tasks.assert_called_once()
    mock_ctx.voice_client.disconnect.assert_called_once_with(force=True)
    mock_ctx.message.add_reaction.assert_called_once_with("👋")


def test_bot_token_loaded():
    load_dotenv()
    token = os.getenv("discord_token")
    assert token is not None, "Discord token not loaded from environment variables"