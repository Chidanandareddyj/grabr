"""Core download logic for grabr with no CLI coupling."""

from __future__ import annotations

import json
import importlib
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .detector import DetectedUrl, ItemType, Source, detect_url
from .utils import (
    expand_output_dir,
    file_exists,
    has_binary,
    sanitize_filename,
    run_command_stream,
    with_retry,
)

ProgressCallback = Callable[[str, dict], None]


@dataclass(frozen=True)
class DownloadConfig:
    format: str = "mp3"
    output_dir: str = "~/Music/grabr"
    embed_cover: bool = True
    max_concurrent: int = 3
    retry_count: int = 1
    backoff_seconds: float = 1.5


@dataclass
class TrackResult:
    source_url: str
    title: str | None = None
    file_path: str | None = None
    status: str = "downloaded"
    error: str | None = None


@dataclass
class DownloadResult:
    source: Source
    item_type: ItemType
    url: str
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    tracks: list[TrackResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class DependencyError(RuntimeError):
    """Raised when required tools are missing."""


SUPPORTED_FORMATS = {"mp3", "flac", "opus"}


def check_dependencies() -> None:
    missing: list[str] = []
    if not has_binary("spotdl"):
        missing.append("spotdl")
    if not has_binary("yt-dlp"):
        missing.append("yt-dlp")

    if missing:
        joined = ", ".join(missing)
        raise DependencyError(
            f"Missing required tools: {joined}. Install with: pip install spotdl yt-dlp"
        )

    try:
        importlib.import_module("yt_dlp")
    except ImportError as exc:
        raise DependencyError(
            "yt-dlp Python package is not installed. Install with: pip install yt-dlp"
        ) from exc

    try:
        import spotdl  # noqa: F401
    except ImportError as exc:
        raise DependencyError(
            "spotdl Python package is not installed. Install with: pip install spotdl"
        ) from exc

    try:
        importlib.import_module("mutagen")
    except ImportError as exc:
        raise DependencyError(
            "mutagen is not installed. Install with: pip install mutagen"
        ) from exc


def download(
    url: str, config: DownloadConfig, progress: ProgressCallback | None = None
) -> DownloadResult:
    if config.format not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported format '{config.format}'. Use mp3, flac, or opus."
        )

    check_dependencies()
    detected = detect_url(url)

    if detected.source is Source.YOUTUBE:
        return _download_youtube(detected, config, progress)
    return _download_spotify(detected, config, progress)


def _download_youtube(
    detected: DetectedUrl,
    config: DownloadConfig,
    progress: ProgressCallback | None,
) -> DownloadResult:
    output_dir = expand_output_dir(config.output_dir)
    result = DownloadResult(
        source=detected.source,
        item_type=detected.item_type,
        url=detected.normalized_url,
    )

    if detected.item_type is ItemType.TRACK:
        track_result = _download_youtube_track(
            detected.normalized_url, output_dir, config
        )
        _record_track_result(result, track_result)
        if progress:
            progress(
                "track_done", {"completed": 1, "total": 1, "title": track_result.title}
            )
        return result

    collection_title, entries = _extract_youtube_collection(detected.normalized_url)
    target_output_dir = _collection_output_dir(
        output_dir,
        collection_title,
        "youtube_playlist",
    )
    total = len(entries)
    if progress:
        progress("playlist_start", {"total": total})

    with ThreadPoolExecutor(max_workers=min(config.max_concurrent, 3)) as executor:
        futures = {
            executor.submit(
                _download_youtube_track,
                entry["url"],
                target_output_dir,
                config,
                entry.get("title"),
                entry.get("id"),
            ): entry
            for entry in entries
        }
        completed = 0
        for future in as_completed(futures):
            track_result = future.result()
            _record_track_result(result, track_result)
            completed += 1
            if progress:
                progress(
                    "track_done",
                    {
                        "completed": completed,
                        "total": total,
                        "title": track_result.title,
                        "status": track_result.status,
                    },
                )
    return result


def _download_youtube_track(
    url: str,
    output_dir: Path,
    config: DownloadConfig,
    title_hint: str | None = None,
    id_hint: str | None = None,
) -> TrackResult:
    yt_dlp = importlib.import_module("yt_dlp")
    track = TrackResult(source_url=url, title=title_hint)

    info = _extract_track_info(url)
    track.title = info.get("title", title_hint)
    video_id = info.get("id", id_hint) or "unknown"
    expected_name = f"{_safe_title(track.title)} [{video_id}].{config.format}"
    expected_path = output_dir / expected_name
    if file_exists(expected_path):
        track.status = "skipped"
        track.file_path = str(expected_path)
        return track

    def _run_download() -> None:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(output_dir / "%(title).180s [%(id)s].%(ext)s"),
            "noplaylist": True,
            "writethumbnail": config.embed_cover,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": config.format,
                    "preferredquality": "320",
                },
                {"key": "FFmpegMetadata"},
            ],
            "quiet": True,
            "no_warnings": True,
        }
        if config.embed_cover:
            ydl_opts["postprocessors"].append({"key": "EmbedThumbnail"})

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

    try:
        with_retry(
            _run_download,
            retries=config.retry_count,
            backoff_seconds=config.backoff_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        track.status = "failed"
        track.error = str(exc)
        return track

    if file_exists(expected_path):
        track.file_path = str(expected_path)
    else:
        candidates = sorted(output_dir.glob(f"*.{config.format}"), reverse=True)
        matching = [
            candidate for candidate in candidates if f"[{video_id}]" in candidate.name
        ]
        if matching:
            track.file_path = str(matching[0])
        else:
            track.status = "failed"
            track.error = "Download completed but file path could not be resolved."
            return track

    from .metadata import TrackMetadata, embed_metadata

    metadata = TrackMetadata(
        title=info.get("title"),
        artist=info.get("artist") or info.get("uploader"),
        album=info.get("album"),
        thumbnail_url=info.get("thumbnail"),
    )
    try:
        embed_metadata(Path(track.file_path), metadata, embed_cover=config.embed_cover)
    except Exception:  # noqa: BLE001
        pass

    return track


def _extract_track_info(url: str) -> dict:
    yt_dlp = importlib.import_module("yt_dlp")
    with yt_dlp.YoutubeDL(
        {"quiet": True, "noplaylist": True, "no_warnings": True}
    ) as ydl:
        return ydl.extract_info(url, download=False)


def _extract_youtube_collection(url: str) -> tuple[str | None, list[dict]]:
    yt_dlp = importlib.import_module("yt_dlp")
    with yt_dlp.YoutubeDL(
        {"quiet": True, "extract_flat": True, "no_warnings": True}
    ) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries") or []
    parsed: list[dict] = []
    for entry in entries:
        if not entry:
            continue
        entry_id = entry.get("id")
        if not entry_id:
            continue
        parsed.append(
            {
                "id": entry_id,
                "title": entry.get("title"),
                "url": f"https://www.youtube.com/watch?v={entry_id}",
            }
        )
    return info.get("title"), parsed


def _collection_output_dir(base_dir: Path, name: str | None, fallback: str) -> Path:
    folder = sanitize_filename(name or fallback)
    path = base_dir / folder
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download_spotify(
    detected: DetectedUrl,
    config: DownloadConfig,
    progress: ProgressCallback | None,
) -> DownloadResult:
    output_dir = expand_output_dir(config.output_dir)
    result = DownloadResult(
        source=detected.source,
        item_type=detected.item_type,
        url=detected.normalized_url,
    )

    save_file = Path(tempfile.gettempdir()) / "grabr_spotify_list.spotdl"
    save_cmd = [
        "spotdl",
        "save",
        detected.normalized_url,
        "--save-file",
        str(save_file),
        "--audio",
        "youtube",
        "--max-retries",
        "0",
    ]
    try:
        with_retry(
            lambda: _run_stream_command_checked(save_cmd, "spotdl save"),
            retries=config.retry_count,
            backoff_seconds=config.backoff_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        result.failed = 1
        result.errors.append(str(exc))
        return result

    songs = _read_spotdl_save_file(save_file)
    target_output_dir = output_dir
    if detected.item_type in {ItemType.PLAYLIST, ItemType.ALBUM}:
        collection_title = _spotify_collection_title(detected.item_type, songs)
        fallback = _spotify_collection_fallback(
            detected.normalized_url, detected.item_type
        )
        target_output_dir = _collection_output_dir(
            output_dir, collection_title, fallback
        )

    total = max(1, len(songs))
    if progress:
        progress("playlist_start", {"total": total})

    if not songs:
        songs = [{"url": detected.normalized_url, "name": "Spotify item"}]

    with ThreadPoolExecutor(max_workers=min(config.max_concurrent, 3)) as executor:
        futures = {
            executor.submit(
                _download_spotify_song, song, target_output_dir, config
            ): song
            for song in songs
        }
        completed = 0
        for future in as_completed(futures):
            track_result = future.result()
            _record_track_result(result, track_result)
            completed += 1
            if progress:
                progress(
                    "track_done",
                    {
                        "completed": completed,
                        "total": total,
                        "title": track_result.title,
                        "status": track_result.status,
                    },
                )

    return result


def _download_spotify_song(
    song: dict, output_dir: Path, config: DownloadConfig
) -> TrackResult:
    track_url = song.get("url") or song.get("song_url") or song.get("spotify_url")
    title = song.get("name") or song.get("title") or track_url
    track = TrackResult(source_url=track_url or "unknown", title=title)

    if not track_url:
        track.status = "failed"
        track.error = "Invalid song entry returned by spotdl save."
        return track

    output_template = str(output_dir / "{title} - {artists}.{output-ext}")
    cmd = [
        "spotdl",
        "download",
        track_url,
        "--output",
        output_template,
        "--format",
        config.format,
        "--threads",
        "1",
        "--overwrite",
        "skip",
        "--audio",
        "youtube",
        "--bitrate",
        "320k",
        "--max-retries",
        "0",
    ]

    def _run() -> None:
        _run_stream_command_checked(cmd, "spotdl download")

    try:
        with_retry(
            _run, retries=config.retry_count, backoff_seconds=config.backoff_seconds
        )
    except Exception as exc:  # noqa: BLE001
        track.status = "failed"
        track.error = str(exc)
        return track

    safe_prefix = sanitize_filename(_safe_title(title))
    candidates = sorted(
        output_dir.glob(f"{safe_prefix}*.{config.format}"), reverse=True
    )
    if candidates:
        track.file_path = str(candidates[0])
        track.status = "downloaded"
        if not config.embed_cover:
            try:
                from .metadata import remove_cover_art

                remove_cover_art(candidates[0])
            except Exception:  # noqa: BLE001
                pass
    else:
        track.status = "skipped"
    return track


def _read_spotdl_save_file(save_file: Path) -> list[dict]:
    if not save_file.exists():
        return []
    content = save_file.read_text(encoding="utf-8").strip()
    if not content:
        return []

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        lines = [line for line in content.splitlines() if line.strip()]
        songs: list[dict] = []
        for line in lines:
            try:
                songs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return songs

    if isinstance(parsed, list):
        return [entry for entry in parsed if isinstance(entry, dict)]
    return []


def _spotify_collection_title(item_type: ItemType, songs: list[dict]) -> str | None:
    if item_type is ItemType.ALBUM:
        candidates = ("album_name", "album")
    elif item_type is ItemType.PLAYLIST:
        candidates = (
            "playlist_name",
            "playlist",
            "list_name",
            "collection_name",
        )
    else:
        return None

    for song in songs:
        for key in candidates:
            value = song.get(key)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    return cleaned
    return None


def _spotify_collection_fallback(url: str, item_type: ItemType) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    item = item_type.value
    if len(parts) >= 2 and parts[0].lower() == item:
        return f"spotify_{item}_{parts[1][:8]}"
    return f"spotify_{item}"


def _run_stream_command_checked(command: list[str], context: str) -> None:
    tail: deque[str] = deque(maxlen=12)

    class _RateLimitDetected(RuntimeError):
        """Raised to abort long-running spotdl retries on rate limit."""

    def _collect(line: str) -> None:
        line = line.strip()
        if line:
            tail.append(line)
        lowered = line.lower()
        if "retry will occur after" in lowered or "rate/request limit" in lowered:
            raise _RateLimitDetected(line)

    try:
        code = run_command_stream(command, on_line=_collect)
    except _RateLimitDetected as exc:
        raise RuntimeError(f"{context} blocked by Spotify rate limit: {exc}") from exc

    if code == 0:
        return

    if tail:
        details = "\n".join(tail)
        raise RuntimeError(
            f"{context} failed with status {code}. Last output:\n{details}"
        )
    raise RuntimeError(f"{context} failed with status {code}.")


def _record_track_result(result: DownloadResult, track: TrackResult) -> None:
    result.tracks.append(track)
    if track.status == "downloaded":
        result.downloaded += 1
    elif track.status == "skipped":
        result.skipped += 1
    else:
        result.failed += 1
        if track.error:
            result.errors.append(track.error)


def _safe_title(title: str | None) -> str:
    if not title:
        return "unknown"
    return " ".join(title.split()).strip()
