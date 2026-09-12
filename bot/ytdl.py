"""yt-dlp extraction wrapper.

Resolves a URL or search query into a playable Track. We extract the direct
audio stream URL (no download) and hand FFmpeg the reconnect flags it needs to
survive transient hiccups mid-stream.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, cast

import yt_dlp
from yt_dlp.utils import DownloadError

# Suppress yt-dlp's noisy stdout; we surface errors ourselves.
_YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",  # bind to IPv4; avoids some YouTube IPv6 blocks
    "extract_flat": False,
}

# FFmpeg reconnect flags keep playback alive if the stream URL stutters.
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"


class ExtractionError(Exception):
    """Raised when a query can't be resolved to a playable track."""


@dataclass
class Track:
    title: str
    stream_url: str
    webpage_url: str
    duration: int | None  # seconds, None if unknown (e.g. live stream)
    requested_by: str

    @property
    def duration_str(self) -> str:
        if self.duration is None:
            return "live"
        m, s = divmod(self.duration, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


def _extract(query: str) -> dict[str, Any]:
    """Blocking yt-dlp call. Run via asyncio.to_thread.

    A fresh YoutubeDL per call: the class is not documented as thread-safe and
    /play can run concurrently across guilds.
    """
    # cast: yt-dlp types its options as a private TypedDict, but a plain dict
    # is the documented way to pass them.
    with yt_dlp.YoutubeDL(cast(Any, _YTDL_OPTS)) as ytdl:
        info = cast("dict[str, Any] | None", ytdl.extract_info(query, download=False))
        if info is None:
            raise ExtractionError("No results found.")
        # A search query returns a playlist-shaped dict; take the first entry.
        if "entries" in info:
            entries = [e for e in info["entries"] if e]
            if not entries:
                raise ExtractionError("No results found.")
            info = entries[0]
        return cast("dict[str, Any]", ytdl.sanitize_info(cast(Any, info)))


def _stream_url(info: dict[str, Any]) -> str | None:
    """Direct audio URL for the chosen format.

    Usually top-level `url`, but when format selection picks a merged
    video+audio pair that key is absent and the parts live under
    `requested_downloads` / `requested_formats`.
    """
    url = info.get("url")
    if url:
        return url
    for key in ("requested_downloads", "requested_formats"):
        for fmt in info.get(key) or []:
            if fmt.get("acodec", "none") != "none" and fmt.get("url"):
                return fmt["url"]
    return None


async def resolve(query: str, requested_by: str) -> Track:
    """Resolve a URL or search string to a Track. Raises ExtractionError."""
    try:
        info = await asyncio.to_thread(_extract, query)
    except ExtractionError:
        raise
    except DownloadError as e:
        raise ExtractionError(_clean_ytdl_error(str(e))) from e
    except Exception as e:  # noqa: BLE001 - yt-dlp raises a wide range
        raise ExtractionError(f"Could not load that track: {e}") from e

    stream_url = _stream_url(info)
    if not stream_url:
        raise ExtractionError("That video has no playable audio stream.")

    return Track(
        title=info.get("title", "Unknown title"),
        stream_url=stream_url,
        webpage_url=info.get("webpage_url", query),
        duration=info.get("duration"),
        requested_by=requested_by,
    )


def _clean_ytdl_error(msg: str) -> str:
    """Turn a verbose yt-dlp error into something a user can read."""
    msg = msg.replace("ERROR: ", "").strip()
    lower = msg.lower()
    if "age" in lower and "restrict" in lower:
        return "That video is age-restricted and can't be played."
    if "private" in lower:
        return "That video is private."
    if "not available" in lower or "unavailable" in lower:
        return "That video is unavailable (region-blocked or removed)."
    # Trim multi-line dumps to the first line.
    return msg.splitlines()[0] if msg else "Could not load that track."
