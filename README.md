# Morlam Bot

A small, private Discord music bot. Streams audio from YouTube into a voice
channel using slash commands. State is in-memory — it only runs while your
machine is on, and each startup is a fresh listening session.

## Commands

| Command | What it does |
| --- | --- |
| `/play <query>` | Play a YouTube URL, or search and play the top hit. Queues if something's already playing. |
| `/skip` | Skip the current song. |
| `/stop` | Clear the queue and disconnect. |
| `/pause` / `/resume` | Pause and resume playback. |
| `/queue` | Show what's playing and what's next. |
| `/nowplaying` | Show the current song. |

Join a voice channel first, then run `/play` — the bot follows you in. It
auto-disconnects 5 minutes after the queue empties or everyone leaves the
channel. Commands are server-only; they don't show up in DMs.

## Setup

### 1. Install FFmpeg and Opus

FFmpeg must be on your `PATH`. Opus is what encodes the audio Discord actually
hears — without it the bot connects but plays silence.

```bash
brew install ffmpeg opus                  # macOS
# sudo apt install ffmpeg libopus0        # Debian/Ubuntu/Raspberry Pi OS
```

### 2. Install Python dependencies

This project uses [uv](https://docs.astral.sh/uv/). Install it if you haven't:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # or: brew install uv
```

Then create the environment and install everything (Python 3.11+ is fetched
automatically if needed):

```bash
uv sync
```

### 3. Create a bot application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. **New Application** → name it → **Bot** tab → **Reset Token** → copy the token.
3. Invite it to your server: **OAuth2 → URL Generator**, scopes `bot` and
   `applications.commands`, bot permissions **Connect** and **Speak**. Open the
   generated URL and add it to your server.

No privileged intents are required — slash commands don't need message content.

### 4. Configure the token

```bash
cp .env.example .env
# edit .env and paste your token after DISCORD_TOKEN=
```

### 5. Run

```bash
uv run python -m bot
```

Slash commands sync on startup; they may take a minute to appear the first time.
`Ctrl+C` stops the bot.

## Layout

```
bot/
  __main__.py   entry point, client, slash commands
  player.py     per-guild queue, playback loop, idle/alone timers
  ytdl.py       yt-dlp extraction -> Track
```

## Troubleshooting

- **Bot joins but is silent:** Opus didn't load. Startup prints a `WARNING:
  could not load libopus` line — install it (step 1) and restart.
- **`/play` suddenly fails on everything:** YouTube likely changed something.
  Fix: `uv lock --upgrade-package yt-dlp && uv sync`.
- **Commands don't appear:** re-invite with the `applications.commands` scope,
  then restart the bot and give Discord a minute.
- **Bot challenges on a server/VPS:** datacenter IPs sometimes get bot-checked
  by YouTube. Running on a home machine (as intended here) avoids this.

## Notes

Built for private use. Queue and connection state live in memory only, so a
restart clears everything.
