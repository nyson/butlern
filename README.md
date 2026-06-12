# Butler Discord Bot

Discord bot that creates same-day scheduled events and posts an interactive RSVP message.

## Setup

1. Install dependencies:
   - `poetry install`
2. Create `config.toml` from `config.toml.example`.
3. Set:
   - `[discord].token` to your bot token
   - `[discord].guild_id` to your test server ID (required for `butler-dev`)

## Required Discord OAuth Scopes

When inviting the bot, include:

- `bot`
- `applications.commands`

## Required Bot Permissions

Grant these permissions to the bot role in the server:

- `View Channels`
- `Use Application Commands`
- `Create Events`
- `Send Messages`
- `Embed Links`

For `butler-dev`, these are required in the specific guild from `guild_id` because command sync is guild-scoped and strict.

## Required Intents

For the current bot features (slash commands, scheduled events, button interactions), no privileged intents are required.

In Discord Developer Portal → **Bot**:

- Leave privileged intents disabled unless you later add features that need them.
- If you add classic prefix text commands that read message content, then enable **Message Content Intent**.

## Run

- Normal mode:
  - `poetry run butler`
- Dev mode (strict guild sync):
  - `poetry run butler-dev`

## Onboarding and default event channel

When Butler joins a server, it posts a short onboarding message asking you to configure the event channel.

Use:

- `/seteventchannel event_channel:<#channel>`

Notes:

- this sets the default channel where Butler posts RSVP messages
- you can change it later by running `/seteventchannel` again
- the bot validates it can post in that channel (`View Channel`, `Send Messages`, `Embed Links`)

## Slash command usage

Use `/eventtoday` to create the scheduled event and post RSVP in the configured default event channel.

Arguments:

- `title` (required)
- `description` (required)
- `start_time` (optional, `HH:MM`, defaults to `19:00`)
- `room_link` (optional, must be `http://` or `https://` URL)

Behavior:

- if default `19:00` has already passed for today, Butler schedules it for tomorrow at `19:00`
- if you provide a manual `start_time` that has already passed, Butler returns an error
- planned events do not require room details up front
- `location` and `duration` are internal defaults and not user inputs
- the RSVP post always includes an **Open Event** link
- an **Open Room** link is included only when `room_link` is provided

## RSVP interactions

- Buttons:
  - `Available`
  - `Maybe`
  - `Arrive later` (prompts for arrival time in `HH:MM`)
- Emoji reactions are also supported:
  - `✅` → Available
  - `🤔` → Maybe
- there is no unavailable option/button

## Common Errors

### `403 Forbidden (50001): Missing Access`

This usually means one of these:

- `guild_id` is wrong
- Bot is not invited to that server
- Missing OAuth scope `applications.commands`
- Bot role/channel permissions are missing (especially `View Channels` and `Use Application Commands`)

### Token errors (`401 Unauthorized` / `Improper token` / login failure)

- Token in `config.toml` is invalid, expired, or from a different app
- You regenerated the token but didn’t update `config.toml`

Fix:

1. Discord Developer Portal → **Bot** → **Reset Token**
2. Paste new token into `config.toml`
3. Restart bot

If a token was exposed accidentally, reset it immediately.
