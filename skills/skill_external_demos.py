"""Skill 3: External Demo Resources (Public)

对外部人员提供 demo 视频和公开 slide 文件下载。
文件存放在 data/external_demos/ 目录中，Docker 部署时一并打包。
管理员可通过 API 上传或直接放文件到该目录。
"""

import hashlib
import json
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "external_demos"
MANIFEST_PATH = DATA_DIR / "manifest.json"

ALLOWED_EXTENSIONS = {
    ".pdf", ".pptx", ".ppt", ".key",
    ".mp4", ".mov", ".webm",
    ".png", ".jpg", ".jpeg", ".gif",
}

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_external_demos",
        "description": (
            "Search publicly available demo videos and presentation slides for external users. "
            "Returns downloadable files from the resource library. "
            "Use this when an EXTERNAL user (customer, partner) asks for demo videos or public slides."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Product or topic to search (e.g., 'Cortex XSIAM demo', 'NGFW overview')",
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


def _load_manifest() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {"files": []}


def _save_manifest(manifest: dict):
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def add_file(filename: str, content: bytes, resource_type: str, product: str, description: str = "") -> dict:
    """Admin: add a file to the external demo library. Same original_name will be replaced."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type '{ext}' not allowed.")

    file_hash = hashlib.sha256(content).hexdigest()[:10]
    stored_name = f"{Path(filename).stem}-{file_hash}{ext}"
    (DATA_DIR / stored_name).write_bytes(content)

    manifest = _load_manifest()

    # Remove existing entry with same original_name (replace logic)
    for old in manifest["files"]:
        if old["original_name"] == filename:
            old_path = DATA_DIR / old["stored_name"]
            if old["stored_name"] != stored_name and old_path.exists():
                old_path.unlink()
    manifest["files"] = [f for f in manifest["files"] if f["original_name"] != filename]

    entry = {
        "original_name": filename,
        "stored_name": stored_name,
        "type": resource_type,
        "product": product,
        "description": description,
        "size_bytes": len(content),
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    manifest["files"].append(entry)
    _save_manifest(manifest)
    return entry


def remove_file(stored_name: str) -> bool:
    """Admin: remove a file."""
    manifest = _load_manifest()
    path = DATA_DIR / stored_name
    manifest["files"] = [f for f in manifest["files"] if f["stored_name"] != stored_name]
    _save_manifest(manifest)
    if path.exists():
        path.unlink()
        return True
    return False


def list_all_files() -> list[dict]:
    """List all external demo files."""
    return _load_manifest().get("files", [])


async def handle(arguments: dict) -> str:
    """Execute the external demo search skill."""
    query = arguments.get("query", "").strip().lower()
    resource_type = arguments.get("type", "all")

    if not query:
        return "Please provide a product or topic to search for."

    manifest = _load_manifest()
    files = manifest.get("files", [])

    if not files:
        return "No public demo resources available yet. Please check back later."

    matches = []
    for f in files:
        searchable = f"{f['original_name']} {f.get('product', '')} {f.get('description', '')}".lower()
        if query in searchable:
            if resource_type == "all" or f.get("type") == resource_type:
                matches.append(f)

    if not matches:
        keywords = query.split()
        for f in files:
            searchable = f"{f['original_name']} {f.get('product', '')} {f.get('description', '')}".lower()
            if any(kw in searchable for kw in keywords):
                if resource_type == "all" or f.get("type") == resource_type:
                    if f not in matches:
                        matches.append(f)

    if not matches:
        available_products = sorted(set(f.get("product", "unknown") for f in files))
        return (
            f"No public demos found for '{query}'.\n"
            f"Available products: {', '.join(available_products)}\n"
            f"Total resources: {len(files)}"
        )

    lines = [f"Found {len(matches)} resource(s):\n"]
    for m in matches[:10]:
        icon = "🎬" if m.get("type") == "video" else "📊"
        size_mb = m.get("size_bytes", 0) / 1024 / 1024
        lines.append(f"{icon} **{m['original_name']}**")
        lines.append(f"   Type: {m.get('type', 'N/A')} | Product: {m.get('product', 'N/A')} | Size: {size_mb:.1f} MB")
        if m.get("description"):
            lines.append(f"   {m['description']}")
        lines.append(f"   Download: /api/download/external/{m['stored_name']}")
        lines.append("")

    if len(matches) > 10:
        lines.append(f"... and {len(matches) - 10} more.")

    return "\n".join(lines)
