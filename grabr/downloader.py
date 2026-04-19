"""Core download logic for grabr with no CLI coupling."""

from __future__ import annotations

import importlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import requests

from .detector import DetectedUrl, ItemType, Source, detect_url
from .utils import (
    expand_output_dir,
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
    download_lyrics: bool = False
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


def _emit_progress(
    progress: ProgressCallback | None, event: str, payload: dict | None = None
) -> None:
    if progress is None:
        return
    progress(event, payload or {})


def check_dependencies() -> None:
    missing_cli: list[str] = []
    for tool in ("spotdl", "yt-dlp", "ffmpeg", "ffprobe"):
        if not has_binary(tool):
            missing_cli.append(tool)

    if missing_cli:
        lines = [f"Missing required tools on PATH: {', '.join(missing_cli)}."]
        if any(tool in {"ffmpeg", "ffprobe"} for tool in missing_cli):
            lines.extend(
                [
                    "ffmpeg and ffprobe must be system binaries (executables).",
                    "Installing the Python package via `pip install ffmpeg` is not enough.",
                    "Install ffmpeg for your OS and ensure its bin directory is on PATH:",
                    "  - Windows: winget install ffmpeg",
                    "  - macOS: brew install ffmpeg",
                    "  - Debian/Ubuntu: sudo apt install ffmpeg",
                    "Verify with: ffmpeg -version and ffprobe -version",
                ]
            )
        if any(tool in {"spotdl", "yt-dlp"} for tool in missing_cli):
            lines.append(
                "Install Python CLI dependencies with: pip install spotdl yt-dlp"
            )
        raise DependencyError("\n".join(lines))

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
        _emit_progress(progress, "status", {"message": "Preparing YouTube track"})
        track_result = _download_youtube_track(
            detected.normalized_url,
            output_dir,
            config,
            progress=progress,
        )
        _record_track_result(result, track_result)
        if progress:
            progress(
                "track_done", {"completed": 1, "total": 1, "title": track_result.title}
            )
        return result

    _emit_progress(progress, "status", {"message": "Resolving YouTube collection"})
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
                progress,
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
    progress: ProgressCallback | None = None,
) -> TrackResult:
    yt_dlp = importlib.import_module("yt_dlp")
    track = TrackResult(source_url=url, title=title_hint)

    video_id = id_hint or _youtube_video_id(url) or "unknown"
    if video_id != "unknown":
        existing = sorted(
            output_dir.glob(f"*[{video_id}].{config.format}"), reverse=True
        )
        if existing:
            _emit_progress(
                progress,
                "background",
                {"message": f"Skipped existing file for video id {video_id}"},
            )
            track.status = "skipped"
            track.file_path = str(existing[0])
            return track

    downloaded_info: dict | None = None
    last_bucket = -1

    def _on_yt_progress(data: dict) -> None:
        nonlocal last_bucket
        status = str(data.get("status") or "")
        if status == "downloading":
            downloaded = data.get("downloaded_bytes")
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            if isinstance(downloaded, (int, float)) and isinstance(total, (int, float)):
                if total > 0:
                    percent = int((float(downloaded) / float(total)) * 100)
                    bucket = percent // 10
                    if bucket > last_bucket:
                        last_bucket = bucket
                        _emit_progress(
                            progress,
                            "status",
                            {"message": f"Downloading audio ({min(percent, 100)}%)"},
                        )
        elif status == "finished":
            _emit_progress(progress, "status", {"message": "Converting and tagging"})

    def _run_download(include_lyrics: bool) -> None:
        nonlocal downloaded_info
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(output_dir / "%(title).180s [%(id)s].%(ext)s"),
            "noplaylist": True,
            "writethumbnail": config.embed_cover,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": config.format,
                },
                {"key": "FFmpegMetadata"},
            ],
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [_on_yt_progress],
        }
        if config.embed_cover:
            ydl_opts["postprocessors"].append({"key": "EmbedThumbnail"})
        if include_lyrics:
            ydl_opts["writesubtitles"] = True
            ydl_opts["writeautomaticsub"] = True
            ydl_opts["subtitleslangs"] = ["en", "en.*"]
            ydl_opts["subtitlesformat"] = "best"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_info = info if isinstance(info, dict) else None

    try:
        _emit_progress(progress, "status", {"message": "Starting YouTube download"})
        with_retry(
            lambda: _run_download(config.download_lyrics),
            retries=config.retry_count,
            backoff_seconds=config.backoff_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        subtitle_issue = "subtitle" in message and (
            "too many requests" in message
            or "http error 429" in message
            or "unable to download" in message
        )
        if config.download_lyrics and subtitle_issue:
            _emit_progress(
                progress,
                "background",
                {"message": "Subtitle fetch hit rate-limit, retrying without lyrics"},
            )
            try:
                with_retry(
                    lambda: _run_download(False),
                    retries=config.retry_count,
                    backoff_seconds=config.backoff_seconds,
                )
            except Exception as fallback_exc:  # noqa: BLE001
                track.status = "failed"
                track.error = str(fallback_exc)
                return track
        else:
            track.status = "failed"
            track.error = str(exc)
            return track

    if isinstance(downloaded_info, dict):
        track.title = downloaded_info.get("title") or track.title
        video_id = downloaded_info.get("id") or video_id

    _emit_progress(progress, "status", {"message": "Finalizing output file"})
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

    return track


def _youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    values = parse_qs(parsed.query).get("v") or []
    if values and values[0]:
        return values[0]
    return None


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

    if progress:
        progress("playlist_start", {"total": 1})
    _emit_progress(progress, "status", {"message": "Preparing Spotify download"})

    output_template = str(output_dir / "{title} - {artists}.{output-ext}")
    cmd = [
        "spotdl",
        "download",
        detected.normalized_url,
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
        "--max-retries",
        "0",
    ]
    if config.download_lyrics:
        cmd.extend(["--lyrics", "synced", "musixmatch", "genius", "azlyrics"])
        cmd.append("--generate-lrc")

    before = {str(path.resolve()) for path in output_dir.rglob(f"*.{config.format}")}

    def _spotdl_stream_line(line: str) -> None:
        lowered = line.lower()
        if "rate/request limit" in lowered or "retry will occur after" in lowered:
            _emit_progress(progress, "background", {"message": line})
            return
        if "downloading" in lowered or "[download]" in lowered:
            _emit_progress(progress, "status", {"message": "Downloading audio"})
            return
        if "ffmpeg" in lowered or "converting" in lowered:
            _emit_progress(progress, "status", {"message": "Converting and tagging"})
            return
        if "spotify" in lowered and "fetch" in lowered:
            _emit_progress(
                progress,
                "status",
                {"message": "Resolving Spotify metadata"},
            )
            return
        if "error" in lowered or "warning" in lowered:
            _emit_progress(progress, "background", {"message": line})

    try:
        _emit_progress(progress, "status", {"message": "Running spotdl"})
        with_retry(
            lambda: _run_stream_command_checked(
                cmd,
                "spotdl download",
                on_line=_spotdl_stream_line,
            ),
            retries=config.retry_count,
            backoff_seconds=config.backoff_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        message_raw = str(exc)
        message = _normalize_spotify_error(message_raw)
        if (
            detected.item_type is ItemType.TRACK
            and _is_spotify_rate_limit_error(message_raw)
        ):
            try:
                track = _download_spotify_track_via_youtube(
                    spotify_url=detected.normalized_url,
                    output_dir=output_dir,
                    config=config,
                    progress=progress,
                )
                _record_track_result(result, track)
                if progress:
                    progress(
                        "track_done",
                        {
                            "completed": 1,
                            "total": 1,
                            "title": track.title or "Spotify track (YouTube fallback)",
                            "status": track.status,
                        },
                    )
                return result
            except Exception as fallback_exc:  # noqa: BLE001
                message = (
                    f"{message}\nFallback failed while mapping Spotify -> YouTube: "
                    f"{fallback_exc}"
                )

        result.failed = 1
        result.errors.append(message)
        result.tracks.append(
            TrackResult(
                source_url=detected.normalized_url,
                title="Spotify item",
                status="failed",
                error=message,
            )
        )
        if progress:
            progress(
                "track_done",
                {
                    "completed": 1,
                    "total": 1,
                    "title": "Spotify item",
                    "status": "failed",
                },
            )
        return result

    after = sorted(output_dir.rglob(f"*.{config.format}"), reverse=True)
    new_files = [path for path in after if str(path.resolve()) not in before]
    if new_files:
        for path in new_files:
            if not config.embed_cover:
                try:
                    from .metadata import remove_cover_art

                    remove_cover_art(path)
                except Exception:  # noqa: BLE001
                    pass
            _record_track_result(
                result,
                TrackResult(
                    source_url=detected.normalized_url,
                    title=path.stem,
                    file_path=str(path),
                    status="downloaded",
                ),
            )
        if progress:
            progress(
                "track_done",
                {
                    "completed": 1,
                    "total": 1,
                    "title": f"Spotify item ({len(new_files)} tracks)",
                    "status": "downloaded",
                },
            )
        return result

    skipped = TrackResult(
        source_url=detected.normalized_url,
        title="Spotify item",
        status="skipped",
    )
    _record_track_result(result, skipped)
    if progress:
        progress(
            "track_done",
            {"completed": 1, "total": 1, "title": "Spotify item", "status": "skipped"},
        )

    return result


def _normalize_spotify_error(message: str) -> str:
    lowered = message.lower()
    timeout_signals = (
        "timed out",
        "timeout",
        "read timed out",
        "connecttimeout",
        "connection aborted",
    )
    if any(signal in lowered for signal in timeout_signals):
        return (
            "Spotify provider timed out while fetching this link. "
            "No Spotify client ID is needed for direct link downloads. "
            "Please retry in a moment."
        )
    return message


def _is_spotify_rate_limit_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        signal in lowered
        for signal in (
            "rate/request limit",
            "retry will occur after",
            "spotify rate limit",
            "http error 429",
        )
    )


def _download_spotify_track_via_youtube(
    spotify_url: str,
    output_dir: Path,
    config: DownloadConfig,
    progress: ProgressCallback | None = None,
) -> TrackResult:
    _emit_progress(
        progress,
        "background",
        {"message": "Spotify rate-limited, switching to YouTube fallback"},
    )
    _emit_progress(progress, "status", {"message": "Resolving Spotify metadata"})
    query = _spotify_oembed_track_query(spotify_url)
    _emit_progress(progress, "status", {"message": "Searching YouTube match"})
    youtube_url, youtube_title = _youtube_first_result_url(query)
    track = _download_youtube_track(
        url=youtube_url,
        output_dir=output_dir,
        config=config,
        title_hint=youtube_title,
        progress=progress,
    )
    track.source_url = spotify_url
    return track


def _spotify_oembed_track_query(spotify_url: str) -> str:
    response = requests.get(
        "https://open.spotify.com/oembed",
        params={"url": spotify_url},
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Spotify oEmbed returned an invalid response.")

    title = payload.get("title")
    artist = payload.get("author_name")
    if not isinstance(title, str) or not title.strip():
        raise RuntimeError("Spotify oEmbed did not provide a track title.")

    title_text = title.strip()
    if isinstance(artist, str) and artist.strip():
        return f"{artist.strip()} - {title_text}"

    cleaned_title = title_text
    suffixes = (
        " - from \"",
        " (from \"",
        " - remastered",
        " (remastered",
    )
    lowered = title_text.lower()
    for suffix in suffixes:
        index = lowered.find(suffix)
        if index > 0:
            cleaned_title = title_text[:index].strip()
            break
    return cleaned_title or title_text


def _youtube_first_result_url(query: str) -> tuple[str, str | None]:
    yt_dlp = importlib.import_module("yt_dlp")
    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "extract_flat": True,
            "no_warnings": True,
        }
    ) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=False)

    entries = info.get("entries") or []
    first = entries[0] if entries else None
    if not isinstance(first, dict):
        raise RuntimeError("No YouTube match found for Spotify fallback query.")

    video_id = first.get("id")
    if isinstance(video_id, str) and video_id:
        return f"https://www.youtube.com/watch?v={video_id}", first.get("title")

    webpage_url = first.get("webpage_url")
    if isinstance(webpage_url, str) and webpage_url:
        return webpage_url, first.get("title")

    raise RuntimeError("Could not resolve a YouTube URL from fallback search result.")


def _run_stream_command_checked(
    command: list[str],
    context: str,
    on_line: Callable[[str], None] | None = None,
) -> None:
    tail: deque[str] = deque(maxlen=12)

    class _RateLimitDetected(RuntimeError):
        """Raised to abort long-running spotdl retries on rate limit."""

    def _collect(line: str) -> None:
        line = line.strip()
        if line:
            tail.append(line)
            if on_line is not None:
                on_line(line)
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
