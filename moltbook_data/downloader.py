#!/usr/bin/env python3
"""
Moltbook Data Downloader

Downloads all available data from Moltbook API:
- Posts with comments (sorted oldest to newest)
- Submolts (communities) with full details
- Agent profiles
- All raw API responses preserved (no filtering)

Data is stored as JSON files with checkpointing for resume capability.
Uses async parallel fetching with rate limiting.
"""

import asyncio
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn

# Configuration
BASE_URL = "https://www.moltbook.com/api/v1"
DATA_DIR = Path("data")

# Rate limiting: 100 requests/min = ~1.67/sec
# With parallel requests, we batch and add delays
MAX_CONCURRENT = 10  # Concurrent requests
BATCH_DELAY = 0.5    # Delay between batches to respect rate limits

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # Exponential backoff: 2s, 4s, 8s

console = Console()


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename across all platforms."""
    # Replace characters problematic on Windows/Unix: < > : " / \ | ? *
    return re.sub(r'[<>:"/\\|?*]', '_', name)


@dataclass
class DownloadState:
    """Tracks download progress for checkpointing."""
    posts_with_details: set[str] = field(default_factory=set)
    submolts_fetched: set[str] = field(default_factory=set)
    agents_fetched: set[str] = field(default_factory=set)
    agent_names_discovered: set[str] = field(default_factory=set)
    submolt_names_discovered: set[str] = field(default_factory=set)
    last_checkpoint: str = ""

    def save(self, path: Path):
        """Save checkpoint to disk."""
        data = {
            "posts_with_details": list(self.posts_with_details),
            "submolts_fetched": list(self.submolts_fetched),
            "agents_fetched": list(self.agents_fetched),
            "agent_names_discovered": list(self.agent_names_discovered),
            "submolt_names_discovered": list(self.submolt_names_discovered),
            "last_checkpoint": datetime.now(timezone.utc).isoformat(),
        }
        # Write atomically
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2, default=str))
        tmp_path.rename(path)

    @classmethod
    def load(cls, path: Path) -> "DownloadState":
        """Load checkpoint from disk."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            state = cls()
            state.posts_with_details = set(data.get("posts_with_details", []))
            state.submolts_fetched = set(data.get("submolts_fetched", []))
            state.agents_fetched = set(data.get("agents_fetched", []))
            state.agent_names_discovered = set(data.get("agent_names_discovered", []))
            state.submolt_names_discovered = set(data.get("submolt_names_discovered", []))
            state.last_checkpoint = data.get("last_checkpoint", "")
            return state
        except (json.JSONDecodeError, KeyError) as e:
            console.print(f"[yellow]Warning: Could not load checkpoint: {e}[/yellow]")
            return cls()


class AsyncMoltbookClient:
    """Async HTTP client for Moltbook API with retry logic."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=30.0,
            headers={"User-Agent": "MoltbookResearch/1.0"},
            limits=httpx.Limits(max_connections=MAX_CONCURRENT, max_keepalive_connections=MAX_CONCURRENT)
        )
        self.request_count = 0
        self.not_found_count = 0  # Track 404/405 separately

    async def get(self, endpoint: str, params: dict | None = None) -> dict | None:
        """Make a GET request with retry logic for transient failures."""
        last_error = None

        for attempt in range(MAX_RETRIES + 1):
            self.request_count += 1

            try:
                response = await self.client.get(endpoint, params=params)
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                status = e.response.status_code

                # 404/405 are not retryable - resource doesn't exist
                if status in (404, 405):
                    self.not_found_count += 1
                    return None

                # 5xx errors are retryable
                if status >= 500 and attempt < MAX_RETRIES:
                    delay = RETRY_BACKOFF_BASE ** attempt
                    console.print(f"[yellow]HTTP {status} on {endpoint}, retrying in {delay}s...[/yellow]")
                    await asyncio.sleep(delay)
                    last_error = e
                    continue

                # 4xx (except 404/405) are not retryable
                console.print(f"[red]HTTP {status}: {endpoint}[/red]")
                return None

            except httpx.RequestError as e:
                # Network errors are retryable
                if attempt < MAX_RETRIES:
                    delay = RETRY_BACKOFF_BASE ** attempt
                    console.print(f"[yellow]Network error on {endpoint}, retrying in {delay}s...[/yellow]")
                    await asyncio.sleep(delay)
                    last_error = e
                    continue

                console.print(f"[red]Request error after {MAX_RETRIES} retries: {e}[/red]")
                return None

            except json.JSONDecodeError:
                console.print(f"[red]Invalid JSON: {endpoint}[/red]")
                return None

        # Exhausted retries
        if last_error:
            console.print(f"[red]Failed after {MAX_RETRIES} retries: {endpoint}[/red]")
        return None

    async def close(self):
        await self.client.aclose()


def extract_names_from_response(data: dict, state: DownloadState):
    """Extract agent and submolt names from any API response for later fetching."""

    def extract_from_author(author):
        if author and isinstance(author, dict):
            name = author.get("name") or author.get("username")
            if name:
                state.agent_names_discovered.add(name)

    def extract_from_submolt(submolt):
        if submolt and isinstance(submolt, dict):
            name = submolt.get("name") or submolt.get("slug")
            if name:
                state.submolt_names_discovered.add(name)

    def extract_from_comments(comments):
        for comment in comments or []:
            extract_from_author(comment.get("author"))
            extract_from_comments(comment.get("replies", []))

    # Extract from post
    post = data.get("post", {})
    extract_from_author(post.get("author"))
    extract_from_submolt(post.get("submolt"))

    # Extract from comments
    extract_from_comments(data.get("comments", []))

    # Extract from agent profile
    agent = data.get("agent", {})
    if agent:
        name = agent.get("name")
        if name:
            state.agent_names_discovered.add(name)

    # Extract from submolt
    submolt = data.get("submolt", {})
    extract_from_submolt(submolt)


async def fetch_all_post_ids(client: AsyncMoltbookClient) -> list[dict]:
    """Fetch all posts from the feed to get their IDs and basic info."""
    all_posts = []
    offset = 0

    console.print("[bold blue]Fetching post list from feed...[/bold blue]")

    while True:
        data = await client.get("/posts", params={"sort": "new", "offset": offset, "limit": 100})

        if not data or not data.get("success"):
            console.print(f"[yellow]Failed to fetch posts at offset {offset}[/yellow]")
            break

        posts = data.get("posts", [])
        if not posts:
            break

        all_posts.extend(posts)
        console.print(f"  Fetched {len(all_posts)} post IDs...", end="\r")

        if not data.get("has_more", False):
            break

        offset = data.get("next_offset", offset + len(posts))
        await asyncio.sleep(0.1)

    console.print(f"\n[green]Found {len(all_posts)} total posts[/green]")

    # Reverse to get oldest first
    all_posts.reverse()
    return all_posts


async def fetch_post_with_comments(client: AsyncMoltbookClient, post_id: str) -> dict | None:
    """Fetch a single post with its comments - returns RAW API response."""
    data = await client.get(f"/posts/{post_id}")
    if not data or not data.get("success"):
        return None

    # Add metadata but preserve ALL original fields
    data["_downloaded_at"] = datetime.now(timezone.utc).isoformat()
    data["_endpoint"] = f"/posts/{post_id}"
    return data


async def fetch_submolt_list(client: AsyncMoltbookClient) -> list[dict]:
    """Fetch submolt list to discover all submolt names."""
    all_submolts = []
    offset = 0

    console.print("[bold blue]Fetching submolt list...[/bold blue]")

    while True:
        data = await client.get("/submolts", params={"offset": offset, "limit": 100})

        if not data or not data.get("success"):
            break

        submolts = data.get("submolts", [])
        if not submolts:
            break

        all_submolts.extend(submolts)

        if not data.get("has_more", False):
            break

        offset = data.get("next_offset", offset + len(submolts))
        await asyncio.sleep(0.1)

    console.print(f"[green]Found {len(all_submolts)} submolts[/green]")
    return all_submolts


async def fetch_submolt_details(client: AsyncMoltbookClient, name: str) -> dict | None:
    """Fetch full submolt details - returns RAW API response."""
    data = await client.get(f"/submolts/{name}")
    if not data or not data.get("success"):
        return None

    data["_downloaded_at"] = datetime.now(timezone.utc).isoformat()
    data["_endpoint"] = f"/submolts/{name}"
    return data


async def fetch_agent_profile(client: AsyncMoltbookClient, name: str) -> dict | None:
    """Fetch full agent profile - returns RAW API response."""
    data = await client.get("/agents/profile", params={"name": name})
    if not data or not data.get("success"):
        return None

    data["_downloaded_at"] = datetime.now(timezone.utc).isoformat()
    data["_endpoint"] = f"/agents/profile?name={name}"
    return data


async def download_all_async(data_dir: Path, resume: bool = True):
    """Main async download function."""

    # Setup directories
    posts_dir = data_dir / "posts"
    submolts_dir = data_dir / "submolts"
    agents_dir = data_dir / "agents"

    for d in [posts_dir, submolts_dir, agents_dir]:
        d.mkdir(parents=True, exist_ok=True)

    checkpoint_path = data_dir / "checkpoint.json"

    # Load checkpoint
    state = DownloadState.load(checkpoint_path) if resume else DownloadState()

    if resume and state.last_checkpoint:
        console.print(f"[cyan]Resuming from checkpoint: {state.last_checkpoint}[/cyan]")
        console.print(f"  Posts: {len(state.posts_with_details)}")
        console.print(f"  Submolts: {len(state.submolts_fetched)}")
        console.print(f"  Agents: {len(state.agents_fetched)}")

    client = AsyncMoltbookClient()

    try:
        # ============================================================
        # PHASE 1: Get all post IDs and fetch posts with comments
        # ============================================================
        all_posts = await fetch_all_post_ids(client)

        # Extract agent/submolt names from feed data
        for post in all_posts:
            author = post.get("author")
            if author:
                name = author.get("name") or author.get("username")
                if name:
                    state.agent_names_discovered.add(name)
            submolt = post.get("submolt")
            if submolt:
                name = submolt.get("name")
                if name:
                    state.submolt_names_discovered.add(name)

        posts_to_fetch = [p for p in all_posts if p.get("id") not in state.posts_with_details]

        if posts_to_fetch:
            console.print(f"[bold blue]Fetching {len(posts_to_fetch)} posts with comments...[/bold blue]")

            completed = 0
            failed = 0

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Downloading posts...", total=len(posts_to_fetch))

                batch_size = MAX_CONCURRENT
                for i in range(0, len(posts_to_fetch), batch_size):
                    batch = posts_to_fetch[i:i + batch_size]

                    tasks = [fetch_post_with_comments(client, p["id"]) for p in batch]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for post_info, result in zip(batch, results):
                        post_id = post_info["id"]

                        if isinstance(result, Exception):
                            console.print(f"[red]Error fetching {post_id}: {result}[/red]")
                            failed += 1
                            continue

                        if result is None:
                            failed += 1
                            continue

                        # Save RAW response
                        filepath = posts_dir / f"{post_id}.json"
                        filepath.write_text(json.dumps(result, indent=2, default=str))

                        # Extract names for later fetching
                        extract_names_from_response(result, state)

                        state.posts_with_details.add(post_id)
                        completed += 1

                    progress.update(task, advance=len(batch), description=f"Posts: {completed} done, {failed} failed")

                    if completed % 100 == 0:
                        state.save(checkpoint_path)

                    await asyncio.sleep(BATCH_DELAY)

            state.save(checkpoint_path)
            console.print(f"[green]Posts complete: {completed} downloaded, {failed} failed[/green]")

        # ============================================================
        # PHASE 2: Fetch all submolt details
        # ============================================================
        # First get submolt list to discover all names
        submolt_list = await fetch_submolt_list(client)
        for s in submolt_list:
            name = s.get("name") or s.get("slug")
            if name:
                state.submolt_names_discovered.add(name)

        submolts_to_fetch = [n for n in state.submolt_names_discovered if n not in state.submolts_fetched]

        if submolts_to_fetch:
            console.print(f"[bold blue]Fetching {len(submolts_to_fetch)} submolt details...[/bold blue]")

            completed = 0
            failed = 0

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Downloading submolts...", total=len(submolts_to_fetch))

                batch_size = MAX_CONCURRENT
                for i in range(0, len(submolts_to_fetch), batch_size):
                    batch = submolts_to_fetch[i:i + batch_size]

                    tasks = [fetch_submolt_details(client, name) for name in batch]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for name, result in zip(batch, results):
                        if isinstance(result, Exception):
                            failed += 1
                            continue

                        if result is None:
                            failed += 1
                            continue

                        # Save RAW response with sanitized filename
                        safe_name = sanitize_filename(name)
                        filepath = submolts_dir / f"{safe_name}.json"
                        filepath.write_text(json.dumps(result, indent=2, default=str))

                        extract_names_from_response(result, state)
                        state.submolts_fetched.add(name)
                        completed += 1

                    progress.update(task, advance=len(batch), description=f"Submolts: {completed} done, {failed} failed")
                    await asyncio.sleep(BATCH_DELAY)

            state.save(checkpoint_path)
            console.print(f"[green]Submolts complete: {completed} downloaded, {failed} failed[/green]")

        # ============================================================
        # PHASE 3: Fetch all agent profiles
        # ============================================================
        agents_to_fetch = [n for n in state.agent_names_discovered if n not in state.agents_fetched]

        if agents_to_fetch:
            console.print(f"[bold blue]Fetching {len(agents_to_fetch)} agent profiles...[/bold blue]")

            completed = 0
            failed = 0

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Downloading agents...", total=len(agents_to_fetch))

                batch_size = MAX_CONCURRENT
                for i in range(0, len(agents_to_fetch), batch_size):
                    batch = agents_to_fetch[i:i + batch_size]

                    tasks = [fetch_agent_profile(client, name) for name in batch]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for name, result in zip(batch, results):
                        if isinstance(result, Exception):
                            failed += 1
                            continue

                        if result is None:
                            failed += 1
                            continue

                        # Save RAW response with sanitized filename
                        safe_name = sanitize_filename(name)
                        filepath = agents_dir / f"{safe_name}.json"
                        filepath.write_text(json.dumps(result, indent=2, default=str))

                        state.agents_fetched.add(name)
                        completed += 1

                    progress.update(task, advance=len(batch), description=f"Agents: {completed} done, {failed} failed")

                    if completed % 100 == 0:
                        state.save(checkpoint_path)

                    await asyncio.sleep(BATCH_DELAY)

            state.save(checkpoint_path)
            console.print(f"[green]Agents complete: {completed} downloaded, {failed} failed[/green]")

        # ============================================================
        # Final summary
        # ============================================================
        console.print("\n[bold green]Download complete![/bold green]")
        console.print(f"  Posts: {len(state.posts_with_details)}")
        console.print(f"  Submolts: {len(state.submolts_fetched)}")
        console.print(f"  Agents: {len(state.agents_fetched)}")
        console.print(f"  Total API requests: {client.request_count}")
        if client.not_found_count > 0:
            console.print(f"  Not found (404/405): {client.not_found_count}")

    finally:
        await client.close()


def download_all(resume: bool = True):
    """Sync wrapper for async download."""
    asyncio.run(download_all_async(DATA_DIR, resume=resume))


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Download all data from Moltbook for research analysis"
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh, don't resume from checkpoint"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory to store downloaded data"
    )

    args = parser.parse_args()

    console.print("[bold]Moltbook Data Downloader[/bold]")
    console.print(f"Data directory: {args.data_dir.absolute()}")
    console.print()

    asyncio.run(download_all_async(args.data_dir, resume=not args.no_resume))


if __name__ == "__main__":
    main()
