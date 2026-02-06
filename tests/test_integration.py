"""
Integration tests against the full download pipeline.

These are oracle-based characterization tests. They capture the exact behavior
of the current implementation and ensure the refactored code produces identical
outputs. They must stay unchanged through the entire refactor.
"""

import json
from pathlib import Path

import pytest

from moltbook_data.moltbook import download_moltbook as download_all_async


@pytest.fixture
def data_dir(tmp_path):
    """Provide a clean temporary data directory."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.mark.asyncio
async def test_full_download_creates_correct_files(mock_api, data_dir):
    """Verify that a full download creates all expected files."""
    await download_all_async(data_dir, resume=False)

    # Posts directory
    posts_dir = data_dir / "posts"
    assert (posts_dir / "post-001.json").exists()
    assert (posts_dir / "post-002.json").exists()
    assert (posts_dir / "post-003.json").exists()

    # Submolts directory
    submolts_dir = data_dir / "submolts"
    assert (submolts_dir / "general.json").exists()
    assert (submolts_dir / "tech.json").exists()
    assert (submolts_dir / "science.json").exists()

    # Agents directory -- agent_dave is 404, so no file
    agents_dir = data_dir / "agents"
    assert (agents_dir / "agent_alice.json").exists()
    assert (agents_dir / "agent_bob.json").exists()
    assert (agents_dir / "agent_charlie.json").exists()
    assert (agents_dir / "agent_eve.json").exists()
    assert not (agents_dir / "agent_dave.json").exists()


@pytest.mark.asyncio
async def test_post_file_contents(mock_api, data_dir):
    """Verify post files contain expected data with metadata."""
    await download_all_async(data_dir, resume=False)

    post_data = json.loads((data_dir / "posts" / "post-002.json").read_text())

    # Original API fields preserved
    assert post_data["success"] is True
    assert post_data["post"]["id"] == "post-002"
    assert post_data["post"]["title"] == "Second Post"
    assert len(post_data["comments"]) == 1
    assert post_data["comments"][0]["author"]["name"] == "agent_dave"

    # Metadata fields added
    assert "_downloaded_at" in post_data
    assert post_data["_endpoint"] == "/posts/post-002"


@pytest.mark.asyncio
async def test_submolt_file_contents(mock_api, data_dir):
    """Verify submolt files contain expected data with metadata."""
    await download_all_async(data_dir, resume=False)

    submolt_data = json.loads((data_dir / "submolts" / "tech.json").read_text())

    assert submolt_data["success"] is True
    assert submolt_data["submolt"]["name"] == "tech"
    assert "_downloaded_at" in submolt_data
    assert submolt_data["_endpoint"] == "/submolts/tech"


@pytest.mark.asyncio
async def test_agent_file_contents(mock_api, data_dir):
    """Verify agent files contain expected data with metadata."""
    await download_all_async(data_dir, resume=False)

    agent_data = json.loads((data_dir / "agents" / "agent_alice.json").read_text())

    assert agent_data["success"] is True
    assert agent_data["agent"]["name"] == "agent_alice"
    assert "_downloaded_at" in agent_data
    assert agent_data["_endpoint"] == "/agents/profile?name=agent_alice"


@pytest.mark.asyncio
async def test_checkpoint_state(mock_api, data_dir):
    """Verify checkpoint contains all fetched IDs and names."""
    await download_all_async(data_dir, resume=False)

    checkpoint = json.loads((data_dir / "checkpoint.json").read_text())

    # All posts tracked
    assert set(checkpoint["phases"]["posts"]) == {"post-001", "post-002", "post-003"}

    # All submolts tracked
    assert set(checkpoint["phases"]["submolts"]) == {"general", "tech", "science"}

    # All successfully fetched agents tracked (not dave -- 404)
    assert set(checkpoint["phases"]["agents"]) == {
        "agent_alice", "agent_bob", "agent_charlie", "agent_eve"
    }

    # Discovered names include all agents and submolts seen
    assert "agent_dave" in checkpoint["discovered"]["agent_names"]
    assert "agent_alice" in checkpoint["discovered"]["agent_names"]
    assert "general" in checkpoint["discovered"]["submolt_names"]
    assert "tech" in checkpoint["discovered"]["submolt_names"]
    assert "science" in checkpoint["discovered"]["submolt_names"]


@pytest.mark.asyncio
async def test_discovered_names(mock_api, data_dir):
    """Verify name extraction discovers agents from comments and submolts from posts."""
    await download_all_async(data_dir, resume=False)

    checkpoint = json.loads((data_dir / "checkpoint.json").read_text())
    discovered_agents = set(checkpoint["discovered"]["agent_names"])

    # agent_dave is discovered from post-002 comments
    assert "agent_dave" in discovered_agents
    # agent_eve is discovered from post-001 comments
    assert "agent_eve" in discovered_agents
    # All post authors discovered
    assert "agent_alice" in discovered_agents
    assert "agent_bob" in discovered_agents
    assert "agent_charlie" in discovered_agents


@pytest.mark.asyncio
async def test_404_agent_gracefully_skipped(mock_api, data_dir):
    """Verify that a 404 agent is not written to disk."""
    await download_all_async(data_dir, resume=False)

    agents_dir = data_dir / "agents"
    assert not (agents_dir / "agent_dave.json").exists()

    # But other agents are written
    agent_files = {f.stem for f in agents_dir.glob("*.json") if f.name != "index.json"}
    assert agent_files == {"agent_alice", "agent_bob", "agent_charlie", "agent_eve"}


@pytest.mark.asyncio
async def test_resume_no_duplicate_fetches(mock_api, data_dir):
    """Verify that resuming doesn't re-fetch already downloaded items."""
    # First run
    await download_all_async(data_dir, resume=False)

    # Count calls from first run
    first_run_calls = len(mock_api.calls)

    # Reset call tracking
    mock_api.calls.clear()

    # Second run with resume=True
    await download_all_async(data_dir, resume=True)

    # The second run should still call the feed + submolt list (to discover new items)
    # but should NOT re-fetch individual posts/submolts/agents
    second_run_calls = mock_api.calls

    # Check no individual post detail calls were made
    post_detail_calls = [
        c for c in second_run_calls
        if "/posts/post-" in str(c.request.url)
    ]
    assert len(post_detail_calls) == 0, f"Unexpected post re-fetches: {post_detail_calls}"

    # Check no individual submolt detail calls
    submolt_detail_calls = [
        c for c in second_run_calls
        if any(f"/submolts/{name}" in str(c.request.url)
               for name in ["general", "tech", "science"])
    ]
    assert len(submolt_detail_calls) == 0, f"Unexpected submolt re-fetches: {submolt_detail_calls}"

    # 404 agents (agent_dave) are NOT tracked in agents_fetched,
    # so they get retried on resume -- this is expected behavior
    agent_calls = [
        c for c in second_run_calls
        if "/agents/profile" in str(c.request.url)
    ]
    # Only agent_dave should be retried (it was 404 and not in agents_fetched)
    assert len(agent_calls) == 1
    assert "agent_dave" in str(agent_calls[0].request.url)


@pytest.mark.asyncio
async def test_json_formatting(mock_api, data_dir):
    """Verify JSON files use indent=2 formatting."""
    await download_all_async(data_dir, resume=False)

    content = (data_dir / "posts" / "post-001.json").read_text()
    # indent=2 means keys are indented with 2 spaces
    assert "\n  " in content
    # Re-parse to confirm valid JSON
    parsed = json.loads(content)
    assert parsed["success"] is True


@pytest.mark.asyncio
async def test_index_files_generated(mock_api, data_dir):
    """Verify index.json files are generated for submolts and agents."""
    await download_all_async(data_dir, resume=False)

    # Submolts index
    submolts_index = json.loads((data_dir / "submolts" / "index.json").read_text())
    assert submolts_index["count"] == 3
    names = {e["name"] for e in submolts_index["submolts"]}
    assert names == {"general", "tech", "science"}

    # Agents index
    agents_index = json.loads((data_dir / "agents" / "index.json").read_text())
    assert agents_index["count"] == 4
    names = {e["name"] for e in agents_index["agents"]}
    assert names == {"agent_alice", "agent_bob", "agent_charlie", "agent_eve"}


@pytest.mark.asyncio
async def test_comment_counts_tracked(mock_api, data_dir):
    """Verify post comment counts are tracked in checkpoint."""
    await download_all_async(data_dir, resume=False)

    checkpoint = json.loads((data_dir / "checkpoint.json").read_text())
    counts = checkpoint["extra"]["post_comment_counts"]

    assert counts["post-001"] == 2
    assert counts["post-002"] == 1
    assert counts["post-003"] == 0
