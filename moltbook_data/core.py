"""
Core scraping infrastructure: config, HTTP client, checkpoint, batch processing, utilities.

This module is site-agnostic. Moltbook-specific logic lives in moltbook.py.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename across all platforms."""
    return re.sub(r'[<>:"/\\|?*]', '_', name)


def atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically using tmp+rename."""
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(content)
    tmp_path.rename(path)


def stamp_metadata(data: dict, endpoint: str) -> dict:
    """Add _downloaded_at and _endpoint metadata to a response dict. Mutates in place."""
    data["_downloaded_at"] = datetime.now(timezone.utc).isoformat()
    data["_endpoint"] = endpoint
    return data


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class ScraperConfig:
    """Configuration for the scraper."""
    base_url: str = "https://www.moltbook.com/api/v1"
    max_concurrent: int = 10
    batch_delay: float = 0.5
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    timeout: float = 30.0
    user_agent: str = "MoltbookResearch/1.0"


# ---------------------------------------------------------------------------
# Checkpoint / Download State
# ---------------------------------------------------------------------------


@dataclass
class DownloadState:
    """
    Generic checkpoint state for any scraper.

    Uses `phases` to track completed items per phase (e.g. "posts", "submolts", "agents")
    and `discovered` to track discovered names (e.g. "agent_names", "submolt_names").
    """
    phases: dict[str, set[str]] = field(default_factory=dict)
    discovered: dict[str, set[str]] = field(default_factory=dict)
    last_checkpoint: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def mark_done(self, phase: str, item_id: str) -> None:
        self.phases.setdefault(phase, set()).add(item_id)

    def get_done(self, phase: str) -> set[str]:
        return self.phases.get(phase, set())

    def add_discovered(self, category: str, name: str) -> None:
        self.discovered.setdefault(category, set()).add(name)

    def get_discovered(self, category: str) -> set[str]:
        return self.discovered.get(category, set())

    def save(self, path: Path) -> None:
        """Save checkpoint to disk atomically."""
        data = {
            "phases": {k: list(v) for k, v in self.phases.items()},
            "discovered": {k: list(v) for k, v in self.discovered.items()},
            "last_checkpoint": datetime.now(timezone.utc).isoformat(),
            "extra": self.extra,
        }
        atomic_write(path, json.dumps(data, indent=2, default=str))

    @classmethod
    def load(cls, path: Path) -> "DownloadState":
        """Load checkpoint from disk. Auto-detects and converts old flat format."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not load checkpoint: %s", e)
            return cls()

        # Detect new generic format
        if "phases" in data:
            state = cls()
            state.phases = {k: set(v) for k, v in data.get("phases", {}).items()}
            state.discovered = {k: set(v) for k, v in data.get("discovered", {}).items()}
            state.last_checkpoint = data.get("last_checkpoint", "")
            state.extra = data.get("extra", {})
            return state

        # Old flat format (backward compatibility)
        state = cls()
        state.phases["posts"] = set(data.get("posts_with_details", []))
        state.phases["submolts"] = set(data.get("submolts_fetched", []))
        state.phases["agents"] = set(data.get("agents_fetched", []))
        state.discovered["agent_names"] = set(data.get("agent_names_discovered", []))
        state.discovered["submolt_names"] = set(data.get("submolt_names_discovered", []))
        state.last_checkpoint = data.get("last_checkpoint", "")
        state.extra["post_comment_counts"] = data.get("post_comment_counts", {})
        return state


# ---------------------------------------------------------------------------
# HTTP Client
# ---------------------------------------------------------------------------


class AsyncHttpClient:
    """Async HTTP client with retry logic for transient failures."""

    def __init__(self, config: ScraperConfig):
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout,
            headers={"User-Agent": config.user_agent},
            limits=httpx.Limits(
                max_connections=config.max_concurrent,
                max_keepalive_connections=config.max_concurrent,
            ),
        )
        self.request_count = 0
        self.not_found_count = 0

    async def get(self, endpoint: str, params: dict | None = None) -> dict | None:
        """Make a GET request with retry logic for transient failures."""
        last_error = None

        for attempt in range(self.config.max_retries + 1):
            self.request_count += 1

            try:
                response = await self.client.get(endpoint, params=params)
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                status = e.response.status_code

                if status in (404, 405):
                    self.not_found_count += 1
                    return None

                if status >= 500 and attempt < self.config.max_retries:
                    delay = self.config.retry_backoff_base ** attempt
                    logger.warning("HTTP %d on %s, retrying in %.1fs...", status, endpoint, delay)
                    await asyncio.sleep(delay)
                    last_error = e
                    continue

                logger.error("HTTP %d: %s", status, endpoint)
                return None

            except httpx.RequestError as e:
                if attempt < self.config.max_retries:
                    delay = self.config.retry_backoff_base ** attempt
                    logger.warning("Network error on %s, retrying in %.1fs...", endpoint, delay)
                    await asyncio.sleep(delay)
                    last_error = e
                    continue

                logger.error("Request error after %d retries: %s", self.config.max_retries, e)
                return None

            except json.JSONDecodeError:
                logger.error("Invalid JSON: %s", endpoint)
                return None

        if last_error:
            logger.error("Failed after %d retries: %s", self.config.max_retries, endpoint)
        return None

    async def close(self):
        await self.client.aclose()


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


async def paginate(
    client: AsyncHttpClient,
    endpoint: str,
    items_key: str,
    params: dict | None = None,
    limit: int = 100,
    page_delay: float = 0.1,
) -> list[dict]:
    """
    Paginate through an API endpoint that uses has_more/next_offset.

    Returns all collected items.
    """
    all_items: list[dict] = []
    offset = 0
    base_params = dict(params or {})

    while True:
        page_params = {**base_params, "offset": offset, "limit": limit}
        data = await client.get(endpoint, params=page_params)

        if not data or not data.get("success"):
            break

        items = data.get(items_key, [])
        if not items:
            break

        all_items.extend(items)

        if not data.get("has_more", False):
            break

        offset = data.get("next_offset", offset + len(items))
        await asyncio.sleep(page_delay)

    return all_items


# ---------------------------------------------------------------------------
# Batch Processing
# ---------------------------------------------------------------------------


@dataclass
class BatchJob:
    """Configuration for a batch fetch-and-save operation."""
    items: list[str]
    fetch_fn: Callable[["AsyncHttpClient", str], Awaitable[dict | None]]
    save_dir: Path
    filename_fn: Callable[[str], str]
    label: str
    state_phase: str
    on_result: Callable[[str, dict], None] | None = None
    checkpoint_interval: int = 100


async def run_batch(
    client: AsyncHttpClient,
    job: BatchJob,
    state: DownloadState,
    checkpoint_path: Path | None,
    batch_size: int | None = None,
    batch_delay: float | None = None,
) -> tuple[int, int]:
    """
    Run a batch of fetch-and-save operations.

    For each item, calls job.fetch_fn, writes the result to job.save_dir,
    marks it done in state, and optionally calls job.on_result.

    Returns (completed, failed) counts.
    """
    if batch_size is None:
        batch_size = client.config.max_concurrent
    if batch_delay is None:
        batch_delay = client.config.batch_delay

    completed = 0
    failed = 0

    for i in range(0, len(job.items), batch_size):
        batch = job.items[i:i + batch_size]

        tasks = [job.fetch_fn(client, item_id) for item_id in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for item_id, result in zip(batch, results):
            if isinstance(result, Exception):
                logger.error("Error fetching %s: %s", item_id, result)
                failed += 1
                continue

            if result is None:
                failed += 1
                continue

            # Save atomically
            filepath = job.save_dir / job.filename_fn(item_id)
            atomic_write(filepath, json.dumps(result, indent=2, default=str))

            # Update state
            state.mark_done(job.state_phase, item_id)

            # Optional callback
            if job.on_result:
                job.on_result(item_id, result)

            completed += 1

        # Periodic checkpoint
        if checkpoint_path and completed % job.checkpoint_interval < batch_size:
            state.save(checkpoint_path)

        if batch_delay > 0:
            await asyncio.sleep(batch_delay)

    # Final checkpoint
    if checkpoint_path:
        state.save(checkpoint_path)

    return completed, failed
