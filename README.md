# ai-limit

English | [中文说明](README.zh-CN.md)

Official downloads: https://github.com/zhuchenxi113/ai-limit/releases

Website: https://ai-limit.waitsugar.com

A lightweight tool to monitor real-time **Claude Code** and **Codex** usage limits, quota consumption, and token statistics — so you can adjust your AI usage before hitting rate limits. Available as a macOS menu bar app, a Windows system tray app, or a CLI (macOS/Windows).

If you find it useful, a Star would be appreciated: [GitHub](https://github.com/zhuchenxi113/ai-limit) · [Gitee](https://gitee.com/zhuchenxi113/ai-limit)

## macOS Menu Bar App

Lives in the menu bar, shows live quota at a glance — no terminal needed. Because it displays text data directly in the menu bar, it takes up more space than a typical icon-only app; a menu bar manager like Bartender is recommended.

![Menu bar screenshot](docs/screenshot-menubar.png)

![Menu bar in context](docs/screenshot-menubar-2.png)

![Menu bar in context](docs/screenshot-menubar-3.png)

<table><tr>
  <td><img src="docs/screenshot-menubar-dropdown-v0321-en.png" width="280" /></td>
  <td><img src="docs/screenshot-menubar-dropdown-v0321.png" width="280" /></td>
</tr></table>

**One-line install**

```bash
curl -fsSL https://raw.githubusercontent.com/zhuchenxi113/ai-limit/main/install.sh | bash
```

**First launch**

The app is signed with a Developer ID and notarized by Apple, so it should open normally — no Gatekeeper bypass needed. If macOS still blocks it (e.g. the notarization/certificate has lapsed), use whichever matches your macOS version:

- **macOS 15 Sequoia and later:** double-click the app. You'll see the dialog below (only **Done** / **Move to Trash** — there is no "Open" button anymore). Click **Done**, then open **System Settings → Privacy & Security**, scroll down to **Security**, and click **Open Anyway** next to ""AI Limit.app" was blocked…". Confirm with your password / Touch ID.
- **macOS 14 Sonoma and earlier:** right-click (Control-click) the app → **Open** → **Open** in the dialog.

<table><tr>
  <td><img src="docs/install-blocked-dialog.png" width="300" /></td>
  <td><img src="docs/install-open-anyway.png" width="440" /></td>
</tr></table>

> The screenshots are from a Chinese-language system. On an English system the same dialogs read: **"AI Limit.app" Not Opened** / **Done** / **Move to Trash**, and **Privacy & Security → Open Anyway**.

**Features**

- Chinese / English UI toggle
- 5-hour / 7-day quota window toggle
- Claude and Codex shown simultaneously, each independently configurable
- Manual refresh
- Click to expand details (plan, usage, reset time)

**Build from source**

```bash
cd menubar
/opt/homebrew/bin/python3.13 setup.py py2app
bash make-dmg.sh
```

> Homebrew Python is required. Anaconda Python causes dylib path conflicts that prevent the packaged app from launching.

---

## Windows Tray App

Lives in the system taskbar, one icon per service (Claude / Codex), showing a battery-style bar + percentage. Same underlying data-fetch logic as the CLI/macOS app, independently implemented UI (PySide6, since Windows' native tray icon toolkits can't reproduce the rich menu-bar rendering rumps/AppKit gives on macOS).

> **Status: not yet in a signed public release.** Pre-built, code-signed installers aren't published yet (blocked on acquiring a Windows code-signing certificate). Build from source in the meantime — see below.

**Build from source**

```powershell
pip install -r requirements.txt
pip install pyinstaller
cd menubar\windows
python make_ico.py
pyinstaller pyinstaller.spec
# optional: package as an installer (requires Inno Setup: https://jrsoftware.org/isinfo.php)
& "C:\path\to\Inno Setup 6\ISCC.exe" installer.iss
```

The unpackaged app can also be run directly for testing: `python menubar\windows\ai-limit-tray.py`.

**Features**: same toggles as the macOS app (display window, refresh interval, language), plus start-at-login (registry `Run` key) and in-app update check.

**Known Windows-specific limitation**: reading Live quota from Chrome/Edge cookies does not work — see [Notes](#notes) below for why, and use Firefox instead if you want Live quota on Windows.

---

## CLI

Output language is detected automatically from your system locale.

### Preview

![CLI screenshot (English)](docs/screenshot-cli-v0321-en.png)

![CLI screenshot (Chinese)](docs/screenshot-cli-v0321.png)

### Requirements

- macOS or Windows
- Python 3.8+
- Chrome or Firefox signed in to [claude.ai](https://claude.ai) (for Claude quota)
- Chrome or Firefox signed in to [chatgpt.com](https://chatgpt.com) (recommended path for Codex quota)
  - **On Windows**, this must be **Firefox**, not Chrome/Edge — see [Notes](#notes) for why
- Optional: [Codex CLI](https://developers.openai.com/codex/cli) installed and signed in (fallback when browser cookies are unavailable)

### Usage Prerequisites

ai-limit only reads your existing local Claude / ChatGPT browser session and local usage records. It does not provide subscriptions and does not bypass any quota limits.

- If Claude Code is available and signed in, Claude Code quota is shown.
- If ChatGPT / Codex is available and signed in, Codex quota is shown.
- Services that are unavailable or not signed in show a ⚠️ warning. You can hide each service from the menu bar app under `Services`.
- If both services are unavailable, the menu bar shows `ai-limit ⚠️` or the corresponding error state.

### Installation

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

### Usage

```bash
ai-limit              # Last 7 days (default)
ai-limit --days 1     # Today only
ai-limit --all        # Full history
ai-limit --detail     # Show per-model token breakdown
```

Output language is auto-detected from the system locale (Chinese on zh systems, English elsewhere). Override with `AI_LIMIT_LANG`:

```bash
AI_LIMIT_LANG=en ai-limit   # force English
AI_LIMIT_LANG=zh ai-limit   # force Chinese
```

---

## Data Sources

### Claude Code

| Data | Source |
|------|--------|
| Token usage details | `~/.claude/projects/**/*.jsonl` |
| Live quota | Browser cookie → `claude.ai/api/organizations/{orgId}/usage` |

Quota reading requires an active browser session on claude.ai. Falls back gracefully with an error message and a direct link if the cookie is missing or expired.

### Codex

Data sources are tried in priority order:

| Priority | Data | Source | Triggers 5h window? |
|------|------|--------|------|
| 1 | Live quota | Browser cookie → `chatgpt.com/backend-api/codex/usage` | ❌ No |
| 2 | Live quota | `codex app-server` WebSocket → `account/rateLimits/read` | ⚠️ **Yes** |
| 3 | Local fallback | `~/.codex/sessions/**/*.jsonl` | ❌ No |

The browser path (1) reuses the same analytics endpoint that powers the chatgpt.com dashboard. It returns **merged Cloud + CLI usage**, is read-only, and does not trigger a new window. This is the recommended default.

> **⚠️ Side-effect warning (Codex protocol limitation):** When path 1 fails (not signed in to chatgpt.com / cookies expired / network issue), ai-limit falls back to `codex app-server`. That path sends an `initialize` call, which OpenAI counts as a session start — if the current 5-hour window has already expired, **this triggers a new 5-hour rolling window**. This is an inherent consequence of how the Codex CLI exposes its data; no workaround exists at the tool level.

## Notes

- **Browser cookie reading, macOS vs Windows**: on macOS, Chrome's cookie encryption key lives in the system Keychain, which has a legitimate per-app consent mechanism (the OS prompts you to allow access) — so Chrome/Firefox both work. On Windows, Chrome/Edge (Chromium 127+, since mid-2024) use "App-Bound Encryption", which cryptographically ties cookie decryption to the browser's own signed executable, with no equivalent third-party consent path — so `browser_cookie3` cannot decrypt Chrome/Edge cookies on Windows at all (this is a deliberate anti-malware protection, not a bug on our end; the only known bypass mimics cookie-stealing malware behavior and gets flagged by antivirus, so we don't implement it). **Firefox is unaffected** (it stores cookies unencrypted on disk) and works fine on both platforms — use Firefox for Live quota on Windows.
- **Unofficial API**: Claude quota is fetched from an internal claude.ai endpoint, not an official API — it may break with future updates
- **Occasional ⚠️ (Cloudflare challenge)**: claude.ai / chatgpt.com may temporarily serve a Cloudflare bot-challenge to non-browser requests (based on TLS fingerprint — even a valid cookie can be blocked), surfaced as a ⚠️. It usually clears on its own. This affects any non-browser tool accessing these sites — the official Claude Code / Codex CLIs hit the same thing — so it is not a defect of ai-limit and generally needs no action. If the warning persists, open the Claude usage page or CodeX dashboard from the menu and **keep the tab open** — the active browser session accelerates recovery
- `<synthetic>` model entries are error placeholders written by Claude Code on API failures; they are excluded from all statistics
- Per-model output share is only available for Claude Code; Codex does not expose per-model breakdown

## Maintenance

This is a personal tool maintained on a best-effort basis. Issues and PRs are welcome but not guaranteed to be addressed promptly. No long-term support is promised.

## Other projects by the author

- [CalcPro — Calculator](https://apps.apple.com/us/app/calcpro-calculator-waitsugar/id6759244291): Available on the App Store. If the link doesn't open on your device, search for "WaitSugar CalcPro" in the App Store.
- [观点会审 (Decide)](https://decide.waitsugar.com/): A web-based decision-making tool.

## License

Project code: [Apache License 2.0](LICENSE)

Third-party dependency: `browser-cookie3` is licensed under LGPL.
