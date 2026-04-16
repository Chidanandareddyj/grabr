"""CLI entrypoint for grabr."""

from __future__ import annotations

import click
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__
from .detector import InvalidUrlError
from .downloader import DependencyError, DownloadConfig, DownloadResult, download
from .search import SearchConfigError, SearchNetworkError, search_tracks


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


def _prompt_selection(result_count: int) -> int | None:
    for _ in range(2):
        answer = (
            console.input("Enter number to download (or q to quit): ").strip().lower()
        )
        if answer == "q":
            return None
        if answer.isdigit():
            selected = int(answer)
            if 1 <= selected <= result_count:
                return selected - 1
        console.print("Invalid selection. Enter a valid number or q.")
    raise click.ClickException("Invalid selection, exiting.")


def _run_search(
    query: str,
    source: str,
    limit: int,
    audio_format: str,
    output_dir: str,
    no_cover: bool,
    no_lyrics: bool,
) -> None:
    try:
        results = search_tracks(query=query, source=source, limit=limit)
    except SearchConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    except SearchNetworkError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if not results:
        console.print(f"No results found for '{query}', try different keywords")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", width=4)
    table.add_column("Title", overflow="fold")
    table.add_column("Artist", overflow="fold")
    table.add_column("Duration", justify="right", width=10)
    for idx, result in enumerate(results, start=1):
        table.add_row(
            str(idx),
            result["title"],
            result["artist"],
            result["duration_str"],
        )
    console.print(table)

    selected = _prompt_selection(len(results))
    if selected is None:
        return

    _run_download(
        url=results[selected]["url"],
        audio_format=audio_format,
        output_dir=output_dir,
        no_cover=no_cover,
        no_lyrics=no_lyrics,
    )


def _run_download(
    url: str,
    audio_format: str,
    output_dir: str,
    no_cover: bool,
    no_lyrics: bool,
) -> None:
    config = DownloadConfig(
        format=audio_format.lower(),
        output_dir=output_dir,
        embed_cover=not no_cover,
        download_lyrics=not no_lyrics,
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


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("command_or_url", required=False)
@click.argument("query", required=False)
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
@click.option(
    "--no-lyrics",
    is_flag=True,
    default=False,
    help="Skip lyric/caption sidecar downloads.",
)
@click.option(
    "--source",
    type=click.Choice(["youtube", "spotify"], case_sensitive=False),
    default="youtube",
    show_default=True,
    help="Search source for `grabr search`.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1, max=50),
    default=5,
    show_default=True,
    help="Result count for `grabr search`.",
)
@click.version_option(__version__, prog_name="grabr")
def main(
    command_or_url: str | None,
    query: str | None,
    audio_format: str,
    output_dir: str,
    no_cover: bool,
    no_lyrics: bool,
    source: str,
    limit: int,
) -> None:
    """Download audio from YouTube Music and Spotify URLs."""
    if not command_or_url:
        raise click.UsageError("Missing URL. Use: grabr <url>")

    if command_or_url.lower() == "search":
        if not query:
            raise click.UsageError('Missing query. Use: grabr search "your keywords"')
        _run_search(
            query=query,
            source=source.lower(),
            limit=limit,
            audio_format=audio_format,
            output_dir=output_dir,
            no_cover=no_cover,
            no_lyrics=no_lyrics,
        )
        return

    if query is not None:
        raise click.UsageError("Unexpected extra argument. Use: grabr <url>")

    _run_download(
        url=command_or_url,
        audio_format=audio_format,
        output_dir=output_dir,
        no_cover=no_cover,
        no_lyrics=no_lyrics,
    )


if __name__ == "__main__":
    main()
