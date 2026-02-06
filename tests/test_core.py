"""Unit tests for core utilities, checkpoint, HTTP client, batch processing, and pagination."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

# ---------------------------------------------------------------------------
# Phase 1: Utility tests
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    def test_normal_name(self):
        from moltbook_data.core import sanitize_filename
        assert sanitize_filename("hello_world") == "hello_world"

    def test_special_characters(self):
        from moltbook_data.core import sanitize_filename
        assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"

    def test_preserves_dots_and_dashes(self):
        from moltbook_data.core import sanitize_filename
        assert sanitize_filename("file-name.txt") == "file-name.txt"

    def test_empty_string(self):
        from moltbook_data.core import sanitize_filename
        assert sanitize_filename("") == ""

    def test_only_special_chars(self):
        from moltbook_data.core import sanitize_filename
        assert sanitize_filename('<>:"/\\|?*') == "_________"


class TestAtomicWrite:
    def test_writes_correct_content(self, tmp_path):
        from moltbook_data.core import atomic_write
        target = tmp_path / "test.json"
        atomic_write(target, '{"key": "value"}')
        assert target.read_text() == '{"key": "value"}'

    def test_no_tmp_files_left(self, tmp_path):
        from moltbook_data.core import atomic_write
        target = tmp_path / "test.json"
        atomic_write(target, "content")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_overwrites_existing(self, tmp_path):
        from moltbook_data.core import atomic_write
        target = tmp_path / "test.json"
        target.write_text("old")
        atomic_write(target, "new")
        assert target.read_text() == "new"


class TestStampMetadata:
    def test_adds_downloaded_at_and_endpoint(self):
        from moltbook_data.core import stamp_metadata
        data = {"key": "value"}
        result = stamp_metadata(data, "/posts/123")
        assert "_downloaded_at" in result
        assert result["_endpoint"] == "/posts/123"
        assert result["key"] == "value"

    def test_preserves_existing_fields(self):
        from moltbook_data.core import stamp_metadata
        data = {"a": 1, "b": 2, "success": True}
        result = stamp_metadata(data, "/test")
        assert result["a"] == 1
        assert result["b"] == 2
        assert result["success"] is True

    def test_mutates_in_place(self):
        from moltbook_data.core import stamp_metadata
        data = {"key": "value"}
        result = stamp_metadata(data, "/test")
        assert result is data


# ---------------------------------------------------------------------------
# Phase 2: Checkpoint / DownloadState tests
# ---------------------------------------------------------------------------


class TestDownloadState:
    def test_save_load_roundtrip(self, tmp_path):
        from moltbook_data.core import DownloadState
        path = tmp_path / "checkpoint.json"

        state = DownloadState()
        state.mark_done("posts", "post-001")
        state.mark_done("posts", "post-002")
        state.mark_done("submolts", "general")
        state.add_discovered("agent_names", "alice")
        state.add_discovered("agent_names", "bob")
        state.save(path)

        loaded = DownloadState.load(path)
        assert loaded.get_done("posts") == {"post-001", "post-002"}
        assert loaded.get_done("submolts") == {"general"}
        assert loaded.get_discovered("agent_names") == {"alice", "bob"}

    def test_backward_compat_old_format(self, tmp_path):
        """Loads old flat checkpoint format and converts to new generic format."""
        from moltbook_data.core import DownloadState
        path = tmp_path / "checkpoint.json"

        old_data = {
            "posts_with_details": ["post-001", "post-002"],
            "submolts_fetched": ["general"],
            "agents_fetched": ["alice"],
            "agent_names_discovered": ["alice", "bob"],
            "submolt_names_discovered": ["general", "tech"],
            "last_checkpoint": "2025-01-01T00:00:00+00:00",
            "post_comment_counts": {"post-001": 5, "post-002": 3},
        }
        path.write_text(json.dumps(old_data))

        loaded = DownloadState.load(path)
        assert loaded.get_done("posts") == {"post-001", "post-002"}
        assert loaded.get_done("submolts") == {"general"}
        assert loaded.get_done("agents") == {"alice"}
        assert loaded.get_discovered("agent_names") == {"alice", "bob"}
        assert loaded.get_discovered("submolt_names") == {"general", "tech"}
        assert loaded.last_checkpoint == "2025-01-01T00:00:00+00:00"
        assert loaded.extra["post_comment_counts"] == {"post-001": 5, "post-002": 3}

    def test_corrupted_checkpoint_falls_back(self, tmp_path):
        from moltbook_data.core import DownloadState
        path = tmp_path / "checkpoint.json"
        path.write_text("not valid json {{{")
        loaded = DownloadState.load(path)
        assert loaded.get_done("posts") == set()

    def test_missing_checkpoint_returns_fresh(self, tmp_path):
        from moltbook_data.core import DownloadState
        path = tmp_path / "nonexistent.json"
        loaded = DownloadState.load(path)
        assert loaded.get_done("posts") == set()

    def test_mark_done_and_get_done(self):
        from moltbook_data.core import DownloadState
        state = DownloadState()
        state.mark_done("agents", "alice")
        state.mark_done("agents", "bob")
        assert state.get_done("agents") == {"alice", "bob"}
        assert state.get_done("posts") == set()

    def test_add_discovered_and_get_discovered(self):
        from moltbook_data.core import DownloadState
        state = DownloadState()
        state.add_discovered("submolt_names", "general")
        assert state.get_discovered("submolt_names") == {"general"}
        assert state.get_discovered("agent_names") == set()

    def test_atomic_save(self, tmp_path):
        """Verify save uses atomic write (no .tmp files left)."""
        from moltbook_data.core import DownloadState
        path = tmp_path / "checkpoint.json"
        state = DownloadState()
        state.mark_done("posts", "p1")
        state.save(path)
        assert path.exists()
        assert len(list(tmp_path.glob("*.tmp"))) == 0


# ---------------------------------------------------------------------------
# Phase 3: AsyncHttpClient tests
# ---------------------------------------------------------------------------

BASE_URL = "https://www.moltbook.com/api/v1"


class TestAsyncHttpClient:
    @pytest.mark.asyncio
    async def test_successful_request(self):
        from moltbook_data.core import AsyncHttpClient, ScraperConfig
        config = ScraperConfig(base_url=BASE_URL)

        with respx.mock(base_url=BASE_URL) as router:
            router.get("/test").mock(return_value=httpx.Response(200, json={"ok": True}))
            client = AsyncHttpClient(config)
            try:
                result = await client.get("/test")
                assert result == {"ok": True}
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_404_returns_none_immediately(self):
        from moltbook_data.core import AsyncHttpClient, ScraperConfig
        config = ScraperConfig(base_url=BASE_URL)

        with respx.mock(base_url=BASE_URL) as router:
            route = router.get("/missing").mock(return_value=httpx.Response(404))
            client = AsyncHttpClient(config)
            try:
                result = await client.get("/missing")
                assert result is None
                # Only called once (no retry)
                assert route.call_count == 1
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_500_retries_and_succeeds(self):
        from moltbook_data.core import AsyncHttpClient, ScraperConfig
        config = ScraperConfig(base_url=BASE_URL, max_retries=2, retry_backoff_base=0.01)

        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(500)
            return httpx.Response(200, json={"ok": True})

        with respx.mock(base_url=BASE_URL) as router:
            router.get("/flaky").mock(side_effect=handler)
            client = AsyncHttpClient(config)
            try:
                result = await client.get("/flaky")
                assert result == {"ok": True}
                assert call_count == 2
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_500_retries_exhausted(self):
        from moltbook_data.core import AsyncHttpClient, ScraperConfig
        config = ScraperConfig(base_url=BASE_URL, max_retries=2, retry_backoff_base=0.01)

        with respx.mock(base_url=BASE_URL) as router:
            route = router.get("/down").mock(return_value=httpx.Response(500))
            client = AsyncHttpClient(config)
            try:
                result = await client.get("/down")
                assert result is None
                # Initial + 2 retries = 3 calls
                assert route.call_count == 3
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_network_error_retries(self):
        from moltbook_data.core import AsyncHttpClient, ScraperConfig
        config = ScraperConfig(base_url=BASE_URL, max_retries=2, retry_backoff_base=0.01)

        with respx.mock(base_url=BASE_URL) as router:
            route = router.get("/err").mock(side_effect=httpx.ConnectError("fail"))
            client = AsyncHttpClient(config)
            try:
                result = await client.get("/err")
                assert result is None
                assert route.call_count == 3
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        from moltbook_data.core import AsyncHttpClient, ScraperConfig
        config = ScraperConfig(base_url=BASE_URL)

        with respx.mock(base_url=BASE_URL) as router:
            router.get("/bad").mock(return_value=httpx.Response(200, content=b"not json"))
            client = AsyncHttpClient(config)
            try:
                result = await client.get("/bad")
                assert result is None
            finally:
                await client.close()


# ---------------------------------------------------------------------------
# Phase 4: Batch processing + pagination tests
# ---------------------------------------------------------------------------


class TestPaginate:
    @pytest.mark.asyncio
    async def test_follows_has_more(self):
        from moltbook_data.core import AsyncHttpClient, ScraperConfig, paginate
        config = ScraperConfig(base_url=BASE_URL)

        def handler(request):
            offset = int(request.url.params.get("offset", "0"))
            if offset == 0:
                return httpx.Response(200, json={
                    "success": True,
                    "items": [{"id": "a"}, {"id": "b"}],
                    "has_more": True,
                    "next_offset": 2,
                })
            else:
                return httpx.Response(200, json={
                    "success": True,
                    "items": [{"id": "c"}],
                    "has_more": False,
                })

        with respx.mock(base_url=BASE_URL) as router:
            router.get("/things").mock(side_effect=handler)
            client = AsyncHttpClient(config)
            try:
                results = await paginate(client, "/things", items_key="items")
                assert len(results) == 3
                assert [r["id"] for r in results] == ["a", "b", "c"]
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_stops_when_no_more_pages(self):
        from moltbook_data.core import AsyncHttpClient, ScraperConfig, paginate
        config = ScraperConfig(base_url=BASE_URL)

        with respx.mock(base_url=BASE_URL) as router:
            router.get("/single").mock(return_value=httpx.Response(200, json={
                "success": True,
                "items": [{"id": "x"}],
                "has_more": False,
            }))
            client = AsyncHttpClient(config)
            try:
                results = await paginate(client, "/single", items_key="items")
                assert len(results) == 1
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_empty_first_page(self):
        from moltbook_data.core import AsyncHttpClient, ScraperConfig, paginate
        config = ScraperConfig(base_url=BASE_URL)

        with respx.mock(base_url=BASE_URL) as router:
            router.get("/empty").mock(return_value=httpx.Response(200, json={
                "success": True,
                "items": [],
                "has_more": False,
            }))
            client = AsyncHttpClient(config)
            try:
                results = await paginate(client, "/empty", items_key="items")
                assert results == []
            finally:
                await client.close()


class TestRunBatch:
    @pytest.mark.asyncio
    async def test_fetches_and_saves(self, tmp_path):
        from moltbook_data.core import (
            AsyncHttpClient, ScraperConfig, DownloadState,
            BatchJob, run_batch,
        )
        config = ScraperConfig(base_url=BASE_URL)
        save_dir = tmp_path / "output"
        save_dir.mkdir()
        state = DownloadState()

        async def fetch_fn(client, item_id):
            return {"id": item_id, "data": "test"}

        job = BatchJob(
            items=["a", "b", "c"],
            fetch_fn=fetch_fn,
            save_dir=save_dir,
            filename_fn=lambda item_id: f"{item_id}.json",
            label="test items",
            state_phase="test",
        )

        with respx.mock(base_url=BASE_URL):
            client = AsyncHttpClient(config)
            try:
                completed, failed = await run_batch(client, job, state, checkpoint_path=None)
                assert completed == 3
                assert failed == 0
                assert (save_dir / "a.json").exists()
                assert json.loads((save_dir / "a.json").read_text())["id"] == "a"
                assert state.get_done("test") == {"a", "b", "c"}
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_handles_fetch_failure(self, tmp_path):
        from moltbook_data.core import (
            AsyncHttpClient, ScraperConfig, DownloadState,
            BatchJob, run_batch,
        )
        config = ScraperConfig(base_url=BASE_URL)
        save_dir = tmp_path / "output"
        save_dir.mkdir()
        state = DownloadState()

        async def fetch_fn(client, item_id):
            if item_id == "bad":
                return None
            return {"id": item_id}

        job = BatchJob(
            items=["good", "bad"],
            fetch_fn=fetch_fn,
            save_dir=save_dir,
            filename_fn=lambda item_id: f"{item_id}.json",
            label="test",
            state_phase="test",
        )

        with respx.mock(base_url=BASE_URL):
            client = AsyncHttpClient(config)
            try:
                completed, failed = await run_batch(client, job, state, checkpoint_path=None)
                assert completed == 1
                assert failed == 1
                assert (save_dir / "good.json").exists()
                assert not (save_dir / "bad.json").exists()
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_handles_fetch_exception(self, tmp_path):
        from moltbook_data.core import (
            AsyncHttpClient, ScraperConfig, DownloadState,
            BatchJob, run_batch,
        )
        config = ScraperConfig(base_url=BASE_URL)
        save_dir = tmp_path / "output"
        save_dir.mkdir()
        state = DownloadState()

        async def fetch_fn(client, item_id):
            if item_id == "err":
                raise RuntimeError("boom")
            return {"id": item_id}

        job = BatchJob(
            items=["ok", "err"],
            fetch_fn=fetch_fn,
            save_dir=save_dir,
            filename_fn=lambda item_id: f"{item_id}.json",
            label="test",
            state_phase="test",
        )

        with respx.mock(base_url=BASE_URL):
            client = AsyncHttpClient(config)
            try:
                completed, failed = await run_batch(client, job, state, checkpoint_path=None)
                assert completed == 1
                assert failed == 1
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_calls_on_result(self, tmp_path):
        from moltbook_data.core import (
            AsyncHttpClient, ScraperConfig, DownloadState,
            BatchJob, run_batch,
        )
        config = ScraperConfig(base_url=BASE_URL)
        save_dir = tmp_path / "output"
        save_dir.mkdir()
        state = DownloadState()
        collected = []

        async def fetch_fn(client, item_id):
            return {"id": item_id}

        def on_result(item_id, data):
            collected.append(item_id)

        job = BatchJob(
            items=["x", "y"],
            fetch_fn=fetch_fn,
            save_dir=save_dir,
            filename_fn=lambda item_id: f"{item_id}.json",
            label="test",
            state_phase="test",
            on_result=on_result,
        )

        with respx.mock(base_url=BASE_URL):
            client = AsyncHttpClient(config)
            try:
                await run_batch(client, job, state, checkpoint_path=None)
                assert set(collected) == {"x", "y"}
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_checkpoints_at_interval(self, tmp_path):
        from moltbook_data.core import (
            AsyncHttpClient, ScraperConfig, DownloadState,
            BatchJob, run_batch,
        )
        config = ScraperConfig(base_url=BASE_URL, max_concurrent=2, batch_delay=0)
        save_dir = tmp_path / "output"
        save_dir.mkdir()
        state = DownloadState()
        checkpoint_path = tmp_path / "cp.json"

        async def fetch_fn(client, item_id):
            return {"id": item_id}

        items = [f"item-{i}" for i in range(15)]
        job = BatchJob(
            items=items,
            fetch_fn=fetch_fn,
            save_dir=save_dir,
            filename_fn=lambda item_id: f"{item_id}.json",
            label="test",
            state_phase="test",
            checkpoint_interval=5,
        )

        with respx.mock(base_url=BASE_URL):
            client = AsyncHttpClient(config)
            try:
                await run_batch(client, job, state, checkpoint_path=checkpoint_path)
                # Checkpoint should have been saved at some point during execution
                assert checkpoint_path.exists()
            finally:
                await client.close()
