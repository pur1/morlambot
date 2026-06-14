"""Per-guild music player.

One GuildPlayer per guild holds that guild's queue and voice connection. A
single background task pulls tracks off the queue and plays them one at a time.
When the queue empties or the channel goes silent, an idle timer disconnects
after IDLE_TIMEOUT seconds.
"""

from __future__ import annotations

import asyncio
from collections import deque

import discord

from ytdl import FFMPEG_BEFORE_OPTIONS, FFMPEG_OPTIONS, Track

IDLE_TIMEOUT = 300  # seconds (5 min) before auto-disconnect when idle/alone


class GuildPlayer:
    def __init__(self, guild: discord.Guild, voice: discord.VoiceClient):
        self.guild = guild
        self.voice = voice
        self.queue: deque[Track] = deque()
        self.current: Track | None = None

        # Set when a track finishes (or fails) so the loop advances.
        self._track_done = asyncio.Event()
        # Pinged whenever a new track is queued, to wake the loop from idle wait.
        self._queued = asyncio.Event()
        self._loop_task = asyncio.create_task(self._run())

    # -- public API ---------------------------------------------------------

    def add(self, track: Track) -> int:
        """Enqueue a track. Returns its 1-based position in the queue."""
        self.queue.append(track)
        self._queued.set()
        return len(self.queue)

    def skip(self) -> bool:
        """Stop the current track so the loop advances. False if nothing playing."""
        if self.voice.is_playing() or self.voice.is_paused():
            self.voice.stop()  # fires the after-callback -> _track_done
            return True
        return False

    def pause(self) -> bool:
        if self.voice.is_playing():
            self.voice.pause()
            return True
        return False

    def resume(self) -> bool:
        if self.voice.is_paused():
            self.voice.resume()
            return True
        return False

    async def stop(self) -> None:
        """Clear the queue and disconnect. Tears down the loop task."""
        self.queue.clear()
        self._loop_task.cancel()
        if self.voice.is_connected():
            self.voice.stop()
            await self.voice.disconnect()

    # -- playback loop ------------------------------------------------------

    async def _run(self) -> None:
        try:
            while True:
                if not self.queue:
                    # Idle: wait for a new track, but give up after the timeout.
                    self._queued.clear()
                    try:
                        await asyncio.wait_for(
                            self._queued.wait(), timeout=IDLE_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        await self._disconnect_idle()
                        return

                track = self.queue.popleft()
                self.current = track

                self._track_done.clear()
                source = discord.FFmpegPCMAudio(
                    track.stream_url,
                    before_options=FFMPEG_BEFORE_OPTIONS,
                    options=FFMPEG_OPTIONS,
                )
                self.voice.play(source, after=self._on_track_end)

                await self._track_done.wait()
                self.current = None
        except asyncio.CancelledError:
            pass

    def _on_track_end(self, error: Exception | None) -> None:
        # Runs in discord.py's voice thread; just signal the async loop.
        self._track_done.set()

    async def _disconnect_idle(self) -> None:
        self.current = None
        if self.voice.is_connected():
            await self.voice.disconnect()

    # -- alone detection ----------------------------------------------------

    def is_alone(self) -> bool:
        """True if no non-bot members share the bot's voice channel."""
        channel = self.voice.channel
        if channel is None:
            return True
        return not any(not m.bot for m in channel.members)
