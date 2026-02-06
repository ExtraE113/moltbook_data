"""CLI entry point for the Moltbook data downloader."""

import argparse
import asyncio
import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from .moltbook import download_moltbook

console = Console()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Download all data from Moltbook for research analysis"
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh, don't resume from checkpoint",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory to store downloaded data",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )

    console.print("[bold]Moltbook Data Downloader[/bold]")
    console.print(f"Data directory: {args.data_dir.absolute()}")
    console.print()

    asyncio.run(download_moltbook(args.data_dir, resume=not args.no_resume))


if __name__ == "__main__":
    main()
