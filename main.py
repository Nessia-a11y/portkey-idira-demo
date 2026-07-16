"""PANW Product Helper — FastAPI app with skill-based tool system.

Skills:
  1. search_datasheet — download datasheets from PANW (zh-CN priority)
  2. query_internal_demos — internal G-Drive links (admin-maintained)
  3. query_external_demos — public demo files (bundled in Docker)
  4. query_sku — SKU calculation (internal only)
  5. query_techdocs — official TechDocs + internal deployment docs
  6. mcp_extension — reserved MCP slot for future tools
"""

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from portkey_ai import Portkey
from pydantic import BaseModel

from mcp_client import MCPManager
from system_prompt import SYSTEM_PROMPT
from skills import skill_datasheet, skill_internal_demos, skill_external_demos, skill_sku, skill_techdocs, skill_mcp_reserved, skill_translate, skill_context7
import auth

load_dotenv()

MODEL = os.getenv("MODEL", "claude-opus-4-8")
PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY")
PORTKEY_VIRTUAL_KEY = os.getenv("PORTKEY_VIRTUAL_KEY")
PORTKEY_CONFIG = os.getenv("PORTKEY_CONFIG")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme")

if not PORTKEY_API_KEY:
    raise RuntimeError("PORTKEY_API_KEY is not set. Copy .env.example to .env and fill it in.")

mcp = MCPManager()

# --- Skill Registry ---
SKILLS = {
    "search_datasheet": skill_datasheet,
    "query_internal_demos": skill_internal_demos,
    "query_external_demos": skill_external_demos,
    "query_sku": skill_sku,
    "query_techdocs": skill_techdocs,
    "translate_slide": skill_translate,
    "query_context7": skill_context7,
    "mcp_extension": skill_mcp_reserved,
}

SKILL_TOOLS = [skill.TOOL_DEFINITION for skill in SKILLS.values()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.init_db()
    await mcp.start()
    yield
    await mcp.stop()


app = FastAPI(title="PANW Product Helper", lifespan=lifespan)


def make_client() -> Portkey:
    kwargs = {"api_key": PORTKEY_API_KEY}
    if PORTKEY_VIRTUAL_KEY:
        kwargs["virtual_key"] = PORTKEY_VIRTUAL_KEY
    if PORTKEY_CONFIG:
        kwargs["config"] = PORTKEY_CONFIG
    return Portkey(**kwargs)


# --- Auth helpers ---

def _client_ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host if request.client else "")


def _get_session_email(request: Request) -> Optional[str]:
    token = request.cookies.get("session") or request.headers.get("x-session-token")
    if not token:
        return None
    return auth.validate_session(token)


def _require_auth(request: Request) -> str:
    email = _get_session_email(request)
    if not email:
        raise HTTPException(401, "Not authenticated")
    return email


# --- Auth Endpoints ---

class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str



@app.post("/auth/register")
async def auth_register(req: RegisterRequest, request: Request):
    ok, msg = auth.register_user(req.email, req.password)
    if not ok:
        raise HTTPException(400, msg)
    auth.log_login(req.email, "register", _client_ip(request), request.headers.get("user-agent", ""), success=True)
    return {"status": "ok", "message": "Registration successful. You can now log in."}


MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 900  # 15 minutes


@app.post("/auth/login")
async def auth_login(req: LoginRequest, request: Request):
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")
    email = req.email.strip().lower()

    if auth.count_recent_failures(email, "login_password_fail", LOGIN_LOCKOUT_SECONDS) >= MAX_LOGIN_ATTEMPTS:
        raise HTTPException(429, "Too many failed attempts. Please try again later.")

    if not auth.verify_password(req.email, req.password):
        auth.log_login(req.email, "login_password_fail", ip, ua, success=False)
        raise HTTPException(401, "Invalid email or password")

    token = auth.create_session(email, ip, ua)
    auth.log_login(email, "login_success", ip, ua, success=True)

    response = JSONResponse({"status": "ok", "email": email})
    response.set_cookie("session", token, httponly=True, secure=True, samesite="lax", max_age=auth.SESSION_TTL)
    return response


@app.post("/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get("session") or request.headers.get("x-session-token")
    if token:
        auth.revoke_session(token)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("session")
    return response


@app.get("/auth/me")
async def auth_me(request: Request):
    email = _get_session_email(request)
    if not email:
        raise HTTPException(401, "Not authenticated")
    return {"email": email}


# --- Models ---

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    user_type: str = "external"  # "internal" or "external"


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict] = []


def _get_tools_for_user(user_type: str) -> list[dict]:
    """Return tools available based on user type."""
    all_tools = list(SKILL_TOOLS)

    if user_type == "external":
        # External users cannot access internal demos, SKU, or full techdocs
        blocked = {"query_internal_demos", "query_sku"}
        all_tools = [t for t in all_tools if t["function"]["name"] not in blocked]

    # Add MCP tools if any
    if mcp.tools:
        all_tools.extend(mcp.tools)

    return all_tools if all_tools else None


async def _dispatch_tool_call(name: str, arguments: dict) -> str:
    """Route a tool call to the appropriate skill or MCP server."""
    if name in SKILLS:
        return await SKILLS[name].handle(arguments)

    # Fall through to MCP
    if mcp.tools:
        try:
            return await mcp.call_tool(name, arguments)
        except Exception as e:
            return f"MCP tool error: {e}"

    return f"Unknown tool: {name}"


async def run_agent_loop(client: Portkey, messages: list[dict], user_type: str, max_iterations: int = 10) -> tuple[str, list[dict]]:
    """Run the tool-calling loop until the model produces a final text answer."""
    tool_trace: list[dict] = []
    tools = _get_tools_for_user(user_type)

    for _ in range(max_iterations):
        kwargs = {"model": MODEL, "messages": messages, "max_tokens": 4096}
        if tools:
            kwargs["tools"] = tools

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        assistant_entry = {"role": "assistant", "content": msg.content or ""}
        if getattr(msg, "tool_calls", None):
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if not getattr(msg, "tool_calls", None):
            return msg.content or "", tool_trace

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            result = await _dispatch_tool_call(name, args)
            tool_trace.append({"name": name, "arguments": args, "result": result[:500]})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )

    return "(max iterations reached)", tool_trace


def _build_system_prompt(user_type: str) -> str:
    """Add user-type context to the system prompt."""
    extra = ""
    if user_type == "internal":
        extra = (
            "\n\nThe current user is a PANW INTERNAL employee. "
            "You can provide internal resources: G-Drive demo links, SKU calculations, "
            "and internal deployment documentation. Use the appropriate tools."
        )
    else:
        extra = (
            "\n\nThe current user is EXTERNAL (customer/partner). "
            "Only provide public resources. Do NOT share internal G-Drive links or SKU details. "
            "Use query_external_demos for demo materials and query_techdocs for documentation."
        )
    return SYSTEM_PROMPT + extra


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    _require_auth(request)
    if not req.messages:
        raise HTTPException(400, "messages cannot be empty")

    system = _build_system_prompt(req.user_type)
    convo = [{"role": "system", "content": system}] + [
        m.model_dump() for m in req.messages
    ]

    try:
        client = make_client()
        reply, trace = await run_agent_loop(client, convo, req.user_type)
        return ChatResponse(reply=reply, tool_calls=trace)
    except Exception as e:
        raise HTTPException(500, f"agent error: {e}")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model": MODEL,
        "skills": list(SKILLS.keys()),
        "mcp_tools": len(mcp.tools),
    }


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


# --- File Upload (Chat) ---

UPLOAD_DIR = Path(__file__).parent / "data" / "uploads"


@app.post("/api/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Upload a file for translation or other processing."""
    _require_auth(request)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    filename = file.filename or "upload"
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")

    content = await file.read()
    safe_name = filename
    (UPLOAD_DIR / safe_name).write_bytes(content)

    return {"status": "ok", "filename": safe_name, "size": len(content)}


@app.post("/api/translate")
async def translate_file(request: Request, filename: str = Form(...), target_language: str = Form(...), translations_json: str = Form(...)):
    """Apply translations to an uploaded file and return the translated version."""
    _require_auth(request)

    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(404, "File not found. Please upload it first.")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")

    try:
        translations = json.loads(translations_json)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid translations_json")

    ext = file_path.suffix.lower()
    output_dir = Path(__file__).parent / "data" / "translations" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_name = f"{target_language}-{filename}"
    output_path = output_dir / output_name

    if ext == ".pptx":
        skill_translate.translate_pptx(file_path, translations, output_path)
    elif ext == ".docx":
        skill_translate.translate_docx(file_path, translations, output_path)
    elif ext == ".pdf":
        skill_translate.build_translated_pdf(file_path, translations, output_path)
        if not output_path.exists():
            output_path = output_path.with_suffix(".txt")
            output_name = output_name.replace(".pdf", ".txt")
    else:
        raise HTTPException(400, f"Unsupported format: {ext}")

    if not output_path.exists():
        raise HTTPException(500, "Translation output failed")

    return {"status": "ok", "download_url": f"/api/download/translated/{output_name}"}


@app.get("/api/download/translated/{filename}")
async def download_translated(filename: str, request: Request):
    """Download a translated file."""
    _require_auth(request)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    output_dir = Path(__file__).parent / "data" / "translations" / "output"
    path = (output_dir / filename).resolve()
    if not path.is_relative_to(output_dir.resolve()):
        raise HTTPException(400, "Invalid path")
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=filename)


# --- Download APIs ---

@app.get("/api/download/datasheet/{filename}")
async def download_datasheet(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    path = (skill_datasheet.DATA_DIR / filename).resolve()
    if not path.is_relative_to(skill_datasheet.DATA_DIR.resolve()):
        raise HTTPException(400, "Invalid path")
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Datasheet not found")
    return FileResponse(path, filename=filename, media_type="application/pdf")


@app.get("/api/download/external/{stored_name}")
async def download_external(stored_name: str):
    if "/" in stored_name or "\\" in stored_name or ".." in stored_name:
        raise HTTPException(400, "Invalid filename")
    path = (skill_external_demos.DATA_DIR / stored_name).resolve()
    if not path.is_relative_to(skill_external_demos.DATA_DIR.resolve()):
        raise HTTPException(400, "Invalid path")
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=stored_name)


# --- Admin APIs ---

def _check_admin(token: str):
    """Legacy token check — still accepted as fallback for API scripts."""
    if token != ADMIN_TOKEN:
        raise HTTPException(403, "Invalid admin token")


def _require_admin(request: Request):
    """Require logged-in admin user via session cookie."""
    email = _get_session_email(request)
    if not email:
        raise HTTPException(401, "Not authenticated")
    if not auth.is_admin(email):
        raise HTTPException(403, "Admin access required")
    return email


# Admin: Datasheets (Skill 1)
@app.post("/admin/datasheets")
async def admin_add_datasheet(
    file: UploadFile = File(...),
    title: str = Form(...),
    language: str = Form("en"),
    product: str = Form(...),
    token: str = Form(...),
):
    _check_admin(token)
    content = await file.read()
    entry = skill_datasheet.add_datasheet(file.filename, content, title, language, product)
    return {"status": "ok", "entry": entry}


@app.get("/admin/datasheets")
async def admin_list_datasheets(token: str = Query(...)):
    _check_admin(token)
    return {"datasheets": skill_datasheet.list_all_datasheets()}


@app.delete("/admin/datasheets/{title}")
async def admin_remove_datasheet(title: str, token: str = Query(...)):
    _check_admin(token)
    if not skill_datasheet.remove_datasheet(title):
        raise HTTPException(404, "Datasheet not found")
    return {"status": "deleted"}


# Admin: Internal Demos (Skill 2)
@app.post("/admin/internal-demos")
async def admin_add_internal_demo(
    title: str = Form(...),
    gdrive_url: str = Form(...),
    resource_type: str = Form("video"),
    product: str = Form(...),
    description: str = Form(""),
    token: str = Form(...),
):
    _check_admin(token)
    entry = skill_internal_demos.add_demo(title, gdrive_url, resource_type, product, description)
    return {"status": "ok", "entry": entry}


@app.delete("/admin/internal-demos/{title}")
async def admin_remove_internal_demo(title: str, token: str = Query(...)):
    _check_admin(token)
    if not skill_internal_demos.remove_demo(title):
        raise HTTPException(404, "Demo not found")
    return {"status": "deleted"}


@app.get("/admin/internal-demos")
async def admin_list_internal_demos(token: str = Query(...)):
    _check_admin(token)
    return {"demos": skill_internal_demos.list_all_demos()}


# Admin: External Demos (Skill 3)
@app.post("/admin/external-demos")
async def admin_add_external_demo(
    file: UploadFile = File(...),
    resource_type: str = Form("video"),
    product: str = Form(...),
    description: str = Form(""),
    token: str = Form(...),
):
    _check_admin(token)
    content = await file.read()
    try:
        entry = skill_external_demos.add_file(file.filename, content, resource_type, product, description)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "ok", "file": entry}


@app.delete("/admin/external-demos/{stored_name}")
async def admin_remove_external_demo(stored_name: str, token: str = Query(...)):
    _check_admin(token)
    if not skill_external_demos.remove_file(stored_name):
        raise HTTPException(404, "File not found")
    return {"status": "deleted"}


@app.get("/admin/external-demos")
async def admin_list_external_demos(token: str = Query(...)):
    _check_admin(token)
    return {"files": skill_external_demos.list_all_files()}


# Admin: SKU Rules (Skill 4) — structured data
@app.post("/admin/sku")
async def admin_add_sku(
    product: str = Form(...),
    skus_json: str = Form(...),
    calculation_notes: str = Form(""),
    token: str = Form(...),
):
    """Add SKU info. skus_json is a JSON array like [{"sku":"PAN-XX","description":"...","notes":"..."}]"""
    _check_admin(token)
    try:
        skus = json.loads(skus_json)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid skus_json format")
    result = skill_sku.add_product_sku(product, skus, calculation_notes)
    return {"status": "ok", "result": result}


# Admin: SKU file upload (doc/slide/PDF)
@app.post("/admin/sku/upload")
async def admin_upload_sku_file(
    file: UploadFile = File(...),
    product: str = Form(...),
    description: str = Form(""),
    token: str = Form(...),
):
    _check_admin(token)
    content = await file.read()
    entry = skill_sku.add_sku_file(file.filename, content, product, description)
    return {"status": "ok", "file": {"original_name": entry["original_name"], "product": entry["product"]}}


@app.get("/admin/sku")
async def admin_list_sku(token: str = Query(...)):
    _check_admin(token)
    return {"products": skill_sku.list_products(), "files": skill_sku.list_files()}


@app.delete("/admin/sku/file/{stored_name}")
async def admin_remove_sku_file(stored_name: str, token: str = Query(...)):
    _check_admin(token)
    if not skill_sku.remove_file(stored_name):
        raise HTTPException(404, "File not found")
    return {"status": "deleted"}


# Admin: TechDocs (Skill 5) — text entry
@app.post("/admin/techdocs")
async def admin_add_techdoc(
    title: str = Form(...),
    content: str = Form(...),
    product: str = Form(...),
    tags: str = Form(""),
    token: str = Form(...),
):
    _check_admin(token)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    entry = skill_techdocs.add_internal_doc(title, content, product, tag_list)
    return {"status": "ok", "entry": {"title": entry["title"], "product": entry["product"]}}


# Admin: TechDocs file upload (datasheet/doc/PDF)
@app.post("/admin/techdocs/upload")
async def admin_upload_techdoc_file(
    file: UploadFile = File(...),
    title: str = Form(...),
    product: str = Form(...),
    description: str = Form(""),
    token: str = Form(...),
):
    _check_admin(token)
    content = await file.read()
    entry = skill_techdocs.add_techdoc_file(file.filename, content, title, product, description)
    return {"status": "ok", "file": {"title": entry["title"], "product": entry["product"]}}


@app.get("/admin/techdocs/files")
async def admin_list_techdoc_files(token: str = Query(...)):
    _check_admin(token)
    return {"files": skill_techdocs.list_techdoc_files()}


@app.delete("/admin/techdocs/file/{stored_name}")
async def admin_remove_techdoc_file(stored_name: str, token: str = Query(...)):
    _check_admin(token)
    if not skill_techdocs.remove_techdoc_file(stored_name):
        raise HTTPException(404, "File not found")
    return {"status": "deleted"}


@app.delete("/admin/techdocs/{title}")
async def admin_remove_techdoc(title: str, token: str = Query(...)):
    _check_admin(token)
    if not skill_techdocs.remove_internal_doc(title):
        raise HTTPException(404, "Document not found")
    return {"status": "deleted"}


# Admin: Check if current user is admin
@app.get("/admin/check")
async def admin_check(request: Request):
    email = _get_session_email(request)
    if not email:
        raise HTTPException(401, "Not authenticated")
    return {"is_admin": auth.is_admin(email), "email": email}


# Admin: Domain Management
@app.get("/admin/domains")
async def admin_list_domains(request: Request):
    _require_admin(request)
    return {"domains": auth.list_domains()}


@app.post("/admin/domains")
async def admin_add_domain(request: Request, domain: str = Form(...)):
    _require_admin(request)
    auth.add_domain(domain)
    return {"status": "ok"}


@app.delete("/admin/domains/{domain}")
async def admin_remove_domain(domain: str, request: Request):
    _require_admin(request)
    if not auth.remove_domain(domain):
        raise HTTPException(404, "Domain not found")
    return {"status": "deleted"}


# Admin: User Management
@app.get("/admin/users")
async def admin_list_users(request: Request):
    _require_admin(request)
    return {"users": auth.list_users()}


@app.post("/admin/users/toggle")
async def admin_toggle_user(request: Request, email: str = Form(...), active: str = Form(...)):
    _require_admin(request)
    auth.set_user_active(email, active == "1")
    return {"status": "ok"}


@app.post("/admin/users/set-admin")
async def admin_set_admin(request: Request, email: str = Form(...), admin: str = Form(...)):
    actor = _require_admin(request)
    if email.lower() == actor.lower():
        raise HTTPException(400, "Cannot change your own admin status")
    auth.set_admin(email, admin == "1")
    return {"status": "ok"}


# Admin: Login Activity
@app.get("/admin/login-logs")
async def admin_login_logs(request: Request, limit: int = Query(100)):
    _require_admin(request)
    return {"logs": auth.get_login_logs(limit)}


@app.get("/admin/active-sessions")
async def admin_active_sessions(request: Request):
    _require_admin(request)
    return {"sessions": auth.get_active_sessions()}


# --- Static files (must be last) ---
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
