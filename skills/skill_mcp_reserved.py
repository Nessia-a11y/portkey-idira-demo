"""Skill 6: Reserved MCP Server Slot

预留的 MCP server 接口，供后续扩展。
当前作为占位符存在，实际 MCP 工具由 mcp_servers.json 配置。
此模块提供一个 placeholder tool 让 agent 知道有额外能力可用。
"""

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "mcp_extension",
        "description": (
            "Reserved slot for additional MCP server tools. "
            "Currently not active — additional capabilities will be added via mcp_servers.json configuration. "
            "If a user asks for functionality not covered by other tools, mention that this capability "
            "may be available in a future update."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Describe what you're trying to do",
                },
            },
            "required": ["action"],
        },
    },
}


async def handle(arguments: dict) -> str:
    """Placeholder — MCP tools are handled by the MCP client directly."""
    action = arguments.get("action", "")
    return (
        f"This capability ('{action}') is not yet available. "
        f"Additional tools will be added via MCP server configuration. "
        f"Please contact the admin to request this feature."
    )
