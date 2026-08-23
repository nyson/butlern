from __future__ import annotations

from typing import Final

from butler.domains.rsvp.types import RsvpRole, RsvpStatus

RsvpStatusEmoji = tuple[RsvpStatus, str]

AVAILABLE_EMOJI: Final[str] = "🪓"
AVAILABLE_BUTTON_LABEL: Final[str] = "Jag vill vara med!"

MAYBE_EMOJI: Final[str] = "🤔"
MAYBE_BUTTON_LABEL: Final[str] = "Förmodligen"

ARRIVE_LATER_EMOJI: Final[str] = "🕒"
ARRIVE_LATER_BUTTON_LABEL: Final[str] = "Kommer senare"

CANT_EMOJI: Final[str] = "🛌"
CANT_BUTTON_LABEL: Final[str] = "Kan inte ikväll 😞"

STORYTELLER_EMOJI: Final[str] = "📖"
STORYTELLER_BUTTON_LABEL: Final[str] = "Jag vill storytella!"

ROOM_MANAGEMENT_BUTTON_LABEL: Final[str] = "Rumshantering"
ROOM_MANAGEMENT_BUTTON_EMOJI: Final[str] = "🛠️"

ROOM_CLOSE_BUTTON_LABEL: Final[str] = "ST: Stäng rummet"
ROOM_CLOSE_BUTTON_EMOJI: Final[str] = "🔒"
ROOM_LINK_PROMPT_BUTTON_LABEL: Final[str] = "ST: Öppna rummet"
ROOM_LINK_PROMPT_BUTTON_EMOJI: Final[str] = "🔗"
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
    ("Cant", CANT_EMOJI),
)
RSVP_REACTION_EMOJIS: Final[tuple[str, ...]] = (
    AVAILABLE_EMOJI,
    MAYBE_EMOJI,
    CANT_EMOJI,
    STORYTELLER_EMOJI,
)
EMOJI_TO_STATUS: Final[dict[str, RsvpStatus]] = {
    emoji: status for status, emoji in RSVP_STATUS_EMOJIS
}
RSVP_STATUS_LABELS: Final[dict[RsvpStatus | RsvpRole, str]] = {
    "Available": "Jag vill vara med!",
    "Maybe": "Jag kommer förmodligen, kan inte lova något",
    "Cant": "Kan inte ikväll 😞"
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
    "{event_section}"
    "{room_section}"
    "{status_sections}\n\n"
    "{footer_text}"
)
ROOM_OPENED_MESSAGE_TEMPLATE: Final[str] = "**Rummet är öppet:** {room_url}"
ROOM_CLOSED_MESSAGE: Final[str] = "**Rummet är nu stängt!** Tack för ikväll!"

ARRIVE_LATER_MODAL_TITLE: Final[str] = "Jag kommer senare"
ARRIVE_LATER_MODAL_PLACEHOLDER: Final[str] = "19:00"
ARRIVE_LATER_INVALID_TIME_MESSAGE: Final[str] = (
    "Du måste skriva in tiden i formatet HH:MM, exempelvis 19:30!"
)

# Slash-command / autocomplete copy (Swedish product surface)
EVENT_OPTION_DESCRIPTION: Final[str] = (
    "Välj befintligt event eller låt Butlern skapa ett nytt"
)
CREATE_NEW_EVENT_CHOICE_LABEL: Final[str] = "Låt Butlern skapa ett evenemang!"
CREATE_NEW_EVENT_CHOICE_VALUE: Final[str] = "__butler_create_new_event__"
EVENT_AUTOCOMPLETE_ERROR_TEMPLATE: Final[str] = "ett fel har hänt: {error}"

# Event linking (rsvp2 button-driven selection; not a slash option)
SELECT_EVENT_BUTTON_LABEL: Final[str] = "Välj Discord-event"
SELECT_EVENT_BUTTON_EMOJI: Final[str] = "📅"
SELECT_EVENT_MODAL_TITLE: Final[str] = "Välj Discord-event"
SELECT_EVENT_MODAL_LABEL: Final[str] = "Event idag"
SELECT_EVENT_PLACEHOLDER: Final[str] = "Välj ett event från cachen"
SELECT_EVENT_EMPTY_MESSAGE: Final[str] = (
    "Inga återanvändbara Discord-event finns i cachen just nu. "
    "Skapa/länka via knappen eller `/event`, eller vänta på att cachen uppdateras."
)
SELECT_EVENT_UPDATED_TEMPLATE: Final[str] = "RSVP länkad till **{event_name}**."
SELECT_EVENT_CREATED_TEMPLATE: Final[str] = (
    "Skapade Discord-event **{event_name}** och länkade RSVP."
)
SELECT_EVENT_PERMISSION_DENIED_MESSAGE: Final[str] = (
    "Du behöver storyteller-rollen för att välja Discord-event."
)

# Discord permission label as shown to Swedish guilds
MANAGE_SERVER_PERMISSION_LABEL: Final[str] = "`Hantera server`"

ROOM_MANAGEMENT_ACCESS_WITH_ROLE_TEMPLATE: Final[str] = (
    f"{MANAGE_SERVER_PERMISSION_LABEL} eller <@&{{role_id}}>"
)
ROOM_MANAGEMENT_CONTENT_TEMPLATE: Final[str] = (
    "## Rumshantering\n"
    "Behörighet: {access_summary}\n"
    "{room_line}"
)

ROOM_LINK_MODAL_TITLE: Final[str] = "Lägg till rumslänk"
ROOM_LINK_MODAL_LABEL: Final[str] = "Rumslänk (http/https)"
ROOM_LINK_MODAL_PLACEHOLDER: Final[str] = "https://example.com/rum"
ROOM_LINK_MODAL_MAX_LENGTH: Final[int] = 250
ROOM_LINK_MODAL_INVALID_MESSAGE: Final[str] = (
    "Ogiltig länk. Använd en fullständig URL som börjar med http:// eller https://."
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

