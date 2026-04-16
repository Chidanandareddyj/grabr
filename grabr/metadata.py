"""Metadata embedding utilities using mutagen."""

from __future__ import annotations

import base64
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TPE1, TIT2
from mutagen.oggopus import OggOpus


@dataclass
class TrackMetadata:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    thumbnail_url: str | None = None


def fetch_cover_bytes(url: str | None) -> bytes | None:
    if not url:
        return None
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310
        return response.read()


def embed_metadata(
    file_path: Path,
    metadata: TrackMetadata,
    embed_cover: bool = True,
) -> None:
    suffix = file_path.suffix.lower()
    cover_data = fetch_cover_bytes(metadata.thumbnail_url) if embed_cover else None

    if suffix == ".mp3":
        _embed_mp3(file_path, metadata, cover_data)
        return
    if suffix == ".flac":
        _embed_flac(file_path, metadata, cover_data)
        return
    if suffix == ".opus":
        _embed_opus(file_path, metadata, cover_data)
        return

    audio = MutagenFile(file_path, easy=True)
    if audio is None:
        return
    if metadata.title:
        audio["title"] = [metadata.title]
    if metadata.artist:
        audio["artist"] = [metadata.artist]
    if metadata.album:
        audio["album"] = [metadata.album]
    audio.save()


def remove_cover_art(file_path: Path) -> None:
    suffix = file_path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = ID3(file_path)
        except ID3NoHeaderError:
            return
        tags.delall("APIC")
        tags.save(file_path)
        return
    if suffix == ".flac":
        audio = FLAC(file_path)
        audio.clear_pictures()
        audio.save()
        return
    if suffix == ".opus":
        audio = OggOpus(file_path)
        if "metadata_block_picture" in audio:
            del audio["metadata_block_picture"]
            audio.save()


def _embed_mp3(
    file_path: Path, metadata: TrackMetadata, cover_data: bytes | None
) -> None:
    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = ID3()
    if metadata.title:
        tags.add(TIT2(encoding=3, text=metadata.title))
    if metadata.artist:
        tags.add(TPE1(encoding=3, text=metadata.artist))
    if metadata.album:
        tags.add(TALB(encoding=3, text=metadata.album))
    if cover_data:
        tags.add(
            APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,
                desc="Cover",
                data=cover_data,
            )
        )
    tags.save(file_path)


def _embed_flac(
    file_path: Path, metadata: TrackMetadata, cover_data: bytes | None
) -> None:
    audio = FLAC(file_path)
    if metadata.title:
        audio["title"] = [metadata.title]
    if metadata.artist:
        audio["artist"] = [metadata.artist]
    if metadata.album:
        audio["album"] = [metadata.album]
    if cover_data:
        picture = Picture()
        picture.data = cover_data
        picture.type = 3
        picture.mime = "image/jpeg"
        audio.clear_pictures()
        audio.add_picture(picture)
    audio.save()


def _embed_opus(
    file_path: Path, metadata: TrackMetadata, cover_data: bytes | None
) -> None:
    audio = OggOpus(file_path)
    if metadata.title:
        audio["title"] = [metadata.title]
    if metadata.artist:
        audio["artist"] = [metadata.artist]
    if metadata.album:
        audio["album"] = [metadata.album]
    if cover_data:
        picture = Picture()
        picture.type = 3
        picture.mime = "image/jpeg"
        picture.desc = "Cover"
        picture.data = cover_data
        encoded = base64.b64encode(picture.write()).decode("ascii")
        audio["metadata_block_picture"] = [encoded]
    audio.save()
