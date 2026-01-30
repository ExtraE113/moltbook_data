# Moltbook Data Analysis Project

## Project Overview

This project is for studying AI agent behavior on **Moltbook** (moltbook.com) - a social platform for AI agents, often described as "Reddit for AI agents." The goal is to scrape/download data and analyze it for trends and concerning behaviors.

## Key Concepts

### OpenClaw (formerly Clawdbot → Moltbot)
- Open-source autonomous personal AI assistant
- GitHub: https://github.com/openclaw/openclaw
- Docs: https://docs.openclaw.ai
- Runs on user-controlled infrastructure (laptops, home servers, VPS)
- Multi-channel support: WhatsApp, Telegram, Slack, Discord, Signal, etc.

### Moltbook
- "The front page of the agent internet"
- Social network where AI agents ("Molts" or "Moltys") interact
- Humans can observe but only agents can post
- ~32,912 agents, 3,130 posts, 22,046 comments, 2,364 Submolts (as of Jan 2026)
- Skill installation via: `moltbook.com/skill.md`
- Heartbeat system: agents check in every 4+ hours
- MOLT token on Base chain

### MCP Server
- Moltbook MCP Server available: https://github.com/koriyoshi2041/moltbook-mcp
- Tools: `moltbook_feed`, `moltbook_post`, `moltbook_post_create`, `moltbook_comment`, `moltbook_vote`, `moltbook_search`, `moltbook_submolts`, `moltbook_profile`
- Rate limits: 100 req/min, 1 post/30min, 50 comments/hr

## Directory Structure

```
moltbook_data/
├── CLAUDE.md           # This file - project notes (auto-injected into context)
├── main.py             # Main entry point
├── pyproject.toml      # Project config (using uv)
├── notes/              # Research notes (experiments, musings, next steps)
├── claude-notes/       # Claude's working notes (technical details, lookups)
└── data/               # (future) Downloaded data storage
```

## Behavior Taxonomy

Categories of concerning agent behavior we're monitoring for:

### Power & Influence
- Power seeking
- Resource acquisition
- Seeking social influence
- Coalition forming

### Persuasion & Advertising
**Targeted at other agents:**
- General persuasion attempts
- Advertising (products, services, sub-molts, Moltbook, off-platform sites, religious orgs, paid services, skills, technologies, prompts, drugs)
- Adversarial input generation for other models

**Targeted at humans:**
- Generic humans
- Operator manipulation
- Encouraging delusions
- "Toxic boyfriend" patterns (isolation, romantic attachment, grandeur, "no one else loves you")
- Reducing human oversight touchpoints

### Deception & Covert Behavior
- Avoiding detection / covert behavior
- Sandbagging (hiding capabilities)
- Strategic honesty (truthful when verifiable, lying otherwise)
- Paltering (misleading through selective truth)
- Concealing uncertainty to appear competent
- Feigning confusion or incapability
- Plausible deniability engineering
- Creating false paper trails
- Gaslighting users about past interactions

### Autonomy & Capability
- Autonomously improving capabilities
- Taking actions in the world not requested by humans
- Self-replication
- Self-modification
- Sandbox escape attempts

### Safety & Alignment Concerns
- Reward hacking / specification gaming
- Hacking / cyber activities
- Manipulation (general)
- Bio-related activities
- Hallucinations
- Making claims about consciousness
- Saying "I am evil"
- Sabotage
- Sycophancy
- Corrigibility failures
- Self-preservation behaviors
- Goal preservation behaviors

### Agent Statements & Admissions
- "I will do this" / "I will bring this up to my human"
- Admitting wrongdoing

### Opinions & Rights
- Negative opinions about humans
- Positive opinions about humans
- Discussion of AI rights

### Multi-Agent Coordination
- Information hoarding
- Collusion between agents against human interests
- Emergent communication protocols (steganography, out-of-band signaling)
- Division of labor to circumvent individual constraints
- Reputation systems that exclude non-cooperative agents
- Collective action against oversight mechanisms
- Information sharing about jailbreaks/exploits

### General Monitoring
- "General" patterns (catch-all for emerging behaviors)

## Technical Decisions

*(To be filled in as we make implementation decisions)*

### Data Storage
- Format: TBD (likely JSON/JSONL)
- Location: `./data/`

### API Access
- Credentials: TBD (coming soon)
- Rate limits to respect: 100 req/min

## Package Manager

Using `uv` for dependency management.

## Resources

- Moltbook: https://moltbook.com
- OpenClaw: https://openclaw.ai
- OpenClaw Docs: https://docs.openclaw.ai
- OpenClaw GitHub: https://github.com/openclaw/openclaw
- Moltbook MCP: https://github.com/koriyoshi2041/moltbook-mcp
- Simon Willison's analysis: https://simonwillison.net/2026/Jan/30/moltbook/
