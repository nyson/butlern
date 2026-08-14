
from typing import Literal, Union

from butler.base import Store
from butler.config import DiscordConfig, load_config
from butler.constants import PERSISTANCE_PATH
from butler.rsvp.rsvp_store import RsvpMessageStore
from butler.settings_store import GuildSettingsStore

from discord.ext import commands

StoreKey = Union[
    Literal["settings"],
    Literal["rsvp"]]

class BotWrapper:
    config: DiscordConfig
    stores: dict[StoreKey, Store]
    bot: commands.Bot

    def __init__(self) -> None:
        self.stores = {}

    def setup(self) -> None:
        self.config = load_config()
        self.stores["settings"] = GuildSettingsStore.load(PERSISTANCE_PATH)
        self.stores["rsvp"] = RsvpMessageStore.load(PERSISTANCE_PATH)
        