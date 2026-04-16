"""URL detection for supported music sources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse


class Source(str, Enum):
    YOUTUBE = "youtube"
    SPOTIFY = "spotify"


class ItemType(str, Enum):
    TRACK = "track"
    PLAYLIST = "playlist"
    ALBUM = "album"


@dataclass(frozen=True)
class DetectedUrl:
    source: Source
    item_type: ItemType
    normalized_url: str


class InvalidUrlError(ValueError):
    """Raised when the URL is not recognized as a supported source."""


def _clean_host(host: str) -> str:
    return host.lower().replace("www.", "")


def detect_url(url: str) -> DetectedUrl:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InvalidUrlError(
            "Invalid URL. Use a full URL like https://music.youtube.com/... or "
            "https://open.spotify.com/track/..."
        )

    host = _clean_host(parsed.netloc)
    path = parsed.path.rstrip("/")
    path_parts = [part for part in path.split("/") if part]

    if host == "music.youtube.com":
        item_type = ItemType.PLAYLIST if "playlist" in path.lower() else ItemType.TRACK
        return DetectedUrl(Source.YOUTUBE, item_type, url)

    if host in {"youtube.com", "m.youtube.com"} and path == "/playlist":
        return DetectedUrl(Source.YOUTUBE, ItemType.PLAYLIST, url)

    if host == "open.spotify.com" and len(path_parts) >= 2:
        kind = path_parts[0].lower()
        if kind == "track":
            return DetectedUrl(Source.SPOTIFY, ItemType.TRACK, url)
        if kind == "album":
            return DetectedUrl(Source.SPOTIFY, ItemType.ALBUM, url)
        if kind == "playlist":
            return DetectedUrl(Source.SPOTIFY, ItemType.PLAYLIST, url)

    raise InvalidUrlError(
        "Unsupported URL. Supported patterns: music.youtube.com, "
        "youtube.com/playlist, open.spotify.com/track|album|playlist"
    )
