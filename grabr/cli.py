"""CLI entrypoint for grabr."""

from __future__ import annotations

import click
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from . import __version__
from .detector import InvalidUrlError
from .downloader import DependencyError, DownloadConfig, DownloadResult, download


console = Console()


def _render_summary(result: DownloadResult) -> None:
    console.print(
        f"[bold green]Done[/bold green] {result.source.value}/{result.item_type.value}"
    )
    console.print(
        "Downloaded: "
        f"[green]{result.downloaded}[/green]  "
        "Skipped: "
        f"[yellow]{result.skipped}[/yellow]  "
        "Failed: "
        f"[red]{result.failed}[/red]"
    )
    if result.errors:
        for error in result.errors[:5]:
            console.print(f"[red]- {error}[/red]")


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("url", required=False)
@click.option(
    "--format",
    "audio_format",
    type=click.Choice(["mp3", "flac", "opus"], case_sensitive=False),
    default="mp3",
    show_default=True,
    help="Output audio format.",
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(path_type=str),
    default="~/Music/grabr",
    show_default=True,
    help="Output directory.",
)
@click.option(
    "--no-cover",
    is_flag=True,
    default=False,
    help="Skip cover art embedding.",
)
@click.version_option(__version__, prog_name="grabr")
def main(url: str | None, audio_format: str, output_dir: str, no_cover: bool) -> None:
    """Download audio from YouTube Music and Spotify URLs."""
    if not url:
        raise click.UsageError("Missing URL. Use: grabr <url>")

    config = DownloadConfig(
        format=audio_format.lower(),
        output_dir=output_dir,
        embed_cover=not no_cover,
    )

    total_tracks = 1
    completed = 0

    with Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress_bar:
        task_id = progress_bar.add_task("Downloading", total=total_tracks)

        def on_progress(event: str, payload: dict) -> None:
            nonlocal total_tracks, completed
            if event == "playlist_start":
                total_tracks = max(1, int(payload.get("total", 1)))
                progress_bar.update(task_id, total=total_tracks)
            elif event == "track_done":
                completed = int(payload.get("completed", completed + 1))
                title = payload.get("title") or "track"
                progress_bar.update(
                    task_id,
                    completed=min(completed, total_tracks),
                    description=f"Downloading: {title}",
                )

        try:
            result = download(url, config, on_progress)
        except InvalidUrlError as exc:
            raise click.ClickException(str(exc)) from exc
        except DependencyError as exc:
            raise click.ClickException(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(f"Download failed: {exc}") from exc

    _render_summary(result)
    if result.failed > 0:
        raise click.ClickException("Some tracks failed to download.")


if __name__ == "__main__":
    main()
