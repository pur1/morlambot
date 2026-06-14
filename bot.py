"""Discord music bot — entry point and slash commands.

Private-server bot: streams audio from YouTube (via yt-dlp) into a voice
channel. One GuildPlayer per guild holds the queue and connection; see
player.py. State is fully in-memory and does not survive a restart.
"""

from __future__ import annotations

import asyncio
import ctypes.util
import os

import discord
from discord import app_commands
from dotenv import load_dotenv

from player import IDLE_TIMEOUT, GuildPlayer
from ytdl import ExtractionError, resolve

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")


def _load_opus() -> None:
    """Ensure libopus is loaded so voice audio can be encoded.

    discord.py needs Opus to send voice, but doesn't auto-load it on every
    platform (notably Apple Silicon macOS). We try the linker first, then the
    common Homebrew/Linux install paths. Without this the bot connects to voice
    but plays silence.
    """
    if discord.opus.is_loaded():
        return
    candidates = [
        ctypes.util.find_library("opus"),
        "/opt/homebrew/lib/libopus.dylib",  # macOS Apple Silicon
        "/usr/local/lib/libopus.dylib",      # macOS Intel
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",  # Debian/Ubuntu
        "libopus.so.0",
    ]
    for path in candidates:
        if not path:
            continue
        try:
            discord.opus.load_opus(path)
            if discord.opus.is_loaded():
                return
        except OSError:
            continue
    print(
        "WARNING: could not load libopus — voice will be silent. "
        "Install it (e.g. `brew install opus` or `apt install libopus0`)."
    )

intents = discord.Intents.default()
# Needed to read voice channel membership for join + alone-detection.
intents.voice_states = True


class MusicBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.players: dict[int, GuildPlayer] = {}
        # Pending "everyone left" disconnect timers, keyed by guild id.
        self._alone_timers: dict[int, asyncio.Task] = {}

    async def setup_hook(self) -> None:
        _load_opus()
        await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} ({self.user.id})")

    def get_player(self, guild_id: int) -> GuildPlayer | None:
        return self.players.get(guild_id)

    def drop_player(self, guild_id: int) -> None:
        self.players.pop(guild_id, None)

    async def on_voice_state_update(self, member, before, after) -> None:
        # Only react to humans moving in/out of the bot's current channel.
        if member.bot:
            return
        for guild_id, player in list(self.players.items()):
            channel = player.voice.channel
            if channel is None:
                continue
            if player.is_alone():
                self._start_alone_timer(guild_id, player)
            else:
                self._cancel_alone_timer(guild_id)

    def _start_alone_timer(self, guild_id: int, player: GuildPlayer) -> None:
        if guild_id in self._alone_timers:
            return  # already counting down

        async def _wait_then_leave() -> None:
            try:
                await asyncio.sleep(IDLE_TIMEOUT)
                if player.is_alone():
                    await player.stop()
                    self.drop_player(guild_id)
            except asyncio.CancelledError:
                pass
            finally:
                self._alone_timers.pop(guild_id, None)

        self._alone_timers[guild_id] = asyncio.create_task(_wait_then_leave())

    def _cancel_alone_timer(self, guild_id: int) -> None:
        task = self._alone_timers.pop(guild_id, None)
        if task:
            task.cancel()


bot = MusicBot()


# -- helpers ----------------------------------------------------------------


async def _ensure_voice(interaction: discord.Interaction) -> GuildPlayer | None:
    """Return the guild's player, connecting to the user's channel if needed.

    Sends an error response and returns None if the user isn't in a voice
    channel, or if the bot is already busy in a different one.
    """
    user = interaction.user
    if not user.voice or not user.voice.channel:
        await interaction.response.send_message(
            "You need to be in a voice channel first.", ephemeral=True
        )
        return None

    target = user.voice.channel
    player = bot.get_player(interaction.guild_id)

    if player and player.voice.is_connected():
        if player.voice.channel.id != target.id:
            await interaction.response.send_message(
                "I'm already playing in another voice channel.", ephemeral=True
            )
            return None
        return player

    voice = await target.connect()
    player = GuildPlayer(interaction.guild, voice)
    bot.players[interaction.guild_id] = player
    return player


# -- commands ---------------------------------------------------------------


@bot.tree.command(description="Play a song from a URL or search query.")
@app_commands.describe(query="A YouTube URL or something to search for.")
async def play(interaction: discord.Interaction, query: str):
    player = await _ensure_voice(interaction)
    if player is None:
        return

    await interaction.response.defer()
    try:
        track = await resolve(query, requested_by=interaction.user.display_name)
    except ExtractionError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return

    position = player.add(track)
    if position == 1 and player.current is None:
        await interaction.followup.send(f"▶️ Now playing **{track.title}**")
    else:
        await interaction.followup.send(
            f"➕ Queued **{track.title}** (position {position})"
        )


@bot.tree.command(description="Skip the current song.")
async def skip(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild_id)
    if player and player.skip():
        await interaction.response.send_message("⏭️ Skipped.")
    else:
        await interaction.response.send_message(
            "Nothing is playing.", ephemeral=True
        )


@bot.tree.command(description="Stop, clear the queue, and disconnect.")
async def stop(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild_id)
    if player:
        await player.stop()
        bot.drop_player(interaction.guild_id)
        await interaction.response.send_message("⏹️ Stopped and disconnected.")
    else:
        await interaction.response.send_message(
            "I'm not playing anything.", ephemeral=True
        )


@bot.tree.command(description="Pause playback.")
async def pause(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild_id)
    if player and player.pause():
        await interaction.response.send_message("⏸️ Paused.")
    else:
        await interaction.response.send_message(
            "Nothing is playing.", ephemeral=True
        )


@bot.tree.command(description="Resume playback.")
async def resume(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild_id)
    if player and player.resume():
        await interaction.response.send_message("▶️ Resumed.")
    else:
        await interaction.response.send_message(
            "Nothing is paused.", ephemeral=True
        )


@bot.tree.command(description="Show the current queue.")
async def queue(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild_id)
    if not player or (player.current is None and not player.queue):
        await interaction.response.send_message(
            "The queue is empty.", ephemeral=True
        )
        return

    lines = []
    if player.current:
        lines.append(f"**Now playing:** {player.current.title}")
    if player.queue:
        lines.append("\n**Up next:**")
        for i, track in enumerate(list(player.queue)[:10], start=1):
            lines.append(f"{i}. {track.title} `[{track.duration_str}]`")
        extra = len(player.queue) - 10
        if extra > 0:
            lines.append(f"...and {extra} more.")
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(description="Show the song playing right now.")
async def nowplaying(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild_id)
    if not player or player.current is None:
        await interaction.response.send_message(
            "Nothing is playing.", ephemeral=True
        )
        return
    t = player.current
    await interaction.response.send_message(
        f"🎵 **{t.title}** `[{t.duration_str}]`\n"
        f"Requested by {t.requested_by}\n{t.webpage_url}"
    )


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and add your "
            "bot token."
        )
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
