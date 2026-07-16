"""MCP client scaffolding.

Reads server configs from mcp_servers.json and exposes their tools to the agent.
Add stdio or SSE server entries to mcp_servers.json to enable them.

Example mcp_servers.json:
{
  "servers": [
    {
      "name": "filesystem",
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    {
      "name": "my-remote",
      "type": "sse",
      "url": "https://example.com/mcp/sse"
    }
  ]
}
"""

import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client


class MCPManager:
    def __init__(self, config_path: str = "mcp_servers.json"):
        self.config_path = Path(config_path)
        self.sessions: dict[str, ClientSession] = {}
        self.tools: list[dict[str, Any]] = []
        self._tool_to_server: dict[str, str] = {}
        self._exit_stack: AsyncExitStack | None = None

    async def start(self) -> None:
        if not self.config_path.exists():
            return
        config = json.loads(self.config_path.read_text())
        servers = config.get("servers", [])
        if not servers:
            return

        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()

        for server in servers:
            name = server["name"]
            try:
                if server["type"] == "stdio":
                    params = StdioServerParameters(
                        command=server["command"],
                        args=server.get("args", []),
                        env=server.get("env"),
                    )
                    read, write = await self._exit_stack.enter_async_context(
                        stdio_client(params)
                    )
                elif server["type"] == "sse":
                    sse_kwargs = {"url": server["url"]}
                    if server.get("headers"):
                        sse_kwargs["headers"] = server["headers"]
                    read, write = await self._exit_stack.enter_async_context(
                        sse_client(**sse_kwargs)
                    )
                else:
                    continue

                session = await self._exit_stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                self.sessions[name] = session

                tools_resp = await session.list_tools()
                for tool in tools_resp.tools:
                    qualified_name = f"{name}__{tool.name}"
                    self._tool_to_server[qualified_name] = name
                    self.tools.append(
                        {
                            "type": "function",
                            "function": {
                                "name": qualified_name,
                                "description": tool.description or "",
                                "parameters": tool.inputSchema
                                or {"type": "object", "properties": {}},
                            },
                        }
                    )
            except Exception as e:
                print(f"[mcp] failed to connect to {name}: {e}")

    async def call_tool(self, qualified_name: str, arguments: dict[str, Any]) -> str:
        server_name = self._tool_to_server.get(qualified_name)
        if not server_name:
            return f"unknown tool: {qualified_name}"
        session = self.sessions[server_name]
        original_name = qualified_name.split("__", 1)[1]
        result = await session.call_tool(original_name, arguments)
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else "(no output)"

    async def stop(self) -> None:
        if self._exit_stack:
            await self._exit_stack.__aexit__(None, None, None)
            self._exit_stack = None
