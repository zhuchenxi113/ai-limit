# ai-limit

[中文说明](README.zh-CN.md)

A lightweight CLI tool to monitor real-time **Claude Code** and **Codex** usage limits, quota consumption, and token statistics — so you can adjust your AI usage intensity before hitting rate limits.

## Preview

```
────────────────────────────────────────────────────
                    Claude Code                     

  Stats from: 05-19 15:24 CST  (last 7 days)
  Total output: 3.2M  |  Net input (non-cache): 13.9M

  Output share
  sonnet-4-6  ███████████████░░░░░  76%
  opus-4-7    █████░░░░░░░░░░░░░░░  24%

  Live quota  (independent of --days range)
  Source: claude.ai usage API  (browser session)

  5-hour window  ██████████████░░░░░░  left 68%  (used 32%)
  Resets at: 05-26 16:20 CST

  7-day window  ██████████████████░░  left 89%  (used 11%)
  Resets at: 05-31 13:00 CST

  📊 At current rate (0.3%/hr), 89% left ≈ 344 hrs

────────────────────────────────────────────────────
                CodeX (OpenAI GPT-5)                

  Data time: 05-26 15:24 CST  (live)
  Source: codex app-server WebSocket
  Plan: PLUS

  5-hour window  ████████░░░░░░░░░░░░  left 39%  (used 61%)
  Resets at: 05-26 17:22 CST

  7-day window  ██████████████████░░  left 89%  (used 11%)
  Resets at: 06-01 18:26 CST

  📊 At current rate (0.5%/hr), 89% left ≈ 170 hrs

────────────────────────────────────────────────────
```

## Requirements

- macOS
- Python 3.8+
- Chrome or Firefox signed in to [claude.ai](https://claude.ai) (for Claude quota)
- [Codex CLI](https://developers.openai.com/codex/cli) installed and signed in (for Codex quota)

## Installation

**1. Clone the repo**

```bash
git clone https://github.com/zhuchenxi113/ai-limit.git ~/Developer/ai-limit
```

**2. Install dependencies**

```bash
pip install -r requirements.txt
```

**3. Add an alias**

Add to `~/.zshrc`:

```bash
alias ai-limit="python3 ~/Developer/ai-limit/usage.py"
```

Then reload:

```bash
source ~/.zshrc
```

## Usage

```bash
ai-limit              # Last 7 days (default)
ai-limit --days 1     # Today only
ai-limit --all        # Full history
ai-limit --offline    # Skip Codex app-server, use local snapshot only
ai-limit --detail     # Show per-model token breakdown
```

Output language is auto-detected from the system locale (Chinese on zh systems, English elsewhere). Override with the `AI_LIMIT_LANG` environment variable:

```bash
AI_LIMIT_LANG=en ai-limit   # force English
AI_LIMIT_LANG=zh ai-limit   # force Chinese
```

## Data Sources

### Claude Code

| Data | Source |
|------|--------|
| Token usage details | `~/.claude/projects/**/*.jsonl` |
| Live quota | Browser cookie → `claude.ai/api/organizations/{orgId}/usage` |

Quota reading requires an active browser session on claude.ai. Falls back gracefully with an error message and a direct link if the cookie is missing or expired.

### Codex

| Data | Source |
|------|--------|
| Live quota | `codex app-server` WebSocket → `account/rateLimits/read` |
| Local fallback | `~/.codex/sessions/**/*.jsonl` |

Prefers live data via the official Codex CLI. Falls back to the latest local snapshot with a staleness timestamp if the app-server is unavailable.

## Notes

- **macOS only**: browser cookie reading relies on the system Keychain to decrypt Chrome cookies
- **Unofficial API**: Claude quota is fetched from an internal claude.ai endpoint, not an official API — it may break with future updates
- `<synthetic>` model entries are error placeholders written by Claude Code on API failures; they are excluded from all statistics

## Maintenance

This is a personal tool maintained on a best-effort basis. Issues and PRs are welcome but not guaranteed to be addressed promptly. No long-term support is promised.

## License

Project code: [Apache License 2.0](LICENSE)

Third-party dependency: `browser-cookie3` is licensed under LGPL.
