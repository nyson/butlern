from __future__ import annotations

from typing import Final

from butler.rsvp.rsvp_domain import RsvpStatus

RsvpStatusEmoji = tuple[RsvpStatus, str]

AVAILABLE_EMOJI: Final[str] = "✅"
MAYBE_EMOJI: Final[str] = "🤔"
LATER_EMOJI: Final[str] = "🕒"
STORYTELLER_EMOJI: Final[str] = "😈"

AVAILABLE_BUTTON_LABEL: Final[str] = "Jag vill vara med!"
MAYBE_BUTTON_LABEL: Final[str] = "Kanske"
ARRIVE_LATER_BUTTON_LABEL: Final[str] = "Kommer senare"
STORYTELLER_BUTTON_LABEL: Final[str] = "Jag vill storytella!"
ROOM_MANAGEMENT_BUTTON_LABEL: Final[str] = "Rumshantering"
ROOM_MANAGEMENT_BUTTON_EMOJI: Final[str] = "🛠️"
ROOM_LINK_PROMPT_BUTTON_LABEL: Final[str] = "ST: Öppna rummet"
ROOM_LINK_PROMPT_BUTTON_EMOJI: Final[str] = "🔗"
ROOM_CLOSE_BUTTON_LABEL: Final[str] = "ST: Stäng rummet"
ROOM_CLOSE_BUTTON_EMOJI: Final[str] = "🔒"
ROOM_LINK_PERMISSION_DENIED_MESSAGE: Final[str] = (
    "Du behöver storyteller-rollen för att öppna eller stänga rum."
)
ROOM_LINK_PERMISSION_DENIED_ROLE_TEMPLATE: Final[str] = (
    "Du behöver rollen {mention} för att öppna eller stänga rum."
)
EVENT_MANAGEMENT_PERMISSION_DENIED_MESSAGE: Final[str] = (
    "Du behöver behörigheten `Hantera server` eller den konfigurerade "
    "storyteller-rollen för att skapa event och öppna eller stänga rum."
)
EVENT_MANAGEMENT_PERMISSION_DENIED_ROLE_TEMPLATE: Final[str] = (
    "Du behöver behörigheten `Hantera server` eller rollen "
    "{mention} för att skapa event och öppna eller stänga rum."
)


RSVP_STATUS_EMOJIS: Final[tuple[RsvpStatusEmoji, ...]] = (
    ("Available", AVAILABLE_EMOJI),
    ("Maybe", MAYBE_EMOJI),
    ("Later", LATER_EMOJI),
    ("Storyteller", STORYTELLER_EMOJI),
)
RSVP_REACTION_EMOJIS: Final[tuple[str, ...]] = (
    AVAILABLE_EMOJI,
    MAYBE_EMOJI,
    LATER_EMOJI,
    STORYTELLER_EMOJI,
)
EMOJI_TO_STATUS: Final[dict[str, RsvpStatus]] = {
    emoji: status for status, emoji in RSVP_STATUS_EMOJIS
}
RSVP_STATUS_LABELS: Final[dict[RsvpStatus, str]] = {
    "Available": "Jag vill vara med!",
    "Maybe": "Kanske",
    "Later": "Kommer senare",
    "Storyteller": "Jag vill storytella!",
}

RSVP_FOOTER_TEXT: Final[str] = (
    "Tryck på en knapp du tycker passar dig eller släng en emoji "
    "på det här meddelandet för att registrera dig!"
)
EDITION_RESOURCE_ID_BY_NAME: Final[dict[str, str]] = {
    "bmr": "bmr",
    "tb": "tb",
    "snv": "snv",
    "custom": "custom",
    "Custom": "custom",
    "Bad Moon Rising": "bmr",
    "Carousel": "custom",
    "Fabled": "custom",
    "Loric": "taf",
    "Sects and Violets": "snv",
    "Trouble Brewing": "tb",
}
EVENT_POST_TEMPLATE: Final[str] = (
    "# {title_line}\n"
    "{event_description}\n\n"
    "{room_section}"
    "{status_sections}\n\n"
    "{footer_text}"
)
ROOM_OPENED_MESSAGE_TEMPLATE: Final[str] = "**Rummet är öppet:** {room_url}"
ROOM_CLOSED_MESSAGE: Final[str] = "**Rummet är nu stängt!** Tack för ikväll!"

ROOM_LINK_MODAL_TITLE: Final[str] = "Lägg till rumslänk"
ROOM_LINK_MODAL_LABEL: Final[str] = "Rumslänk (http/https)"
ROOM_LINK_MODAL_PLACEHOLDER: Final[str] = "https://example.com/rum"
ROOM_LINK_MODAL_MAX_LENGTH: Final[int] = 30
ROOM_LINK_MODAL_INVALID_MESSAGE: Final[str] = (
    "Ogiltig länk. Använd en fullständig URL som börjar med http:// eller https://."
)
DESIGN_PREVIEW_DEFAULT_TITLE: Final[str] = "Detta är ett exempel på ett event"
DESIGN_PREVIEW_DEFAULT_DESCRIPTION: Final[str] = (
    "Det här är en förhandsvisning av Butlers RSVP-layout."
)

ONBOARDING_MESSAGE: Final[str] = (
    "Tack för att du lade till Butler! Ställ in standardkanalen för event med "
    "`/seteventchannel` så att inläggen hamnar rätt. Du kan ändra det senare."
)
ROOM_OPENED_WITH_MENTIONS_TEMPLATE: Final[str] = (
    "Rummet är öppet! {mentions}\n"
    "{message_link}"
)
ROOM_OPENED_NO_MENTIONS_TEMPLATE: Final[str] = (
    "Rummet är öppet!\n"
    "{message_link}"
)
