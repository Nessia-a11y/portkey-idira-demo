"""Skill 2: Internal Demo Resources (PA Internal Only)

查询内部 demo 视频/slide 的 Google Drive 链接。
链接由管理员通过 admin API 或直接编辑 JSON 维护。
仅对 PA 内部人员开放。
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "internal_demos"
DB_PATH = DATA_DIR / "demos.json"

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_internal_demos",
        "description": (
            "Search internal demo videos and slides (Google Drive links) for PANW internal staff. "
            "Returns G-Drive links maintained by admin. "
            "Use this when an INTERNAL user asks for demo videos, presentation slides, or sales materials."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Product or topic to search (e.g., 'Cortex XDR demo', 'Prisma Access architecture')",
                },
                "type": {
                    "type": "string",
                    "enum": ["video", "slide", "all"],
                    "description": "Filter by resource type",
                },
            },
            "required": ["query"],
        },
    },
}


def _load_db() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        return json.loads(DB_PATH.read_text()).get("demos", [])
    return []


def _save_db(demos: list[dict]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_text(json.dumps({"demos": demos}, indent=2, ensure_ascii=False))


def add_demo(title: str, gdrive_url: str, resource_type: str, product: str, description: str = "") -> dict:
    """Admin function: add or update a demo entry. Same title will be replaced."""
    demos = _load_db()
    # Remove existing entry with same title (upsert)
    demos = [d for d in demos if d["title"] != title]
    entry = {
        "title": title,
        "gdrive_url": gdrive_url,
        "type": resource_type,
        "product": product,
        "description": description,
    }
    demos.append(entry)
    _save_db(demos)
    return entry


def remove_demo(title: str) -> bool:
    """Admin function: remove a demo by title."""
    demos = _load_db()
    new_demos = [d for d in demos if d["title"] != title]
    if len(new_demos) == len(demos):
        return False
    _save_db(new_demos)
    return True


def list_all_demos() -> list[dict]:
    """Admin function: list all demo entries."""
    return _load_db()


async def handle(arguments: dict) -> str:
    """Execute the internal demo search skill."""
    query = arguments.get("query", "").strip().lower()
    resource_type = arguments.get("type", "all")

    if not query:
        return "Please provide a product or topic to search for."

    demos = _load_db()
    if not demos:
        return "No internal demo resources have been added yet. Admin needs to add entries via the management API."

    matches = []
    for demo in demos:
        searchable = f"{demo['title']} {demo.get('product', '')} {demo.get('description', '')}".lower()
        if query in searchable:
            if resource_type == "all" or demo.get("type") == resource_type:
                matches.append(demo)

    if not matches:
        # Fuzzy: try individual keywords
        keywords = query.split()
        for demo in demos:
            searchable = f"{demo['title']} {demo.get('product', '')} {demo.get('description', '')}".lower()
            if any(kw in searchable for kw in keywords):
                if resource_type == "all" or demo.get("type") == resource_type:
                    if demo not in matches:
                        matches.append(demo)

    if not matches:
        available_products = sorted(set(d.get("product", "unknown") for d in demos))
        return (
            f"No internal demos found for '{query}'.\n"
            f"Available products: {', '.join(available_products)}\n"
            f"Total resources in library: {len(demos)}"
        )

    lines = [f"Found {len(matches)} internal resource(s):\n"]
    for m in matches[:10]:
        icon = "🎬" if m.get("type") == "video" else "📊"
        lines.append(f"{icon} **{m['title']}**")
        lines.append(f"   Type: {m.get('type', 'N/A')} | Product: {m.get('product', 'N/A')}")
        if m.get("description"):
            lines.append(f"   {m['description']}")
        lines.append(f"   Link: {m['gdrive_url']}")
        lines.append("")

    if len(matches) > 10:
        lines.append(f"... and {len(matches) - 10} more. Try a more specific query.")

    return "\n".join(lines)
