"""Search helpers for YouTube and Spotify sources."""

from __future__ import annotations

import importlib
import os
from typing import Any

import requests


class SearchError(RuntimeError):
    """Base error for search operations."""


class SearchConfigError(SearchError):
    """Raised when required search configuration is missing."""


class SearchNetworkError(SearchError):
    """Raised when remote search endpoints are unavailable."""


def search_tracks(
    query: str, source: str = "youtube", limit: int = 5
) -> list[dict[str, str]]:
    """Search tracks from a supported source.

    Returns a list of dictionaries with this stable shape:
    { title, artist, duration_str, url, source }
    """
    normalized_source = source.lower().strip()
    if normalized_source == "youtube":
        return search_youtube(query, limit)
    if normalized_source == "spotify":
        return search_spotify(query, limit)
    raise ValueError("Invalid source. Valid options: youtube, spotify")


def search_youtube(query: str, limit: int = 5) -> list[dict[str, str]]:
    """Search YouTube with yt-dlp ytsearch prefix."""
    yt_dlp = importlib.import_module("yt_dlp")
    search_term = f"ytsearch{max(1, limit)}:{query}"

    info: dict[str, Any] | None = None
    last_error: Exception | None = None
    for _ in range(2):
        try:
            with yt_dlp.YoutubeDL(
                {
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": True,
                }
            ) as ydl:
                info = ydl.extract_info(search_term, download=False)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    if info is None:
        assert last_error is not None
        raise SearchNetworkError(
            f"Network failure while searching YouTube: {last_error}"
        ) from last_error

    entries = info.get("entries") or []
    results: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        url = None
        if isinstance(video_id, str) and video_id:
            url = f"https://music.youtube.com/watch?v={video_id}"
        else:
            webpage_url = entry.get("webpage_url")
            if isinstance(webpage_url, str) and webpage_url:
                url = webpage_url.replace(
                    "https://www.youtube.com/", "https://music.youtube.com/"
                )
        if not isinstance(url, str) or not url:
            continue

        title = _to_text(entry.get("title")) or "Unknown title"
        artist = (
            _to_text(entry.get("artist"))
            or _to_text(entry.get("uploader"))
            or _to_text(entry.get("channel"))
            or "Unknown artist"
        )
        duration_str = _format_duration(entry.get("duration"))
        results.append(
            {
                "title": title,
                "artist": artist,
                "duration_str": duration_str,
                "url": url,
                "source": "youtube",
            }
        )
    return results


def search_spotify(query: str, limit: int = 5) -> list[dict[str, str]]:
    """Search Spotify tracks through Web API."""
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SearchConfigError(
            "Spotify search requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.\n"
            "Set them before running search, for example:\n"
            "  Windows PowerShell:\n"
            "    $env:SPOTIFY_CLIENT_ID='your_client_id'\n"
            "    $env:SPOTIFY_CLIENT_SECRET='your_client_secret'"
        )

    token = _spotify_token(client_id, client_secret)
    payload = _spotify_search_tracks(query=query, limit=max(1, limit), token=token)

    tracks = payload.get("tracks", {})
    items = tracks.get("items") or []
    results: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _to_text(item.get("name")) or "Unknown title"
        artists = item.get("artists") or []
        artist_names = []
        if isinstance(artists, list):
            for artist in artists:
                if isinstance(artist, dict):
                    artist_name = _to_text(artist.get("name"))
                    if artist_name:
                        artist_names.append(artist_name)
        artist = ", ".join(artist_names) if artist_names else "Unknown artist"

        duration_ms = item.get("duration_ms")
        duration_str = _format_duration_ms(duration_ms)

        external_urls = item.get("external_urls") or {}
        url = external_urls.get("spotify")
        if not isinstance(url, str) or not url:
            continue

        results.append(
            {
                "title": name,
                "artist": artist,
                "duration_str": duration_str,
                "url": url,
                "source": "spotify",
            }
        )
    return results


def _spotify_token(client_id: str, client_secret: str) -> str:
    response: requests.Response | None = None
    last_error: Exception | None = None
    for _ in range(2):
        try:
            response = requests.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                auth=(client_id, client_secret),
                timeout=12,
            )
            response.raise_for_status()
            token = response.json().get("access_token")
            if not isinstance(token, str) or not token:
                raise SearchNetworkError("Spotify token response missing access_token")
            return token
        except requests.RequestException as exc:
            last_error = exc

    if response is not None and response.status_code in {400, 401, 403}:
        raise SearchConfigError(
            "Spotify credentials were rejected. Verify SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET."
        )
    assert last_error is not None
    raise SearchNetworkError(
        f"Network failure while connecting to Spotify auth: {last_error}"
    ) from last_error


def _spotify_search_tracks(query: str, limit: int, token: str) -> dict[str, Any]:
    response: requests.Response | None = None
    last_error: Exception | None = None
    for _ in range(2):
        try:
            response = requests.get(
                "https://api.spotify.com/v1/search",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": query, "type": "track", "limit": limit},
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise SearchNetworkError("Unexpected Spotify search response format")
            return payload
        except requests.RequestException as exc:
            last_error = exc

    if response is not None and response.status_code == 401:
        raise SearchConfigError(
            "Spotify access token was rejected while searching. "
            "Try again with valid credentials."
        )
    assert last_error is not None
    raise SearchNetworkError(
        f"Network failure while searching Spotify: {last_error}"
    ) from last_error


def _to_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _format_duration(value: object) -> str:
    if isinstance(value, (int, float)) and value >= 0:
        total = int(value)
        minutes, seconds = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"
    return "--:--"


def _format_duration_ms(value: object) -> str:
    if isinstance(value, (int, float)) and value >= 0:
        return _format_duration(int(value) // 1000)
    return "--:--"
