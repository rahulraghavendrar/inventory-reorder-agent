"""Ask the multi-agent system a question from the terminal (needs a Gemini key).

    python ask.py "What should I reorder today?"
    python ask.py "What if IT-100's lead time becomes 21 days?"
    python ask.py "Are there any data problems?"
    python ask.py "Write a summary for the purchasing team"

Shows which agent handled the request, which tools were called, and the answer.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def show(author: str, text: str) -> None:
    """Render the agent's markdown (tables, bold, lists) properly in the terminal."""
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.panel import Panel

        Console().print(Panel(Markdown(text), title=author, title_align="left", expand=True))
    except ImportError:
        print(f"\n[{author}]\n{text}\n")


async def ask(question: str) -> None:
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from agents.inventory_agent.agent import root_agent

    runner = InMemoryRunner(agent=root_agent, app_name="inventory")
    session = await runner.session_service.create_session(app_name="inventory", user_id="cli")
    message = types.Content(role="user", parts=[types.Part(text=question)])

    async for event in runner.run_async(user_id="cli", session_id=session.id, new_message=message):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.function_call:
                args = dict(part.function_call.args or {})
                print(f"  [{event.author}] -> tool {part.function_call.name}({args})")
            elif part.text and event.author != "user":
                show(event.author, part.text)

    session = await runner.session_service.get_session(app_name="inventory", user_id="cli", session_id=session.id)
    guard = session.state.get("guardrail_last_result")
    if guard:
        status = "PASSED" if guard["passed"] else f"FLAGGED {guard['unverified']}"
        print(f"Guardrail: {status} ({guard['numbers_checked']} numbers checked)")


def main() -> int:
    if not os.getenv("GOOGLE_API_KEY"):
        print("GOOGLE_API_KEY is not set. Copy .env.example to .env and add your Gemini key.")
        print("(Everything else works without a key: python run_report.py / pytest)")
        return 1
    question = " ".join(sys.argv[1:]) or "Show the full sorted list of all items"
    print(f"Q: {question}\n")
    try:
        asyncio.run(ask(question))
    except Exception as exc:  # show a short message instead of a long traceback
        text = str(exc)
        if "503" in text or "UNAVAILABLE" in text:
            print("\nGemini is overloaded right now (503). Wait a minute and retry, or set "
                  "GEMINI_MODEL=gemini-2.5-flash-lite in .env.")
        elif "429" in text or "RESOURCE_EXHAUSTED" in text:
            print("\nGemini rate limit reached (429). Wait about a minute and retry.")
        elif "API key" in text or "API_KEY" in text:
            print("\nThe Gemini API key was rejected. Check GOOGLE_API_KEY in .env.")
        else:
            raise
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
