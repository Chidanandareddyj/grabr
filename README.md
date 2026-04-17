# grabr

Download music from YouTube Music and Spotify via CLI.

## Prerequisites

- Python 3.10+
- ffmpeg

```bash
# Windows
winget install ffmpeg

# macOS
brew install ffmpeg

# Linux (Debian/Ubuntu)
sudo apt install ffmpeg
```

`grabr` requires system `ffmpeg` and `ffprobe` executables on PATH.
Installing `pip install ffmpeg` alone is not sufficient.

You can verify your setup with:

```bash
ffmpeg -version
ffprobe -version
```

If you see `Postprocessing: ffprobe and ffmpeg not found`, install ffmpeg for your OS
and restart your terminal so PATH changes are applied.

## Installation

```bash
pip install grabr
```

## Usage

```bash
grabr <url>                        # download track/playlist/album
grabr <url> --format flac          # choose format (mp3/flac/opus)
grabr <url> --output ~/Downloads   # custom output directory
grabr search "query"               # search and pick interactively
grabr search "query" --source spotify
grabr search "query" --limit 8
```

Spotify link downloads do not require Spotify developer credentials.
Paste a Spotify track/album/playlist link directly into `grabr`.

If Spotify rate-limits `spotdl`, `grabr` automatically falls back for single-track
Spotify links by mapping track metadata to a YouTube search and downloading the
best first match.

## Spotify Search Setup

Only needed for `--source spotify`.

- Get free credentials at developer.spotify.com
- Set environment variables:

```bash
SPOTIFY_CLIENT_ID=your_id
SPOTIFY_CLIENT_SECRET=your_secret
```

## Output

Files are saved to `~/Music/grabr/` by default.

## License

MIT
