# grabr

Terminal-first audio downloader for YouTube Music and Spotify links.

## Install

After cloning:

```bash
pip install -e .
```

## Usage

```bash
grabr <url>
grabr <url> --format flac
grabr <url> --output ~/Downloads
grabr <url> --no-cover
grabr --version
grabr --help
```

## Supported URLs

- YouTube Music: `music.youtube.com/...`
- YouTube playlist: `youtube.com/playlist?...`
- Spotify track: `open.spotify.com/track/...`
- Spotify album: `open.spotify.com/album/...`
- Spotify playlist: `open.spotify.com/playlist/...`

## Notes

- Downloads are audio-only and default to MP3 320kbps.
- Default output directory: `~/Music/grabr`.
- Duplicate files are skipped when detected.
- Playlist and album operations run with up to 3 concurrent downloads.

## Troubleshooting

If startup reports missing tools:

- Install dependencies: `pip install -r requirements.txt`
- Ensure binaries are available in PATH:
  - `yt-dlp`
  - `spotdl`
