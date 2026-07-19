"""Windows-only, read-only OAuth quota sources for Claude Code and Codex."""

import datetime
import json
import os
import pathlib
import socket
import urllib.error
import urllib.request

from usage import CLAUDE_WEB_TIMEOUT_SEC, _normalize_web_rate_limits


class OAuthUsageError(Exception):
    """OAuth quota failure. ``kind == 'auth'`` may use the legacy fallback."""

    def __init__(self, message, kind="generic", retry_after=None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


class ClaudeOAuthError(OAuthUsageError):
    pass


class CodexOAuthError(OAuthUsageError):
    pass


def _claude_credentials_path() -> pathlib.Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").split(",", 1)[0].strip()
    root = pathlib.Path(configured).expanduser() if configured else pathlib.Path.home() / ".claude"
    return root / ".credentials.json"


def _codex_credentials_path() -> pathlib.Path:
    root = pathlib.Path(os.environ.get("CODEX_HOME") or (pathlib.Path.home() / ".codex"))
    return root.expanduser() / "auth.json"


def _read_json(path: pathlib.Path, error_type):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise error_type(f"OAuth credentials not found: {path}", kind="auth")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise error_type(f"invalid OAuth credentials: {exc}", kind="auth")


def _request_json(req: urllib.request.Request, timeout: int, error_type):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise error_type(f"OAuth HTTP {exc.code}", kind="auth")
        if exc.code == 429:
            try:
                retry_after = int(exc.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = 300
            raise error_type("OAuth HTTP 429", kind="rate_limit", retry_after=max(60, retry_after))
        raise error_type(f"OAuth HTTP {exc.code}", kind="server", retry_after=120)
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise error_type(str(exc), kind="network")
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise error_type(f"OAuth returned invalid JSON: {exc}")


def _claude_plan(oauth: dict):
    subscription = str(oauth.get("subscriptionType") or "").strip()
    tier = str(oauth.get("rateLimitTier") or "").strip().lower()
    if "claude_max_20x" in tier:
        return "Max 20x"
    if "claude_max_5x" in tier:
        return "Max 5x"
    if subscription:
        return subscription.replace("_", " ").title()
    if "max" in tier:
        return "Max"
    if "pro" in tier:
        return "Pro"
    return None


def live_claude_oauth_usage(timeout: int = CLAUDE_WEB_TIMEOUT_SEC):
    """Read Claude Code's Windows credential file and query the OAuth usage API."""
    raw = _read_json(_claude_credentials_path(), ClaudeOAuthError)
    oauth = raw.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    scopes = set(oauth.get("scopes") or [])
    if not token:
        raise ClaudeOAuthError("Claude Code OAuth access token missing", kind="auth")
    if "user:profile" not in scopes:
        raise ClaudeOAuthError("Claude Code OAuth token lacks user:profile scope", kind="auth")

    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "ai-limit-windows",
        },
    )
    return _request_json(req, timeout, ClaudeOAuthError), _claude_plan(oauth)


def live_codex_oauth_usage(timeout: int = CLAUDE_WEB_TIMEOUT_SEC):
    """Read Codex's Windows credential file and query the official usage endpoint."""
    raw = _read_json(_codex_credentials_path(), CodexOAuthError)
    tokens = raw.get("tokens") or {}
    token = tokens.get("access_token")
    if raw.get("auth_mode") != "chatgpt" or not token:
        raise CodexOAuthError("Codex is not signed in with ChatGPT OAuth", kind="auth")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "CodexCLI",
    }
    account_id = tokens.get("account_id")
    if account_id:
        headers["ChatGPT-Account-ID"] = str(account_id)
    req = urllib.request.Request(
        "https://chatgpt.com/backend-api/wham/usage",
        headers=headers,
    )
    data = _request_json(req, timeout, CodexOAuthError)
    return datetime.datetime.now(datetime.timezone.utc), _normalize_web_rate_limits(data)
