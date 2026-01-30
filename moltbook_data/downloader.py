#!/usr/bin/env python3
"""
Moltbook Data Downloader

Downloads all available data from Moltbook API:
- Posts with comments (sorted oldest to newest)
- Submolts (communities)
- Agent profiles extracted from posts/comments

Data is stored as JSON files with checkpointing for resume capability.
Uses async parallel fetching with rate limiting.
"""

import asyncio
import json
import time
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
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

console = Console()


@dataclass
class DownloadState:
    """Tracks download progress for checkpointing."""
    posts_fetched: set[str] = field(default_factory=set)
    posts_with_details: set[str] = field(default_factory=set)
    submolts_fetched: bool = False
    agents: dict[str, dict] = field(default_factory=dict)
    submolts: dict[str, dict] = field(default_factory=dict)
    last_checkpoint: str = ""

    def save(self, path: Path):
        """Save checkpoint to disk."""
        data = {
            "posts_fetched": list(self.posts_fetched),
            "posts_with_details": list(self.posts_with_details),
            "submolts_fetched": self.submolts_fetched,
            "agents": self.agents,
            "submolts": self.submolts,
            "last_checkpoint": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(data, indent=2, default=str))

    @classmethod
    def load(cls, path: Path) -> "DownloadState":
        """Load checkpoint from disk."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            state = cls()
            state.posts_fetched = set(data.get("posts_fetched", []))
            state.posts_with_details = set(data.get("posts_with_details", []))
            state.submolts_fetched = data.get("submolts_fetched", False)
            state.agents = data.get("agents", {})
            state.submolts = data.get("submolts", {})
            state.last_checkpoint = data.get("last_checkpoint", "")
            return state
        except (json.JSONDecodeError, KeyError) as e:
            console.print(f"[yellow]Warning: Could not load checkpoint: {e}[/yellow]")
            return cls()


class AsyncMoltbookClient:
    """Async HTTP client for Moltbook API with rate limiting."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=30.0,
            headers={"User-Agent": "MoltbookResearch/1.0"},
            limits=httpx.Limits(max_connections=MAX_CONCURRENT, max_keepalive_connections=MAX_CONCURRENT)
        )
        self.request_count = 0
        self._lock = asyncio.Lock()

    async def get(self, endpoint: str, params: dict | None = None) -> dict | None:
        """Make a GET request with error handling."""
        async with self._lock:
            self.request_count += 1

        try:
            response = await self.client.get(endpoint, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:  # Don't spam 404s
                console.print(f"[red]HTTP {e.response.status_code}: {endpoint}[/red]")
            return None
        except httpx.RequestError as e:
            console.print(f"[red]Request error: {e}[/red]")
            return None
        except json.JSONDecodeError:
            console.print(f"[red]Invalid JSON: {endpoint}[/red]")
            return None

    async def close(self):
        await self.client.aclose()


def extract_agent_from_author(author: dict | None, agents: dict, source_type: str, source_id: str):
    """Extract agent information from author data."""
    if not author or not isinstance(author, dict):
        return

    agent_name = author.get("name") or author.get("username")
    agent_id = author.get("id")

    if not agent_name:
        return

    if agent_name not in agents:
        agents[agent_name] = {
            "id": agent_id,
            "name": agent_name,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "karma": author.get("karma"),
            "follower_count": author.get("follower_count"),
            "following_count": author.get("following_count"),
            "post_ids": [],
            "comment_ids": [],
        }

    # Update with any new info
    if author.get("karma") is not None:
        agents[agent_name]["karma"] = author.get("karma")
    if author.get("follower_count") is not None:
        agents[agent_name]["follower_count"] = author.get("follower_count")

    # Track activity
    if source_type == "post" and source_id not in agents[agent_name]["post_ids"]:
        agents[agent_name]["post_ids"].append(source_id)
    elif source_type == "comment" and source_id not in agents[agent_name]["comment_ids"]:
        agents[agent_name]["comment_ids"].append(source_id)


def extract_agents_from_comments(comments: list[dict], agents: dict):
    """Recursively extract agents from comments and their replies."""
    for comment in comments:
        comment_id = comment.get("id")
        extract_agent_from_author(comment.get("author"), agents, "comment", comment_id)

        # Process nested replies
        replies = comment.get("replies", [])
        if replies:
            extract_agents_from_comments(replies, agents)


async def fetch_all_post_ids(client: AsyncMoltbookClient) -> list[dict]:
    """Fetch all posts from the feed to get their IDs and basic info."""
    all_posts = []
    offset = 0

    console.print("[bold blue]Fetching post list from feed...[/bold blue]")

    while True:
        # Use sort=new to get chronological order
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
        await asyncio.sleep(0.1)  # Small delay between pages

    console.print(f"\n[green]Found {len(all_posts)} total posts[/green]")

    # Reverse to get oldest first
    all_posts.reverse()
    return all_posts


async def fetch_post_with_comments(client: AsyncMoltbookClient, post_id: str) -> dict | None:
    """Fetch a single post with its comments."""
    data = await client.get(f"/posts/{post_id}")
    if not data or not data.get("success"):
        return None

    result = {
        "post": data.get("post", {}),
        "comments": data.get("comments", []),
        "_downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    return result


async def fetch_all_submolts(client: AsyncMoltbookClient) -> list[dict]:
    """Fetch all submolts (communities)."""
    all_submolts = []
    offset = 0

    console.print("[bold blue]Fetching submolts...[/bold blue]")

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

    console.print(f"[green]Fetched {len(all_submolts)} submolts[/green]")
    return all_submolts


async def download_all_async(data_dir: Path, resume: bool = True):
    """Main async download function."""

    # Setup directories
    posts_dir = data_dir / "posts"
    submolts_dir = data_dir / "submolts"
    posts_dir.mkdir(parents=True, exist_ok=True)
    submolts_dir.mkdir(parents=True, exist_ok=True)

    checkpoint_path = data_dir / "checkpoint.json"

    # Load checkpoint
    state = DownloadState.load(checkpoint_path) if resume else DownloadState()

    if resume and state.last_checkpoint:
        console.print(f"[cyan]Resuming from checkpoint: {state.last_checkpoint}[/cyan]")
        console.print(f"  Posts with details: {len(state.posts_with_details)}")
        console.print(f"  Agents discovered: {len(state.agents)}")

    client = AsyncMoltbookClient()

    try:
        # 1. Fetch submolts if not done
        if not state.submolts_fetched:
            submolts = await fetch_all_submolts(client)
            for submolt in submolts:
                name = submolt.get("name") or submolt.get("slug")
                if name:
                    submolt["_downloaded_at"] = datetime.now(timezone.utc).isoformat()
                    filepath = submolts_dir / f"{name}.json"
                    filepath.write_text(json.dumps(submolt, indent=2, default=str))
                    state.submolts[name] = {
                        "name": name,
                        "description": submolt.get("description", ""),
                        "subscriber_count": submolt.get("subscriber_count", 0),
                    }
            state.submolts_fetched = True
            state.save(checkpoint_path)

        # 2. Get all post IDs from feed
        all_posts = await fetch_all_post_ids(client)

        # 3. Filter to posts we haven't fetched details for
        posts_to_fetch = [p for p in all_posts if p.get("id") not in state.posts_with_details]

        console.print(f"[bold blue]Fetching {len(posts_to_fetch)} posts with comments (parallel)...[/bold blue]")

        # 4. Fetch posts in parallel batches
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

            # Process in batches
            batch_size = MAX_CONCURRENT
            for i in range(0, len(posts_to_fetch), batch_size):
                batch = posts_to_fetch[i:i + batch_size]

                # Create tasks for parallel fetching
                tasks = [fetch_post_with_comments(client, p["id"]) for p in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results
                for post_info, result in zip(batch, results):
                    post_id = post_info["id"]

                    if isinstance(result, Exception):
                        console.print(f"[red]Error fetching {post_id}: {result}[/red]")
                        failed += 1
                        continue

                    if result is None:
                        failed += 1
                        continue

                    # Save post with comments
                    filepath = posts_dir / f"{post_id}.json"
                    filepath.write_text(json.dumps(result, indent=2, default=str))

                    # Extract agent info
                    post_data = result.get("post", {})
                    extract_agent_from_author(post_data.get("author"), state.agents, "post", post_id)

                    comments = result.get("comments", [])
                    extract_agents_from_comments(comments, state.agents)

                    state.posts_with_details.add(post_id)
                    completed += 1

                progress.update(task, advance=len(batch), description=f"Posts: {completed} done, {failed} failed")

                # Checkpoint every 100 posts
                if completed % 100 == 0:
                    state.save(checkpoint_path)

                # Rate limit delay between batches
                await asyncio.sleep(BATCH_DELAY)

        # Final checkpoint
        state.save(checkpoint_path)

        # Save agent index
        agents_path = data_dir / "agents_index.json"
        agents_path.write_text(json.dumps(state.agents, indent=2, default=str))

        # Save submolts index
        submolts_path = data_dir / "submolts_index.json"
        submolts_path.write_text(json.dumps(state.submolts, indent=2, default=str))

        # Final stats
        console.print("\n[bold green]Download complete![/bold green]")
        console.print(f"  Posts with comments: {len(state.posts_with_details)}")
        console.print(f"  Failed: {failed}")
        console.print(f"  Unique agents found: {len(state.agents)}")
        console.print(f"  Submolts: {len(state.submolts)}")
        console.print(f"  Total API requests: {client.request_count}")

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
