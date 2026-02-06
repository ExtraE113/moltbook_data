"""
Moltbook-specific scraping logic.

Contains fetch functions, name extraction, and the download_moltbook() orchestrator
that uses core infrastructure (BatchJob, run_batch, paginate).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn

from .core import (
    AsyncHttpClient,
    BatchJob,
    DownloadState,
    ScraperConfig,
    atomic_write,
    paginate,
    run_batch,
    sanitize_filename,
    stamp_metadata,
)

logger = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Name extraction
# ---------------------------------------------------------------------------


def extract_names_from_response(data: dict, state: DownloadState) -> None:
    """Extract agent and submolt names from any API response for later fetching."""

    def extract_from_author(author):
        if author and isinstance(author, dict):
            name = author.get("name") or author.get("username")
            if name:
                state.add_discovered("agent_names", name)

    def extract_from_submolt(submolt):
        if submolt and isinstance(submolt, dict):
            name = submolt.get("name") or submolt.get("slug")
            if name:
                state.add_discovered("submolt_names", name)

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
            state.add_discovered("agent_names", name)

    # Extract from submolt
    submolt = data.get("submolt", {})
    extract_from_submolt(submolt)


# ---------------------------------------------------------------------------
# Fetch functions
# ---------------------------------------------------------------------------


async def fetch_post_with_comments(client: AsyncHttpClient, post_id: str) -> dict | None:
    """Fetch a single post with its comments -- returns RAW API response."""
    data = await client.get(f"/posts/{post_id}")
    if not data or not data.get("success"):
        return None
    return stamp_metadata(data, f"/posts/{post_id}")


async def fetch_submolt_details(client: AsyncHttpClient, name: str) -> dict | None:
    """Fetch full submolt details -- returns RAW API response."""
    data = await client.get(f"/submolts/{name}")
    if not data or not data.get("success"):
        return None
    return stamp_metadata(data, f"/submolts/{name}")


async def fetch_agent_profile(client: AsyncHttpClient, name: str) -> dict | None:
    """Fetch full agent profile -- returns RAW API response."""
    data = await client.get("/agents/profile", params={"name": name})
    if not data or not data.get("success"):
        return None
    return stamp_metadata(data, f"/agents/profile?name={name}")


# ---------------------------------------------------------------------------
# Post change detection + archiving (carried over from downloader.py)
# ---------------------------------------------------------------------------


def archive_post_if_changed(posts_dir: Path, post_id: str, new_data: dict) -> bool:
    """Archive existing post if content has changed. Returns True if archived."""
    current_file = posts_dir / f"{post_id}.json"
    if not current_file.exists():
        return False

    try:
        existing_data = json.loads(current_file.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    old_count = len(existing_data.get("comments", []))
    new_count = len(new_data.get("comments", []))
    old_post = existing_data.get("post", {})
    new_post = new_data.get("post", {})

    content_changed = (
        old_count != new_count
        or old_post.get("content") != new_post.get("content")
        or old_post.get("title") != new_post.get("title")
    )
    if not content_changed:
        return False

    archive_dir = posts_dir / "archive" / post_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    old_timestamp = existing_data.get("_downloaded_at", "unknown")
    safe_timestamp = old_timestamp.replace(":", "-").replace("+", "_").replace(".", "-")
    archive_file = archive_dir / f"{safe_timestamp}.json"
    archive_file.write_text(json.dumps(existing_data, indent=2, default=str))
    return True


def identify_posts_needing_refresh(
    all_posts: list[dict], state: DownloadState
) -> tuple[list[dict], list[dict]]:
    """Categorize posts into new posts and posts needing refresh."""
    done_posts = state.get_done("posts")
    comment_counts = state.extra.get("post_comment_counts", {})

    new_posts = []
    posts_to_refresh = []

    for post in all_posts:
        post_id = post.get("id")
        if not post_id:
            continue

        api_comment_count = post.get("comment_count", 0)

        if post_id not in done_posts:
            new_posts.append(post)
        elif post_id not in comment_counts:
            posts_to_refresh.append(post)
        elif api_comment_count > comment_counts[post_id]:
            posts_to_refresh.append(post)

    return new_posts, posts_to_refresh


def migrate_comment_counts(data_dir: Path, state: DownloadState) -> int:
    """Backfill post_comment_counts from existing post files."""
    posts_dir = data_dir / "posts"
    comment_counts = state.extra.setdefault("post_comment_counts", {})
    migrated = 0

    for post_file in posts_dir.glob("*.json"):
        post_id = post_file.stem
        if post_id in comment_counts:
            continue
        try:
            data = json.loads(post_file.read_text())
            comment_count = data.get("post", {}).get("comment_count", 0)
            comment_counts[post_id] = comment_count
            migrated += 1
        except (json.JSONDecodeError, OSError):
            continue

    return migrated


# ---------------------------------------------------------------------------
# Index generation
# ---------------------------------------------------------------------------


def generate_submolts_index(submolts_dir: Path) -> None:
    """Generate an index.json file listing all submolts with basic metadata."""
    index_entries = []

    for filepath in sorted(submolts_dir.glob("*.json")):
        if filepath.name == "index.json":
            continue
        try:
            data = json.loads(filepath.read_text())
            submolt = data.get("submolt", {})
            index_entries.append({
                "name": submolt.get("name", filepath.stem),
                "display_name": submolt.get("display_name"),
                "subscriber_count": submolt.get("subscriber_count", 0),
                "file": filepath.name,
            })
        except (json.JSONDecodeError, OSError):
            index_entries.append({"name": filepath.stem, "file": filepath.name})

    index_path = submolts_dir / "index.json"
    index_data = {
        "count": len(index_entries),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "submolts": index_entries,
    }
    index_path.write_text(json.dumps(index_data, indent=2))
    console.print(f"[green]Generated submolts index with {len(index_entries)} entries[/green]")


def generate_agents_index(agents_dir: Path) -> None:
    """Generate an index.json file listing all agents with basic metadata."""
    index_entries = []

    for filepath in sorted(agents_dir.glob("*.json")):
        if filepath.name == "index.json":
            continue
        try:
            data = json.loads(filepath.read_text())
            agent = data.get("agent", {})
            index_entries.append({
                "name": agent.get("name", filepath.stem),
                "karma": agent.get("karma", 0),
                "follower_count": agent.get("follower_count", 0),
                "file": filepath.name,
            })
        except (json.JSONDecodeError, OSError):
            index_entries.append({"name": filepath.stem, "file": filepath.name})

    index_path = agents_dir / "index.json"
    index_data = {
        "count": len(index_entries),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agents": index_entries,
    }
    index_path.write_text(json.dumps(index_data, indent=2))
    console.print(f"[green]Generated agents index with {len(index_entries)} entries[/green]")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def download_moltbook(data_dir: Path, resume: bool = True, config: ScraperConfig | None = None) -> None:
    """Main async download function for Moltbook."""
    config = config or ScraperConfig()

    # Setup directories
    posts_dir = data_dir / "posts"
    submolts_dir = data_dir / "submolts"
    agents_dir = data_dir / "agents"
    archive_dir = posts_dir / "archive"

    for d in [posts_dir, submolts_dir, agents_dir, archive_dir]:
        d.mkdir(parents=True, exist_ok=True)

    checkpoint_path = data_dir / "checkpoint.json"

    # Load checkpoint
    state = DownloadState.load(checkpoint_path) if resume else DownloadState()

    # Ensure extra has post_comment_counts
    state.extra.setdefault("post_comment_counts", {})

    # Migrate existing data if needed (backfill comment counts)
    if state.get_done("posts") and not state.extra.get("post_comment_counts"):
        console.print("[cyan]Migrating existing posts to comment tracking...[/cyan]")
        migrated = migrate_comment_counts(data_dir, state)
        console.print(f"[green]Migrated {migrated} posts[/green]")
        state.save(checkpoint_path)

    if resume and state.last_checkpoint:
        console.print(f"[cyan]Resuming from checkpoint: {state.last_checkpoint}[/cyan]")
        console.print(f"  Posts: {len(state.get_done('posts'))}")
        console.print(f"  Posts with tracked counts: {len(state.extra.get('post_comment_counts', {}))}")
        console.print(f"  Submolts: {len(state.get_done('submolts'))}")
        console.print(f"  Agents: {len(state.get_done('agents'))}")

    client = AsyncHttpClient(config)

    try:
        # ============================================================
        # PHASE 1: Get all post IDs and fetch posts with comments
        # ============================================================
        console.print("[bold blue]Fetching post list from feed...[/bold blue]")
        all_posts = await paginate(
            client, "/posts", items_key="posts",
            params={"sort": "new"},
        )
        console.print(f"\n[green]Found {len(all_posts)} total posts[/green]")

        # Extract agent/submolt names from feed data
        for post in all_posts:
            extract_names_from_response({"post": post}, state)

        # Reverse to get oldest first
        all_posts.reverse()

        # Categorize posts: new vs needing refresh
        new_posts, posts_to_refresh = identify_posts_needing_refresh(all_posts, state)
        all_posts_to_fetch = new_posts + posts_to_refresh
        refresh_ids = {p["id"] for p in posts_to_refresh}

        if new_posts:
            console.print(f"[blue]New posts to download: {len(new_posts)}[/blue]")
        if posts_to_refresh:
            console.print(f"[blue]Posts to refresh (new comments): {len(posts_to_refresh)}[/blue]")

        if all_posts_to_fetch:
            console.print(f"[bold blue]Fetching {len(all_posts_to_fetch)} posts with comments...[/bold blue]")

            completed = 0
            failed = 0
            archived = 0

            def on_post_result(post_id: str, data: dict) -> None:
                nonlocal archived
                # Archive if refresh and content changed
                if post_id in refresh_ids:
                    if archive_post_if_changed(posts_dir, post_id, data):
                        archived += 1
                # Extract names
                extract_names_from_response(data, state)
                # Track comment count
                comment_count = data.get("post", {}).get("comment_count", 0)
                state.extra["post_comment_counts"][post_id] = comment_count

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Downloading posts...", total=len(all_posts_to_fetch))

                batch_size = config.max_concurrent
                for i in range(0, len(all_posts_to_fetch), batch_size):
                    batch = all_posts_to_fetch[i:i + batch_size]

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

                        # Archive if this is a refresh and content changed
                        if post_id in refresh_ids:
                            if archive_post_if_changed(posts_dir, post_id, result):
                                archived += 1

                        # Save RAW response
                        filepath = posts_dir / f"{post_id}.json"
                        filepath.write_text(json.dumps(result, indent=2, default=str))

                        # Extract names
                        extract_names_from_response(result, state)

                        # Update tracking
                        state.mark_done("posts", post_id)
                        comment_count = result.get("post", {}).get("comment_count", 0)
                        state.extra["post_comment_counts"][post_id] = comment_count
                        completed += 1

                    progress.update(task, advance=len(batch), description=f"Posts: {completed} done, {archived} archived, {failed} failed")

                    if completed % 100 == 0:
                        state.save(checkpoint_path)

                    await asyncio.sleep(config.batch_delay)

            state.save(checkpoint_path)
            console.print(f"[green]Posts complete: {completed} downloaded, {archived} archived, {failed} failed[/green]")

        # ============================================================
        # PHASE 2: Fetch all submolt details
        # ============================================================
        console.print("[bold blue]Fetching submolt list...[/bold blue]")
        submolt_list = await paginate(
            client, "/submolts", items_key="submolts",
        )
        console.print(f"[green]Found {len(submolt_list)} submolts[/green]")

        for s in submolt_list:
            name = s.get("name") or s.get("slug")
            if name:
                state.add_discovered("submolt_names", name)

        submolts_to_fetch = [
            n for n in state.get_discovered("submolt_names")
            if n not in state.get_done("submolts")
        ]

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

                batch_size = config.max_concurrent
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

                        safe_name = sanitize_filename(name)
                        filepath = submolts_dir / f"{safe_name}.json"
                        filepath.write_text(json.dumps(result, indent=2, default=str))

                        extract_names_from_response(result, state)
                        state.mark_done("submolts", name)
                        completed += 1

                    progress.update(task, advance=len(batch), description=f"Submolts: {completed} done, {failed} failed")
                    await asyncio.sleep(config.batch_delay)

            state.save(checkpoint_path)
            console.print(f"[green]Submolts complete: {completed} downloaded, {failed} failed[/green]")

        generate_submolts_index(submolts_dir)

        # ============================================================
        # PHASE 3: Fetch all agent profiles
        # ============================================================
        agents_to_fetch = [
            n for n in state.get_discovered("agent_names")
            if n not in state.get_done("agents")
        ]

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

                batch_size = config.max_concurrent
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

                        safe_name = sanitize_filename(name)
                        filepath = agents_dir / f"{safe_name}.json"
                        filepath.write_text(json.dumps(result, indent=2, default=str))

                        state.mark_done("agents", name)
                        completed += 1

                    progress.update(task, advance=len(batch), description=f"Agents: {completed} done, {failed} failed")

                    if completed % 100 == 0:
                        state.save(checkpoint_path)

                    await asyncio.sleep(config.batch_delay)

            state.save(checkpoint_path)
            console.print(f"[green]Agents complete: {completed} downloaded, {failed} failed[/green]")

        generate_agents_index(agents_dir)

        # ============================================================
        # Final summary
        # ============================================================
        console.print("\n[bold green]Download complete![/bold green]")
        console.print(f"  Posts: {len(state.get_done('posts'))}")
        console.print(f"  Submolts: {len(state.get_done('submolts'))}")
        console.print(f"  Agents: {len(state.get_done('agents'))}")
        console.print(f"  Total API requests: {client.request_count}")
        if client.not_found_count > 0:
            console.print(f"  Not found (404/405): {client.not_found_count}")

    finally:
        await client.close()


# Need asyncio import for gather
import asyncio
