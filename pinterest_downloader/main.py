#!/usr/bin/env python3
"""Pinterest Board Downloader CLI entry point."""

import asyncio
import logging
import re
import signal
import sys
from pathlib import Path
from typing import Literal, Optional

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from .archiver import Archiver
from .config import Config
from .downloader import DiskFullError, MediaDownloader, PermissionDeniedError
from .pinterest_client import PinterestClient, PinterestError

console = Console()
logger = logging.getLogger(__name__)

_shutdown_requested: bool = False
_shutdown_event: Optional[asyncio.Event] = None

AccessMode = Literal["api", "scrape", "browser"]


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with rich handler."""
    level = logging.DEBUG if verbose else logging.WARNING

    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[
            RichHandler(
                console=console,
                show_path=verbose,
                show_time=verbose,
                rich_tracebacks=True,
            )
        ],
    )

    if not verbose:
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
        logging.getLogger("asyncio").setLevel(logging.WARNING)


def signal_handler(signum, frame) -> None:
    """Handle shutdown signals gracefully - async-safe."""
    global _shutdown_requested
    _shutdown_requested = True
    if _shutdown_event:
        _shutdown_event.set()


@click.group()
@click.version_option(version="1.0.0", prog_name="pinterest-downloader")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Pinterest Board Downloader - Download and archive Pinterest boards."""
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


@cli.command()
@click.argument("board_url")
@click.option(
    "--output", "-o", type=click.Path(), default="./downloads", help="Output directory"
)
@click.option(
    "--archive",
    "-a",
    type=click.Choice(["zip", "tar", "tar.gz", "tar.bz2", "none"]),
    default="none",
    help="Archive format",
)
@click.option(
    "--config-file", "-c", type=click.Path(exists=True), help="Path to .env config file"
)
@click.option("--resume", "-r", is_flag=True, help="Resume interrupted download")
@click.option(
    "--method",
    "-m",
    type=click.Choice(["api", "scrape", "browser"]),
    default="scrape",
    help="Access method: scrape (default, public boards), browser (private boards, interactive login), api (requires Business credentials)",
)
@click.option(
    "--headless",
    is_flag=True,
    help="Run browser in headless mode (with --method=browser)",
)
@click.pass_context
def download(
    ctx: click.Context,
    board_url: str,
    output: str,
    archive: str,
    config_file: Optional[str],
    resume: bool,
    method: str,
    headless: bool,
) -> None:
    """Download all media from a Pinterest board."""
    config = _load_config(config_file, output)

    if not _validate_config(config):
        sys.exit(1)

    try:
        asyncio.run(
            _download_board(board_url, config, archive, resume, method, headless)
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Download cancelled by user.[/yellow]")
        sys.exit(130)
    except DiskFullError as e:
        console.print(f"\n[red]Disk full:[/red] {e}")
        sys.exit(1)
    except PermissionDeniedError as e:
        console.print(f"\n[red]Permission denied:[/red] {e}")
        sys.exit(1)
    except PinterestError as e:
        console.print(f"\n[red]Pinterest API error:[/red] {e}")
        sys.exit(1)


@cli.command("all-boards")
@click.option(
    "--output", "-o", type=click.Path(), default="./downloads", help="Output directory"
)
@click.option(
    "--archive",
    "-a",
    type=click.Choice(["zip", "tar", "tar.gz", "tar.bz2", "none"]),
    default="none",
    help="Archive format",
)
@click.option(
    "--config-file", "-c", type=click.Path(exists=True), help="Path to .env config file"
)
@click.option("--resume", "-r", is_flag=True, help="Resume interrupted downloads")
@click.pass_context
def all_boards(
    ctx: click.Context,
    output: str,
    archive: str,
    config_file: Optional[str],
    resume: bool,
) -> None:
    """Download all boards for the authenticated user."""
    config = _load_config(config_file, output)

    if not config.has_api_credentials:
        console.print(
            "[red]Error:[/red] API credentials required. Set PINTEREST_ACCESS_TOKEN in .env file."
        )
        sys.exit(1)

    if not _validate_config(config):
        sys.exit(1)

    try:
        asyncio.run(_download_all_boards(config, archive, resume))
    except KeyboardInterrupt:
        console.print("\n[yellow]Download cancelled by user.[/yellow]")
        sys.exit(130)
    except DiskFullError as e:
        console.print(f"\n[red]Disk full:[/red] {e}")
        sys.exit(1)
    except PinterestError as e:
        console.print(f"\n[red]Pinterest API error:[/red] {e}")
        sys.exit(1)


@cli.command("list")
@click.option(
    "--config-file", "-c", type=click.Path(exists=True), help="Path to .env config file"
)
@click.pass_context
def list_boards(ctx: click.Context, config_file: Optional[str]) -> None:
    """List all boards for the authenticated user."""
    config = _load_config(config_file, "./downloads")

    if not config.has_api_credentials:
        console.print(
            "[red]Error:[/red] API credentials required. Set PINTEREST_ACCESS_TOKEN in .env file."
        )
        sys.exit(1)

    try:
        asyncio.run(_list_boards(config))
    except PinterestError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.option("--force", "-f", is_flag=True, help="Overwrite existing .env file")
@click.option(
    "--browser-only", is_flag=True, help="Only setup browser login credentials"
)
@click.pass_context
def setup(ctx: click.Context, force: bool, browser_only: bool) -> None:
    """Interactive setup for credentials (API or browser login)."""
    env_path = Path(".env")

    if env_path.exists() and not force:
        console.print(
            "[yellow].env file already exists. Use --force to overwrite.[/yellow]"
        )
        return

    console.print("[bold]Pinterest Downloader Setup[/bold]\n")

    if not browser_only:
        console.print(
            "[cyan]=== Option 1: Pinterest API (Business Account Required) ===[/cyan]"
        )
        console.print(
            "[dim]Pros: Official API, reliable access to private boards[/dim]"
        )
        console.print("[dim]Cons: Requires business account + app approval[/dim]")
        console.print(
            "  - Convert personal account: https://pinterest.com/settings/convert-to-business"
        )
        console.print(
            "  - Create business account: https://pinterest.com/business/create\n"
        )
        console.print("1. Go to https://developers.pinterest.com/apps/")
        console.print("2. Create an app and get your App ID and App Secret")
        console.print("3. Generate an access token\n")
        use_api = click.confirm(
            "Would you like to setup API credentials?", default=False
        )
    else:
        use_api = False

    api_content = ""
    if use_api:
        app_id = click.prompt("  Pinterest App ID", type=str)
        app_secret = click.prompt("  Pinterest App Secret", type=str, hide_input=True)
        access_token = click.prompt("  Access Token", type=str, hide_input=True)
        api_content = f"""PINTEREST_APP_ID={app_id}
PINTEREST_APP_SECRET={app_secret}
PINTEREST_ACCESS_TOKEN={access_token}
"""

    console.print(
        "\n[cyan]=== Option 2: Browser Automation (Automated Login) ===[/cyan]"
    )
    console.print(
        "[dim]Pros: No business account needed, handles 2FA, PITA login[/dim]"
    )
    console.print(
        "[dim]Cons: Slower, requires visible browser (can use headless)[/dim]"
    )
    console.print("  Credentials stored securely in .env file and used for auto-login")

    use_browser = click.confirm(
        "Would you like to setup browser login credentials?", default=True
    )

    browser_content = ""
    if use_browser:
        username = click.prompt("  Pinterest username or email", type=str)
        password = click.prompt("  Pinterest password", type=str, hide_input=True)
        console.print(
            "\n[yellow]⚠ Security note:[/yellow] Credentials stored in plain text in .env file"
        )
        console.print("  Ensure the file has proper permissions: chmod 600 .env")
        browser_content = f"""PINTEREST_USERNAME={username}
PINTEREST_PASSWORD={password}
"""

    if not use_api and not use_browser:
        console.print("\n[yellow]No credentials provided. Nothing to save.[/yellow]")
        return

    env_content = api_content + browser_content

    try:
        env_path.write_text(env_content)
        # Try to set restrictive permissions if on Unix-like system
        try:
            import os

            os.chmod(env_path, 0o600)
        except:
            pass
        console.print(f"\n[green]✓ Credentials saved to {env_path.absolute()}[/green]")

        # Show what was saved
        if use_api:
            console.print("[dim]  • API credentials configured[/dim]")
        if use_browser:
            console.print("[dim]  • Browser login credentials configured[/dim]")

    except PermissionError:
        console.print(
            f"\n[red]Permission denied writing to {env_path.absolute()}[/red]"
        )
        sys.exit(1)


def _load_config(config_file: Optional[str], output: str) -> Config:
    """Load configuration from file or defaults."""
    if config_file:
        from dotenv import load_dotenv

        load_dotenv(config_file, override=True)

    return Config(output_dir=Path(output))


def _validate_config(config: Config) -> bool:
    """Validate configuration and check system requirements."""
    if not config.is_output_writable():
        console.print(
            f"[red]Error:[/red] Output directory is not writable: {config.output_dir}"
        )
        return False

    has_space, free_mb = config.check_disk_space(config.min_free_disk_mb)
    if not has_space:
        console.print(f"[red]Error:[/red] Insufficient disk space (free: {free_mb}MB)")
        return False

    return True


async def _download_board(
    board_url: str,
    config: Config,
    archive_format: str,
    resume: bool,
    method: str = "api",
    headless: bool = False,
) -> None:
    """Download a single board using specified method."""
    global _shutdown_event, _shutdown_requested
    _shutdown_event = asyncio.Event()
    _shutdown_requested = False

    try:
        username, board_name = PinterestClient.parse_board_url(board_url)
    except ValueError:
        username, board_name = "unknown", board_url.split("/")[-1]

    console.print(f"[bold]Downloading board:[/bold] {username}/{board_name}")
    console.print(f"[dim]Method: {method}[/dim]")

    config.ensure_output_dir()
    safe_board_name = _sanitize_board_name(f"{username}_{board_name}")
    board_dir = config.output_dir / safe_board_name
    board_dir.mkdir(parents=True, exist_ok=True)

    pins = []

    if method == "api":
        if not config.has_api_credentials:
            console.print("[red]Error:[/red] API credentials required for API method.")
            console.print(
                "Use --method scrape for public boards or --method browser for interactive login."
            )
            return

        async with PinterestClient(config) as client:
            console.print("[cyan]Fetching board information via API...[/cyan]")
            target_board = await client.get_board_by_name(board_name)

            if not target_board:
                console.print(f"[red]Board '{board_name}' not found[/red]")
                return

            console.print(
                f"[green]Found board:[/green] {target_board.name} ({target_board.pin_count} pins)"
            )
            pins = await client.get_board_pins(target_board.id)

    elif method == "scrape":
        from .scraper import PinterestScraper

        console.print("[cyan]Scraping public board...[/cyan]")

        async with PinterestScraper(config) as scraper:
            pins = await scraper.get_board_pins_from_url(board_url)

    elif method == "browser":
        try:
            from .browser_automation import PinterestBrowser, PLAYWRIGHT_AVAILABLE

            if not PLAYWRIGHT_AVAILABLE:
                console.print("[red]Error:[/red] Playwright not installed. Run:")
                console.print("  pip install playwright && playwright install chromium")
                return

            async with PinterestBrowser(
                config, headless=headless, shutdown_event=_shutdown_event
            ) as browser:
                if not await browser.ensure_logged_in():
                    console.print("[red]Failed to log in.[/red]")
                    return

                pins = await browser.get_board_pins_from_url(board_url)
        except ImportError as e:
            console.print(f"[red]Error:[/red] {e}")
            return

    if _shutdown_requested or _shutdown_event.is_set():
        console.print("[yellow]Download interrupted.[/yellow]")
        return

    console.print(f"[green]Found {len(pins)} pins to download[/green]")

    if not pins:
        console.print("[yellow]No downloadable pins found.[/yellow]")
        return

    async with MediaDownloader(config, board_dir) as downloader:
        existing_files = downloader.get_existing_files() if resume else set()

        if resume and existing_files:
            console.print(
                f"[cyan]Found {len(existing_files)} files in output directory[/cyan]"
            )
            # Debug info to help understand pin-what-file mapping
            if len(pins) > 0 and len(existing_files) > len(pins):
                console.print(
                    f"[dim]Note: More files ({len(existing_files)}) than pins ({len(pins)}). Some files may be unrelated.[/dim]"
                )

        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
            disable=not _is_tty(),
        ) as progress:
            results = await downloader.download_pins(pins, progress, existing_files)

        # Enhanced summary before print
        if resume:
            skipped_count = sum(1 for r in results if r.skipped)
            downloaded_count = sum(1 for r in results if r.success and not r.skipped)
            if skipped_count > 0:
                console.print(
                    f"[dim]Resume: {skipped_count} pins matched existing files[/dim]"
                )

    _print_download_summary(results, len(pins))

    if archive_format != "none" and not _shutdown_requested:
        archiver = Archiver(board_dir)
        archiver.create_archive(archive_format, board_name)


async def _download_all_boards(
    config: Config, archive_format: str, resume: bool
) -> None:
    """Download all boards for the authenticated user."""
    global _shutdown_event, _shutdown_requested
    _shutdown_event = asyncio.Event()
    _shutdown_requested = False

    config.ensure_output_dir()

    async with PinterestClient(config) as client:
        console.print("[cyan]Fetching your boards...[/cyan]")
        boards = await client.get_user_boards()

        if not boards:
            console.print("[yellow]No boards found.[/yellow]")
            return

        console.print(f"[green]Found {len(boards)} boards[/green]\n")

        for board in boards:
            if _shutdown_requested or _shutdown_event.is_set():
                console.print("[yellow]Download interrupted.[/yellow]")
                return

            console.print(
                f"[bold]Processing:[/bold] {board.name} ({board.pin_count} pins)"
            )

            safe_name = _sanitize_board_name(board.name)
            board_dir = config.output_dir / safe_name
            board_dir.mkdir(parents=True, exist_ok=True)

            pins = await client.get_board_pins(board.id)

            if pins:
                async with MediaDownloader(config, board_dir) as downloader:
                    existing_files = (
                        downloader.get_existing_files() if resume else set()
                    )

                    with Progress(
                        TextColumn("[bold blue]{task.description}"),
                        BarColumn(),
                        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                        console=console,
                        disable=not _is_tty(),
                    ) as progress:
                        results = await downloader.download_pins(
                            pins, progress, existing_files
                        )

                    successful = sum(1 for r in results if r.success)
                    skipped = sum(1 for r in results if r.skipped)

                    status = f"[green]Downloaded:[/green] {successful}/{len(pins)}"
                    if skipped:
                        status += f" ({skipped} resumed)"
                    console.print(f"{status}\n")

            if archive_format != "none" and not _shutdown_requested:
                archiver = Archiver(board_dir)
                archiver.create_archive(archive_format, board.name)


async def _list_boards(config: Config) -> None:
    """List all boards for the authenticated user."""
    async with PinterestClient(config) as client:
        console.print("[cyan]Fetching your boards...[/cyan]")
        boards = await client.get_user_boards()

        if not boards:
            console.print("[yellow]No boards found.[/yellow]")
            return

        table = Table(title="Your Pinterest Boards")
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Pins", justify="right", style="green")
        table.add_column("Description", style="dim")

        for board in boards:
            desc = (
                board.description[:50] + "..."
                if len(board.description) > 50
                else board.description
            )
            table.add_row(board.name, str(board.pin_count), desc)

        console.print(table)


def _print_download_summary(results: list, total_pins: int) -> None:
    """Print download summary."""
    successful = sum(1 for r in results if r.success and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    failed = len(results) - (successful + skipped)
    total_bytes = sum(getattr(r, "bytes_downloaded", 0) for r in results)

    console.print()

    # New pins downloaded vs pins total
    if skipped == 0:
        console.print(f"[green]✓ All {successful} pins downloaded successfully[/green]")
    else:
        console.print(f"[green]✓ Downloaded {successful} new pins[/green]")
        console.print(f"[dim]  {skipped} pins already existed on disk (skipped)[/dim]")

        # Show what happened to pins that were "missed"
        if successful + skipped < total_pins:
            missing = total_pins - successful - skipped
            console.print(
                f"[yellow]  ⚠ {missing} pins from board weren't downloaded (may require login)[/yellow]"
            )

    if failed:
        console.print(f"[red]  ✗ {failed} pins failed to download[/red]")

    if total_bytes > 0:
        console.print(
            f"[dim]  Total space used: {Archiver._format_size(total_bytes)}[/dim]"
        )


def _is_tty() -> bool:
    """Check if stdout is a TTY."""
    return sys.stdout.isatty()


def _sanitize_board_name(name: str) -> str:
    """Sanitize board name for use as directory name."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe[:100] if safe else "unnamed_board"


if __name__ == "__main__":
    cli()
