# AGENTS.md

This repo is a single-package Python CLI project (`grabr`), not a monorepo.

## Fast Start (Windows-safe)

- From repo root (`.../Download/grabr`), install with: `python -m pip install -e .`
- Do **not** run `pip install -e .` directly in this environment; `pip.exe` may be blocked by App Control.
- Do **not** run install from parent folder (`.../Download`); `pyproject.toml` is in `.../Download/grabr`.

## Verified Commands

- Help: `grabr --help`
- Version: `grabr --version`
- Syntax sanity check: `python -m compileall grabr`
- Module fallback if PATH is not updated: `python -m grabr.cli --help`

No test suite/lint/typecheck config exists yet; use the commands above for lightweight verification.

## Package Boundaries (important)

- `grabr/cli.py`: Click + Rich only (argument parsing, progress, user-facing errors)
- `grabr/downloader.py`: core orchestration, dependency checks, retries, concurrency, source routing
- `grabr/detector.py`: URL source/type detection
- `grabr/metadata.py`: mutagen embedding/removal helpers
- `grabr/utils.py`: shell/process helpers, retries, path/file utilities

Keep CLI coupling out of core modules; preserve this separation for future Android wrapping.

## Runtime Expectations

- External tools are required in PATH: `yt-dlp`, `spotdl`.
- Python deps are pinned in `pyproject.toml` / `requirements.txt`.
- Entry point is defined in `pyproject.toml`: `grabr = "grabr.cli:main"`.
- Default output dir is `~/Music/grabr` (Windows resolves to `C:\Users\<user>\Music\grabr`).

## Current Behavioral Notes

- YouTube downloads set `preferredquality=320` for MP3 extraction.
- YouTube playlist downloads are written into a playlist-named subfolder under the selected output directory.
- Spotify album/playlist downloads are written into a collection-named subfolder when metadata includes collection names.
- Spotify downloads force `spotdl --audio youtube` to avoid environments blocked by YouTube Music.
- Spotify downloads explicitly pass `--bitrate 320k` for consistent default quality.

## Session Continuity

When starting a new OpenCode session, include a short state block to restore context quickly:

```
Project: grabr (Python CLI)
Repo: C:\Users\jlcre\OneDrive\Desktop\Download\grabr
Current status: [what is already done]
Next task: [exact change wanted]
Constraints: [any requirements]
```

If you change architecture or behavior, update this file in the same PR so future sessions inherit accurate context.
