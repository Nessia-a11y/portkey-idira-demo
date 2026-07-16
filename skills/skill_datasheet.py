"""Skill 1: Datasheet Download

从 RAG 库中搜索已上传的 datasheet，同时保留在线搜索能力。
在提供文件前由 agent 询问用户需要什么语言。
"""

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, quote_plus

import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent.parent / "data" / "datasheets"
MANIFEST_PATH = DATA_DIR / "manifest.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

PANW_SEARCH_BASE = "https://www.paloaltonetworks.com"
PANW_SEARCH_CN = "https://www.paloaltonetworks.cn"

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "search_datasheet",
        "description": (
            "Search for PANW product datasheets. First checks the local RAG library for uploaded datasheets, "
            "then falls back to online search (Google + PANW official site). "
            "IMPORTANT: Before calling this tool, ask the user which language they prefer (中文/English). "
            "Pass the preferred language in the 'language' parameter."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Product name or keyword to search for (e.g., 'PA-450', 'Cortex XDR', 'Prisma Access')",
                },
                "language": {
                    "type": "string",
                    "enum": ["zh", "en", "any"],
                    "description": "Preferred language: 'zh' for Chinese, 'en' for English, 'any' for no preference",
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
    return {"datasheets": {}}


def _save_manifest(manifest: dict):
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def _search_local(query: str, language: str) -> list[dict]:
    """Search local RAG library for matching datasheets."""
    manifest = _load_manifest()
    results = []
    query_lower = query.lower()

    for key, entry in manifest.get("datasheets", {}).items():
        title_lower = entry.get("title", "").lower()
        filename_lower = entry.get("filename", "").lower()

        if query_lower in title_lower or query_lower in key or query_lower in filename_lower:
            entry_lang = entry.get("language", "en")
            if language == "any" or language == "zh" and entry_lang in ("zh-CN", "zh") or language == "en" and entry_lang == "en":
                results.append(entry)
            else:
                results.append(entry)

    return results


def _search_google(query: str, client: httpx.Client) -> list[dict]:
    """Use Google search to find PANW datasheet PDFs."""
    results = []
    search_queries = [
        f"site:paloaltonetworks.com filetype:pdf {query} datasheet",
        f"site:paloaltonetworks.cn filetype:pdf {query}",
        f"site:paloaltonetworks.com filetype:pdf {query}",
    ]
    for sq in search_queries:
        try:
            url = f"https://www.google.com/search?q={quote_plus(sq)}&num=10"
            resp = client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/url?q=" in href:
                    real_url = href.split("/url?q=")[1].split("&")[0]
                    if "paloaltonetworks" in real_url and ".pdf" in real_url:
                        from urllib.parse import unquote
                        real_url = unquote(real_url)
                        title = a.get_text(strip=True) or Path(real_url).stem
                        if real_url not in [r["url"] for r in results]:
                            results.append({"title": title, "url": real_url})
        except Exception:
            continue
        if results:
            break
    return results


def _search_panw_direct(query: str, language: str, client: httpx.Client) -> list[dict]:
    """Try common PANW datasheet/content URL patterns."""
    slug = re.sub(r"[^\w-]", "-", query.lower()).strip("-")
    candidates = []

    if language in ("zh", "any"):
        candidates += [
            f"{PANW_SEARCH_CN}/content/dam/pan/zh_CN/assets/pdf/datasheets/{slug}.pdf",
            f"{PANW_SEARCH_CN}/content/dam/pan/zh_CN/assets/pdf/datasheets/{slug}-datasheet.pdf",
        ]
    if language in ("en", "any"):
        candidates += [
            f"{PANW_SEARCH_BASE}/content/dam/pan/en_US/assets/pdf/datasheets/{slug}.pdf",
            f"{PANW_SEARCH_BASE}/content/dam/pan/en_US/assets/pdf/datasheets/{slug}-datasheet.pdf",
        ]
    candidates.append(f"{PANW_SEARCH_BASE}/resources/datasheets/{slug}")

    results = []
    for url in candidates:
        try:
            resp = client.head(url, follow_redirects=True, timeout=10)
            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "")
                if "pdf" in ct or url.endswith(".pdf"):
                    results.append({"title": query, "url": url})
        except Exception:
            continue
    return results


def _download_pdf(url: str, client: httpx.Client) -> tuple[bytes | None, str]:
    """Download PDF content. Returns (bytes, content_type)."""
    try:
        with client.stream("GET", url, follow_redirects=True) as r:
            if r.status_code != 200:
                return None, ""
            ct = r.headers.get("content-type", "")
            chunks = []
            for chunk in r.iter_bytes(8192):
                chunks.append(chunk)
            return b"".join(chunks), ct
    except Exception:
        return None, ""


def add_datasheet(filename: str, content: bytes, title: str, language: str, product: str) -> dict:
    """Admin upload: add a datasheet to RAG library (upsert by title)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest()

    # Upsert: remove existing with same title
    cache_key = title.lower()
    if cache_key in manifest["datasheets"]:
        old_file = DATA_DIR / manifest["datasheets"][cache_key].get("filename", "")
        if old_file.exists():
            old_file.unlink()

    file_hash = hashlib.md5(content).hexdigest()[:8]
    safe_name = re.sub(r"[^\w.-]", "-", filename)[:80]
    stored_name = f"{file_hash}-{safe_name}"

    (DATA_DIR / stored_name).write_bytes(content)

    entry = {
        "title": title,
        "filename": stored_name,
        "original_name": filename,
        "language": language,
        "product": product,
        "size_bytes": len(content),
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "admin_upload",
        "url": "",
    }
    manifest["datasheets"][cache_key] = entry
    _save_manifest(manifest)
    return entry


def list_all_datasheets() -> list[dict]:
    """List all datasheets in the RAG library."""
    manifest = _load_manifest()
    return list(manifest.get("datasheets", {}).values())


def remove_datasheet(title: str) -> bool:
    """Remove a datasheet by title."""
    manifest = _load_manifest()
    cache_key = title.lower()
    if cache_key not in manifest["datasheets"]:
        return False
    entry = manifest["datasheets"].pop(cache_key)
    file_path = DATA_DIR / entry.get("filename", "")
    if file_path.exists():
        file_path.unlink()
    _save_manifest(manifest)
    return True


async def handle(arguments: dict) -> str:
    """Execute the datasheet search skill."""
    query = arguments.get("query", "").strip()
    language = arguments.get("language", "any").strip().lower()
    if not query:
        return "Please provide a product name or keyword to search for datasheets."

    # Step 1: Search local RAG library first
    local_results = _search_local(query, language)
    if local_results:
        # Return best local match
        # Prefer matching language
        best = None
        for entry in local_results:
            entry_lang = entry.get("language", "en")
            if language == "any":
                best = entry
                break
            elif language == "zh" and entry_lang in ("zh-CN", "zh"):
                best = entry
                break
            elif language == "en" and entry_lang == "en":
                best = entry
                break
        if not best:
            best = local_results[0]

        lang_display = "中文" if best.get("language", "").startswith("zh") else "English"
        result = (
            f"Found datasheet in local library:\n"
            f"- Title: {best['title']}\n"
            f"- Language: {lang_display}\n"
            f"- File: {best['filename']}\n"
            f"- Download: /api/download/datasheet/{best['filename']}\n"
        )
        if best.get("url"):
            result += f"- Source: {best['url']}\n"

        # Show other available versions
        if len(local_results) > 1:
            result += "\nOther available versions:\n"
            for entry in local_results:
                if entry != best:
                    el = "中文" if entry.get("language", "").startswith("zh") else "English"
                    result += f"  - {entry['title']} ({el}): /api/download/datasheet/{entry['filename']}\n"

        return result

    # Step 2: Online search
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        results_direct = _search_panw_direct(query, language, client)
        results_google = _search_google(query, client)

        seen = set()
        all_results = []
        for r in results_direct + results_google:
            if r["url"] not in seen:
                seen.add(r["url"])
                all_results.append(r)

        downloaded = None
        lang = "en"
        for r in all_results:
            content, ct = _download_pdf(r["url"], client)
            if content and (b"%PDF" in content[:10] or "pdf" in ct):
                downloaded = (r, content)
                if "zh_CN" in r["url"] or ".cn" in r["url"]:
                    lang = "zh-CN"
                break

        if not downloaded:
            return (
                f"Could not find a downloadable datasheet for '{query}' "
                f"(language preference: {language}). "
                f"Searched local library (0 results) and {len(all_results)} online results. "
                f"Suggestions:\n"
                f"- Try a more specific product name (e.g., 'PA-5450' instead of 'PA')\n"
                f"- Ask your admin to upload the datasheet to the RAG library\n"
                f"- Check: https://www.paloaltonetworks.com/resources/datasheets"
            )

        info, content = downloaded
        file_hash = hashlib.md5(content).hexdigest()[:8]
        safe_name = re.sub(r"[^\w-]", "-", query.lower())[:60]
        filename = f"{safe_name}-{lang}-{file_hash}.pdf"

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / filename).write_bytes(content)

        entry = {
            "title": info["title"],
            "url": info["url"],
            "filename": filename,
            "language": lang,
            "product": query,
            "size_bytes": len(content),
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "online_search",
        }
        manifest = _load_manifest()
        manifest["datasheets"][query.lower()] = entry
        _save_manifest(manifest)

        lang_display = "中文" if lang == "zh-CN" else "English"
        return (
            f"Datasheet downloaded from online source:\n"
            f"- Title: {info['title']}\n"
            f"- Language: {lang_display}\n"
            f"- Size: {len(content) / 1024:.1f} KB\n"
            f"- Download: /api/download/datasheet/{filename}\n"
            f"- Source: {info['url']}"
        )
