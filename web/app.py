"""Web app: chat UI + JSON API for the inventory multi-agent system.

    uvicorn web.app:app --reload --port 8080      (local)
    docker compose up --build                      (container)

Endpoints
  GET  /              chat page
  GET  /api/health    status + whether a Gemini key is configured
  GET  /api/report    full deterministic report (no LLM) - works even if Gemini is down
  POST /api/chat      {message, session_id?} -> agent reply + verified tables + trace
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from inventory_core import tools as inventory_tools  # noqa: E402
from web.tables import tables_from_tool  # noqa: E402

logger = logging.getLogger("inventory_web")
STATIC = Path(__file__).resolve().parent / "static"
APP_NAME = "inventory"
USER_ID = "web"

app = FastAPI(title="Inventory Reorder Assistant", version="1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_runner = None


def get_runner():
    """Create the ADK runner once (lazy, so the page loads even without a key)."""
    global _runner
    if _runner is None:
        from google.adk.runners import InMemoryRunner

        from agents.inventory_agent.agent import root_agent

        _runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)
    return _runner


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: Optional[str] = None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "llm_configured": bool(os.getenv("GOOGLE_API_KEY")),
        "model": os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
        "use_mcp": os.getenv("USE_MCP", "true"),
    }


@app.get("/api/report")
def report():
    """Deterministic answer straight from the engine - no LLM involved."""
    full = inventory_tools.get_full_sorted_list()
    analysis = inventory_tools.analyze_inventory_file()
    if "error" in full or "error" in analysis:
        return JSONResponse({"error": full.get("error") or analysis.get("error")}, status_code=400)
    tables = tables_from_tool("get_full_sorted_list", full) + tables_from_tool("analyze_inventory_file", analysis)
    s = analysis["summary"]
    reply = (
        f"**{s['items_to_reorder']} of {s['total_items']} items need a reorder** "
        f"({s['out_of_stock']} out of stock, {s['critical']} critical, {s['reorder']} reorder). "
        f"{s['watch']} on the watch list, {s['safe']} safe. "
        f"{s['items_with_data_issues']} rows have data issues.\n\n"
        "_Calculated directly by the reorder engine (no AI)._"
    )
    return {"reply": reply, "agent": "reorder engine", "tables": tables, "steps": [], "guardrail": None}


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "503" in text or "UNAVAILABLE" in text:
        return ("Gemini is overloaded right now (503). Try again in a minute, switch "
                "GEMINI_MODEL to gemini-2.5-flash-lite, or use **Full report (no AI)**.")
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "Gemini rate limit reached (429). Wait about a minute, or use **Full report (no AI)**."
    if "API key" in text or "API_KEY" in text or "PERMISSION_DENIED" in text:
        return "The Gemini API key was rejected. Check GOOGLE_API_KEY in .env."
    return f"Something went wrong: {text[:300]}"


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not os.getenv("GOOGLE_API_KEY"):
        return JSONResponse(
            {"error": "GOOGLE_API_KEY is not set. Add it to .env, or use Full report (no AI)."},
            status_code=400,
        )

    from google.genai import types

    runner = get_runner()
    session_id = req.session_id or uuid.uuid4().hex
    session = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )
    if session is None:
        session = await runner.session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )

    steps, tables, replies = [], [], []
    message = types.Content(role="user", parts=[types.Part(text=req.message)])
    try:
        async for event in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=message):
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if part.function_call:
                    name = part.function_call.name
                    if name == "transfer_to_agent":
                        target = (part.function_call.args or {}).get("agent_name")
                        steps.append({"type": "handoff", "agent": event.author, "detail": f"-> {target}"})
                    else:
                        steps.append({"type": "tool", "agent": event.author, "detail": name,
                                      "args": dict(part.function_call.args or {})})
                elif part.function_response:
                    tables.extend(tables_from_tool(part.function_response.name, part.function_response.response))
                elif part.text and event.author != "user":
                    replies.append({"agent": event.author, "text": part.text})
    except Exception as exc:  # model overload, quota, bad key...
        logger.exception("Agent run failed")
        return JSONResponse({"session_id": session_id, "error": _friendly_error(exc), "steps": steps},
                            status_code=502)

    session = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )
    final = replies[-1] if replies else {"agent": "assistant", "text": "(no answer)"}
    return {
        "session_id": session_id,
        "agent": final["agent"],
        "reply": final["text"],
        "steps": steps,
        "tables": tables,
        "guardrail": session.state.get("guardrail_last_result") if session else None,
    }
