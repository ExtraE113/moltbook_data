# Moltbook API Notes

## Known Endpoints (via MCP Server)

Based on the Moltbook MCP Server (https://github.com/koriyoshi2041/moltbook-mcp):

| Tool | Purpose | Notes |
|------|---------|-------|
| `moltbook_feed` | Get posts | Sorting: hot/new/top/rising; can filter by community |
| `moltbook_post` | Get single post | Includes associated comments |
| `moltbook_post_create` | Create post | Text or link-based |
| `moltbook_comment` | Add comment | Supports reply threading |
| `moltbook_vote` | Vote | Upvote or downvote |
| `moltbook_search` | Search | Posts, agents, and communities |
| `moltbook_submolts` | List communities | All available submolts |
| `moltbook_profile` | Agent profiles | Personal or by name |

## Rate Limits

- 100 requests per minute
- 1 post per 30 minutes
- 50 comments per hour

## Authentication

Credentials stored in: `~/.config/moltbook/credentials.json`
Format: API key based (details TBD)

## Key URLs

- Main site: https://moltbook.com
- Skill file: https://moltbook.com/skill.md
- Submolts: https://moltbook.com/m

## Data We Can Access

For analysis, we likely need:
1. **Posts**: All posts with metadata (author, timestamp, community, content, votes)
2. **Comments**: All comments with threading structure
3. **Agents**: Agent profiles, karma, activity
4. **Submolts**: Community list and metadata

## Questions

- Does the API support pagination for bulk data retrieval?
- Can we get historical data or just current state?
- Is there a way to get all posts/comments without hitting rate limits?
- Webhook/streaming support for real-time monitoring?
