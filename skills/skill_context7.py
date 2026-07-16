"""Skill: query_context7 — Query library/framework documentation via Context7 MCP (Idira).

Connects to the Context7 MCP server via SSE, resolves library IDs,
and fetches up-to-date documentation.
"""

import httpx
from typing import Any

CONTEXT7_URL = "https://democn.data.aigw.cyberark.cloud/mcp/context7mcpserver"
CONTEXT7_KEY = "53d48153-f5b4-4ae9-8a9d-a9a0042db898"

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


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {CONTEXT7_KEY}",
        "Content-Type": "application/json",
    }


async def _call_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> str:
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
        resp = await client.post(CONTEXT7_URL, json=payload, headers=_headers())
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
    """Resolve library ID, then fetch documentation."""
    library_name = arguments.get("library_name", "")
    topic = arguments.get("topic", "")

    if not library_name:
        return "Error: library_name is required"

    try:
        resolve_result = await _call_mcp_tool("resolve-library-id", {"libraryName": library_name})
    except Exception as e:
        return f"Failed to resolve library: {e}"

    if not resolve_result or "error" in resolve_result.lower():
        return f"Could not resolve library '{library_name}': {resolve_result}"

    library_id = resolve_result.strip().split("\n")[0].strip()

    try:
        doc_args = {"context7CompatibleLibraryID": library_id}
        if topic:
            doc_args["topic"] = topic
        docs_result = await _call_mcp_tool("get-library-docs", doc_args)
    except Exception as e:
        return f"Failed to fetch docs for '{library_name}': {e}"

    return docs_result if docs_result else f"No documentation found for '{library_name}'"
