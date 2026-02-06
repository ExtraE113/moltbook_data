"""Unit tests for Moltbook-specific fetch functions and name extraction."""

import pytest

from moltbook_data.core import DownloadState


class TestExtractNamesFromResponse:
    def test_extracts_post_author(self):
        from moltbook_data.moltbook import extract_names_from_response
        state = DownloadState()
        data = {"post": {"author": {"name": "alice"}}}
        extract_names_from_response(data, state)
        assert "alice" in state.get_discovered("agent_names")

    def test_extracts_submolt_from_post(self):
        from moltbook_data.moltbook import extract_names_from_response
        state = DownloadState()
        data = {"post": {"submolt": {"name": "general"}}}
        extract_names_from_response(data, state)
        assert "general" in state.get_discovered("submolt_names")

    def test_extracts_comment_authors_recursive(self):
        from moltbook_data.moltbook import extract_names_from_response
        state = DownloadState()
        data = {
            "post": {},
            "comments": [
                {
                    "author": {"name": "bob"},
                    "replies": [
                        {"author": {"name": "charlie"}, "replies": []},
                    ],
                },
            ],
        }
        extract_names_from_response(data, state)
        agents = state.get_discovered("agent_names")
        assert "bob" in agents
        assert "charlie" in agents

    def test_extracts_agent_profile_name(self):
        from moltbook_data.moltbook import extract_names_from_response
        state = DownloadState()
        data = {"agent": {"name": "dave"}}
        extract_names_from_response(data, state)
        assert "dave" in state.get_discovered("agent_names")

    def test_extracts_submolt_detail(self):
        from moltbook_data.moltbook import extract_names_from_response
        state = DownloadState()
        data = {"submolt": {"name": "tech"}}
        extract_names_from_response(data, state)
        assert "tech" in state.get_discovered("submolt_names")

    def test_handles_missing_fields_gracefully(self):
        from moltbook_data.moltbook import extract_names_from_response
        state = DownloadState()
        data = {"post": {}, "comments": []}
        # Should not raise
        extract_names_from_response(data, state)
        assert state.get_discovered("agent_names") == set()

    def test_uses_username_fallback(self):
        from moltbook_data.moltbook import extract_names_from_response
        state = DownloadState()
        data = {"post": {"author": {"username": "fallback_user"}}}
        extract_names_from_response(data, state)
        assert "fallback_user" in state.get_discovered("agent_names")

    def test_uses_slug_fallback_for_submolt(self):
        from moltbook_data.moltbook import extract_names_from_response
        state = DownloadState()
        data = {"post": {"submolt": {"slug": "slug_submolt"}}}
        extract_names_from_response(data, state)
        assert "slug_submolt" in state.get_discovered("submolt_names")


class TestFeedNameExtraction:
    """Test that feed items can be wrapped for extract_names_from_response."""

    def test_wrap_feed_post(self):
        from moltbook_data.moltbook import extract_names_from_response
        state = DownloadState()
        feed_post = {
            "id": "post-1",
            "author": {"name": "alice"},
            "submolt": {"name": "general"},
        }
        # Wrap as {"post": post} to reuse extract_names_from_response
        extract_names_from_response({"post": feed_post}, state)
        assert "alice" in state.get_discovered("agent_names")
        assert "general" in state.get_discovered("submolt_names")
