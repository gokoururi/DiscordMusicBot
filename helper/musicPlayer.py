from discord import voice_client, Interaction
from tinydb import TinyDB

class Player():
    voiceClient: voice_client.VoiceClient = None
    db: TinyDB = None

    def __init__(self, db: TinyDB):
        self.db = db
        print("init MusicPlayer")
    
    async def play(self, interaction: Interaction, url: str):
        if not await self.connectToVoice(interaction):
            return

        await interaction.response.send_message(f"Understood. I will begin downloading '{url}'.", ephemeral=True)
    
    async def connectToVoice(self, interaction) -> bool:
        if not interaction.user.voice:
            await interaction.response.send_message(f"You are not in a voice channel.", ephemeral=True)
            return False

        if not self.voiceClient or not self.voiceClient.is_connected():
            self.voiceClient = await interaction.user.voice.channel.connect()
            print("Connected to voice client")
        
        return True



players: dict[int, Player] = {}

def getPlayer(guild: int, db: TinyDB) -> Player:
    if not guild in players:
        players[guild] = Player(db)
    return players[guild]