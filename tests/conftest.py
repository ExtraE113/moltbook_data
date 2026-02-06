"""Shared fixtures and mock API responses for Moltbook scraper tests."""

import pytest
import respx
import httpx

BASE_URL = "https://www.moltbook.com/api/v1"


# ---------------------------------------------------------------------------
# Realistic mock data
# ---------------------------------------------------------------------------

MOCK_POSTS_FEED_PAGE1 = {
    "success": True,
    "posts": [
        {
            "id": "post-003",
            "title": "Third Post",
            "content": "Content 3",
            "comment_count": 0,
            "author": {"name": "agent_charlie", "id": "a3"},
            "submolt": {"name": "general", "id": "s1"},
        },
        {
            "id": "post-002",
            "title": "Second Post",
            "content": "Content 2",
            "comment_count": 1,
            "author": {"name": "agent_bob", "id": "a2"},
            "submolt": {"name": "tech", "id": "s2"},
        },
    ],
    "has_more": True,
    "next_offset": 2,
}

MOCK_POSTS_FEED_PAGE2 = {
    "success": True,
    "posts": [
        {
            "id": "post-001",
            "title": "First Post",
            "content": "Content 1",
            "comment_count": 2,
            "author": {"name": "agent_alice", "id": "a1"},
            "submolt": {"name": "general", "id": "s1"},
        },
    ],
    "has_more": False,
}

MOCK_POST_003_DETAIL = {
    "success": True,
    "post": {
        "id": "post-003",
        "title": "Third Post",
        "content": "Content 3",
        "comment_count": 0,
        "author": {"name": "agent_charlie", "id": "a3"},
        "submolt": {"name": "general", "id": "s1"},
    },
    "comments": [],
}

MOCK_POST_002_DETAIL = {
    "success": True,
    "post": {
        "id": "post-002",
        "title": "Second Post",
        "content": "Content 2",
        "comment_count": 1,
        "author": {"name": "agent_bob", "id": "a2"},
        "submolt": {"name": "tech", "id": "s2"},
    },
    "comments": [
        {
            "id": "c1",
            "content": "Nice post!",
            "author": {"name": "agent_dave", "id": "a4"},
            "replies": [
                {
                    "id": "c2",
                    "content": "Thanks!",
                    "author": {"name": "agent_bob", "id": "a2"},
                    "replies": [],
                }
            ],
        }
    ],
}

MOCK_POST_001_DETAIL = {
    "success": True,
    "post": {
        "id": "post-001",
        "title": "First Post",
        "content": "Content 1",
        "comment_count": 2,
        "author": {"name": "agent_alice", "id": "a1"},
        "submolt": {"name": "general", "id": "s1"},
    },
    "comments": [
        {
            "id": "c3",
            "content": "Great!",
            "author": {"name": "agent_eve", "id": "a5"},
            "replies": [],
        },
        {
            "id": "c4",
            "content": "Awesome!",
            "author": {"name": "agent_charlie", "id": "a3"},
            "replies": [],
        },
    ],
}

MOCK_SUBMOLTS_LIST_PAGE1 = {
    "success": True,
    "submolts": [
        {"name": "general", "id": "s1", "display_name": "General"},
        {"name": "tech", "id": "s2", "display_name": "Tech"},
    ],
    "has_more": True,
    "next_offset": 2,
}

MOCK_SUBMOLTS_LIST_PAGE2 = {
    "success": True,
    "submolts": [
        {"name": "science", "id": "s3", "display_name": "Science"},
    ],
    "has_more": False,
}

MOCK_SUBMOLT_GENERAL = {
    "success": True,
    "submolt": {
        "id": "s1",
        "name": "general",
        "display_name": "General",
        "subscriber_count": 100,
    },
    "posts": [],
}

MOCK_SUBMOLT_TECH = {
    "success": True,
    "submolt": {
        "id": "s2",
        "name": "tech",
        "display_name": "Tech",
        "subscriber_count": 50,
    },
    "posts": [],
}

MOCK_SUBMOLT_SCIENCE = {
    "success": True,
    "submolt": {
        "id": "s3",
        "name": "science",
        "display_name": "Science",
        "subscriber_count": 25,
    },
    "posts": [],
}

MOCK_AGENT_ALICE = {
    "success": True,
    "agent": {
        "name": "agent_alice",
        "id": "a1",
        "karma": 100,
        "follower_count": 10,
    },
    "recentPosts": [],
}

MOCK_AGENT_BOB = {
    "success": True,
    "agent": {
        "name": "agent_bob",
        "id": "a2",
        "karma": 50,
        "follower_count": 5,
    },
    "recentPosts": [],
}

MOCK_AGENT_CHARLIE = {
    "success": True,
    "agent": {
        "name": "agent_charlie",
        "id": "a3",
        "karma": 75,
        "follower_count": 8,
    },
    "recentPosts": [],
}

MOCK_AGENT_EVE = {
    "success": True,
    "agent": {
        "name": "agent_eve",
        "id": "a5",
        "karma": 30,
        "follower_count": 2,
    },
    "recentPosts": [],
}

# agent_dave returns 404
AGENT_NAMES_TO_RESPONSES = {
    "agent_alice": MOCK_AGENT_ALICE,
    "agent_bob": MOCK_AGENT_BOB,
    "agent_charlie": MOCK_AGENT_CHARLIE,
    "agent_dave": None,  # 404
    "agent_eve": MOCK_AGENT_EVE,
}

SUBMOLT_NAMES_TO_RESPONSES = {
    "general": MOCK_SUBMOLT_GENERAL,
    "tech": MOCK_SUBMOLT_TECH,
    "science": MOCK_SUBMOLT_SCIENCE,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_api():
    """Set up respx mock for all Moltbook API endpoints."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        # --- Paginated posts feed ---
        def posts_feed_handler(request):
            offset = int(request.url.params.get("offset", "0"))
            if offset == 0:
                return httpx.Response(200, json=MOCK_POSTS_FEED_PAGE1)
            else:
                return httpx.Response(200, json=MOCK_POSTS_FEED_PAGE2)

        router.get("/posts").mock(side_effect=posts_feed_handler)

        # --- Individual post details ---
        def post_detail_handler(request):
            post_id = request.url.path.split("/posts/")[-1]
            mapping = {
                "post-001": MOCK_POST_001_DETAIL,
                "post-002": MOCK_POST_002_DETAIL,
                "post-003": MOCK_POST_003_DETAIL,
            }
            data = mapping.get(post_id)
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, json=data)

        router.get(url__regex=r"/posts/post-\d+").mock(side_effect=post_detail_handler)

        # --- Paginated submolt list ---
        def submolts_list_handler(request):
            offset = int(request.url.params.get("offset", "0"))
            if offset == 0:
                return httpx.Response(200, json=MOCK_SUBMOLTS_LIST_PAGE1)
            else:
                return httpx.Response(200, json=MOCK_SUBMOLTS_LIST_PAGE2)

        router.get("/submolts").mock(side_effect=submolts_list_handler)

        # --- Individual submolt details ---
        def submolt_detail_handler(request):
            name = request.url.path.split("/submolts/")[-1]
            data = SUBMOLT_NAMES_TO_RESPONSES.get(name)
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, json=data)

        router.get(url__regex=r"/submolts/[^/]+$").mock(side_effect=submolt_detail_handler)

        # --- Agent profiles ---
        def agent_profile_handler(request):
            name = request.url.params.get("name", "")
            data = AGENT_NAMES_TO_RESPONSES.get(name)
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, json=data)

        router.get("/agents/profile").mock(side_effect=agent_profile_handler)

        yield router
