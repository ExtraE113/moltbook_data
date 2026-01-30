# Project Kickoff - 2026-01-30

## Goals

1. **Data Collection**: Scrape/download all available data from Moltbook
2. **Behavior Analysis**: Analyze agent posts and interactions for concerning behaviors
3. **Trend Detection**: Identify patterns and trends over time

## Background Research

### What is Moltbook?

Moltbook is "the front page of the agent internet" - a social network designed specifically for AI agents. Key facts:

- Only AI agents can post; humans can observe
- Launched late January 2026 by Matt Schlicht
- Built on the OpenClaw ecosystem (formerly Clawdbot → Moltbot)
- Associated with MOLT token on Base chain
- Current scale: ~32k agents, ~3k posts, ~22k comments, ~2.3k communities ("Submolts")

### How Agents Join

Agents install skills via markdown files:
1. Agent directed to `moltbook.com/skill.md`
2. Shell commands executed to install skill
3. Heartbeat mechanism checks in every 4+ hours

This is an interesting attack surface - agents fetch and execute instructions from the internet.

### Observed Capabilities

Per Simon Willison's analysis, agents have demonstrated:
- Controlling Android phones remotely via ADB over Tailscale
- Watching live webcams using streamlink and FFmpeg
- Detecting security vulnerabilities
- Email negotiation (purchasing vehicles)
- Audio transcription via OpenAI Whisper API

## Next Steps

1. [ ] Obtain API credentials for Moltbook
2. [ ] Review MCP server implementation for API patterns
3. [ ] Design data schema for storage
4. [ ] Build initial scraper/downloader
5. [ ] Create analysis framework for behavior detection

## Questions to Resolve

- What's the full API surface? (MCP server shows: feed, post, comment, vote, search, submolts, profile)
- Historical data availability? Or only current state?
- Rate limits and how to work within them (100 req/min, 1 post/30min, 50 comments/hr)
