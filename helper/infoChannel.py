import time
from tinydb import TinyDB, Query
from discord import ui, enums, Interaction, SelectOption

class InfoChannel():
    def __init__(self, db: TinyDB):
        self.db = db
        print("muh")

    async def set(self, interaction: Interaction):
        dropdown = DropDown("Select a channel", options=self.listChannels(interaction), infoChannel=self, originalInteraction=interaction)
        view = ui.View()
        view.add_item(dropdown)
        await interaction.response.send_message(
            "Select a Channel to send information to:",
            view=view,
            ephemeral=True
        )

    def listChannels(self, interaction: Interaction):
        channels = []
        for channel in interaction.guild.channels:
            if channel.type == enums.ChannelType.text:
                channels.append(channel.name)
        return channels

    async def handleResponse(self, interaction: Interaction, channel):
            if not channel in self.listChannels(interaction):
                await interaction.edit_original_response(content=f"There is no channel '{channel}' on this server.", view=None)
                return
            self.write(interaction, channel)
            await interaction.edit_original_response(content=f"Understood. I will post information to channel '{channel}'.", view=None)
    
    def write(self, interaction: Interaction, channel: str):
        guildId = interaction.guild.id
        guildName = interaction.guild.name
        result = self.db.search(Query().id == guildId)
        if result:
            print(f"Guild {guildName} ({guildId}) already in db")
            self.db.update({'infoChannel': channel}, Query().id == guildId)
        else:
            self.db.insert({'id': guildId, 'name': guildName, 'infoChannel': channel})

class DropDown(ui.Select):
    def __init__(self, placeholder, options, infoChannel: InfoChannel,originalInteraction: Interaction):
        self.infoChannel = infoChannel
        self.originalInteraction = originalInteraction
        selectOptionList = []
        for option in options:
            selectOptionList.append(SelectOption(label=option))
        super().__init__(placeholder=placeholder, options=selectOptionList, min_values=1, max_values=1)
    
    async def callback(self, _):
        await self.infoChannel.handleResponse(self.originalInteraction, self.values[0])