"""Skill: query_context7 — Query library/framework documentation via Context7 MCP (Idira).

Connects to the Context7 MCP server via Idira with OAuth PKCE authentication.
On first use, the user is redirected to Idira's login page. After auth, the
access token is stored per-user and used for subsequent MCP calls.
"""

import httpx
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

CONTEXT7_URL = "https://democn.data.aigw.cyberark.cloud/mcp/context7mcpserver"
CLIENT_ID = "53d48153-f5b4-4ae9-8a9d-a9a0042db898"

# MCP auth endpoint — Idira uses /mcp/start-auth/{client_id} pattern
OAUTH_BASE = "https://democn.data.aigw.cyberark.cloud"
START_AUTH_URL = f"{OAUTH_BASE}/mcp/start-auth/{CLIENT_ID}"

# Redirect URI after auth completes
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "https://13.213.58.106/oauth/callback/context7")

# Token storage
TOKEN_DIR = Path(__file__).parent.parent / "data" / "oauth_tokens"
PENDING_DIR = Path(__file__).parent.parent / "data" / "oauth_pending"

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_context7",
        "description": (
            "Query programming library/framework documentation via Context7. "
            "Use when user asks to look up docs for any library, SDK, or framework "
            "(e.g. React, FastAPI, LangChain, etc.), or explicitly says 'use context7'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "library_name": {
                    "type": "string",
                    "description": "Name of the library or framework to look up (e.g. 'react', 'fastapi', 'langchain')",
                },
                "topic": {
                    "type": "string",
                    "description": "Optional: specific topic or function to look up within the library",
                },
            },
            "required": ["library_name"],
        },
    },
}


def _ensure_dirs():
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_DIR.mkdir(parents=True, exist_ok=True)


def _token_path(email: str) -> Path:
    return TOKEN_DIR / f"{email}.json"


def get_user_token(email: str) -> Optional[str]:
    """Get stored access token for user, or None if not authenticated."""
    path = _token_path(email)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data.get("expires_at", 0) < time.time():
        # Try refresh
        refresh_token = data.get("refresh_token")
        if refresh_token:
            new_token = _refresh_token(refresh_token)
            if new_token:
                save_user_token(email, new_token)
                return new_token.get("access_token")
        path.unlink(missing_ok=True)
        return None
    return data.get("access_token")


def save_user_token(email: str, token_data: dict):
    """Save OAuth token for user."""
    _ensure_dirs()
    if "expires_in" in token_data and "expires_at" not in token_data:
        token_data["expires_at"] = time.time() + token_data["expires_in"] - 60
    _token_path(email).write_text(json.dumps(token_data))


def _refresh_token(refresh_token: str) -> Optional[dict]:
    """Attempt to refresh an expired access token."""
    try:
        token_url = f"{OAUTH_BASE}/oauth2/token"
        resp = httpx.post(token_url, data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        }, timeout=10.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def create_auth_url(email: str) -> str:
    """Generate MCP start-auth URL for Idira authentication."""
    _ensure_dirs()

    state = secrets.token_urlsafe(32)

    # Store pending auth state
    pending = {
        "email": email,
        "state": state,
        "created_at": time.time(),
    }
    (PENDING_DIR / f"{state}.json").write_text(json.dumps(pending))

    params = {
        "redirect_url": REDIRECT_URI + f"?state={state}",
        "open_in_browser": "1",
        "product_surface": "panw-product-helper",
    }
    return f"{START_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str = None, state: str = None, token: str = None) -> Optional[str]:
    """Process auth callback. Accepts either a code to exchange or a direct token."""
    if not state:
        return None

    pending_path = PENDING_DIR / f"{state}.json"
    if not pending_path.exists():
        return None

    pending = json.loads(pending_path.read_text())
    email = pending["email"]

    # Clean up
    pending_path.unlink(missing_ok=True)

    # Check expiry (10 min)
    if time.time() - pending["created_at"] > 600:
        return None

    # If token is provided directly in callback (MCP auth pattern)
    if token:
        token_data = {"access_token": token, "expires_at": time.time() + 3600}
        save_user_token(email, token_data)
        return email

    # If code is provided, exchange it
    if code:
        token_url = f"{OAUTH_BASE}/oauth2/token"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(token_url, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
            })

        if resp.status_code != 200:
            return None

        token_data = resp.json()
        save_user_token(email, token_data)
        return email

    return None


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


async def _call_mcp_tool(tool_name: str, arguments: dict[str, Any], access_token: str) -> str:
    """Call a tool on the Context7 MCP server via HTTP POST (JSON-RPC style)."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(CONTEXT7_URL, json=payload, headers=_headers(access_token))
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        return f"Context7 error: {data['error']}"

    result = data.get("result", {})
    content = result.get("content", [])
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("text"):
            parts.append(block["text"])
    return "\n".join(parts) if parts else str(result)


async def handle(arguments: dict[str, Any]) -> str:
    """Resolve library ID, then fetch documentation.

    If the user hasn't authenticated with Idira yet, returns an auth URL
    that the frontend should redirect the user to.
    """
    library_name = arguments.get("library_name", "")
    topic = arguments.get("topic", "")
    email = arguments.get("_user_email", "")

    if not library_name:
        return "Error: library_name is required"

    # Check if user has a valid token
    token = get_user_token(email) if email else None
    if not token:
        # Generate auth URL for user
        auth_url = create_auth_url(email) if email else create_auth_url("anonymous")
        return json.dumps({
            "needs_auth": True,
            "auth_url": auth_url,
            "message": "需要先登录 Idira 进行身份验证。请点击以下链接完成认证后重试。",
        })

    try:
        resolve_result = await _call_mcp_tool("resolve-library-id", {"libraryName": library_name}, token)
    except Exception as e:
        return f"Failed to resolve library: {e}"

    if not resolve_result or "error" in resolve_result.lower():
        return f"Could not resolve library '{library_name}': {resolve_result}"

    library_id = resolve_result.strip().split("\n")[0].strip()

    try:
        doc_args = {"context7CompatibleLibraryID": library_id}
        if topic:
            doc_args["topic"] = topic
        docs_result = await _call_mcp_tool("get-library-docs", doc_args, token)
    except Exception as e:
        return f"Failed to fetch docs for '{library_name}': {e}"

    return docs_result if docs_result else f"No documentation found for '{library_name}'"
