import logging

import butler.design as design

import discord
from discord import ui
from discord.ui import Button, TextDisplay

from butler.rsvp.types import ViewState
from butler.rsvp2.controller import RsvpController

logger = logging.getLogger(__name__)

class EventMessageRsvpActions(ui.ActionRow): # type: ignore
    def __init__(
        self, 
        *children: ui.Item,  # type: ignore
        id: int | None = None,
        controller: RsvpController) -> None:
        super().__init__(*children, id=id) # type: ignore
        self.controller = controller

    @ui.button(
        label=design.AVAILABLE_BUTTON_LABEL,
        emoji=design.AVAILABLE_EMOJI)
    async def attending(
        self, 
        interaction: discord.Interaction,
        button: Button[EventMessageView]) -> None:
        logger.info(f"pressed {button.id}")
        await interaction.response.send_message(f"attending", ephemeral=True)

    @ui.button(
        label=design.MAYBE_BUTTON_LABEL,
        emoji=design.MAYBE_EMOJI)
    async def maybe(
        self, 
        interaction: discord.Interaction,
        button: Button[EventMessageView]) -> None:
        await interaction.response.send_message(f"maybe", ephemeral=True)

    @ui.button(
        label=design.CANT_BUTTON_LABEL,
        emoji=design.CANT_EMOJI)
    async def regret(
        self, 
        interaction: discord.Interaction,
        button: Button[EventMessageView]) -> None:
        await interaction.response.send_message(f"regret", ephemeral=True)        

class EventMessageMetaActions(ui.ActionRow): # type: ignore
    def __init__(
        self, 
        *children: ui.Item,  # type: ignore
        id: int | None = None,
        controller: RsvpController) -> None:
        super().__init__(*children, id=id) # type: ignore
        self.controller = controller


    @ui.button(
        label=design.ARRIVE_LATER_BUTTON_LABEL,
        emoji=design.ARRIVE_LATER_EMOJI)
    async def arriving_later(
        self, 
        interaction: discord.Interaction,
        button: Button[EventMessageView]) -> None:
        await interaction.response.send_message(f"later", ephemeral=True)

    @ui.button(
        label=design.STORYTELLER_BUTTON_LABEL,
        emoji=design.STORYTELLER_EMOJI)
    async def storyteller(
        self, 
        interaction: discord.Interaction,
        button: Button[EventMessageView]) -> None:
        await interaction.response.send_message(f"storyteller", ephemeral=True)

class EventMessageStorytellerActions(ui.ActionRow): # type: ignore
    def __init__(
        self, 
        *children: ui.Item,  # type: ignore
        id: int | None = None,
        controller: RsvpController) -> None:
        super().__init__(*children, id=id) # type: ignore
        self.controller = controller


    @ui.button(
        label=design.ROOM_LINK_PROMPT_BUTTON_LABEL,
        emoji=design.ROOM_LINK_PROMPT_BUTTON_EMOJI)
    async def open_room(
        self, 
        interaction: discord.Interaction,
        button: Button[EventMessageView]) -> None:
        await interaction.response.send_message(f"open room", ephemeral=True)

    @ui.button(
        label=design.ROOM_CLOSE_BUTTON_LABEL,
        emoji=design.ROOM_CLOSE_BUTTON_EMOJI)
    async def close_room(
        self, 
        interaction: discord.Interaction,
        button: Button[EventMessageView]) -> None:
        await interaction.response.send_message(f"open room", ephemeral=True)

class EventMessageView(ui.LayoutView):

    def __init__(
        self, 
        viewState: ViewState,
        rsvpController: RsvpController) -> None:
        super().__init__(timeout=None)
        self.state = viewState
        self.controller = rsvpController
        self.render()

    def __description_block(self) -> TextDisplay[EventMessageView]:
        return TextDisplay("loaded text")
    
    def render(self) -> None:
        self.add_item(self.__description_block())
        self.add_item(EventMessageRsvpActions(controller=self.controller))
        self.add_item(EventMessageMetaActions(controller=self.controller))
        self.add_item(EventMessageStorytellerActions(controller=self.controller))

