"""Web app: page, deterministic report, and the chat endpoint with a scripted fake LLM."""
import pytest
from fastapi.testclient import TestClient

import web.app as webapp
from agents.inventory_agent.agent import build_root_agent
from google.adk.runners import InMemoryRunner
from tests.test_agents import ScriptedLlm
from web.tables import tables_from_tool, unwrap_tool_response

client = TestClient(webapp.app)


def test_index_page_served():
    r = client.get("/")
    assert r.status_code == 200 and "Inventory Reorder Assistant" in r.text


def test_static_markdown_lib_served():
    assert client.get("/static/marked.min.js").status_code == 200


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_report_without_llm():
    data = client.get("/api/report").json()
    full = data["tables"][0]
    assert len(full["rows"]) == 25
    assert full["rows"][0][1] == "IT-113" and full["rows"][-1][1] == "IT-106"
    assert "9 of 25" in data["reply"]


def test_chat_without_key_is_clear_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 400 and "GOOGLE_API_KEY" in r.json()["error"]


@pytest.fixture
def fake_runner(monkeypatch):
    def install(script):
        llm = ScriptedLlm(script=list(script))
        runner = InMemoryRunner(agent=build_root_agent(model=llm, use_mcp=False), app_name=webapp.APP_NAME)
        monkeypatch.setattr(webapp, "_runner", runner)
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    return install


def test_chat_full_list_returns_verified_table(fake_runner):
    fake_runner([
        ("transfer_to_agent", {"agent_name": "reorder_analyst"}),
        ("get_full_sorted_list", {}),
        "Here is the full list. IT-113 is first (0 days) and IT-106 is last.",
    ])
    data = client.post("/api/chat", json={"message": "Show the full sorted list"}).json()
    assert data["agent"] == "reorder_analyst"
    assert [s["detail"] for s in data["steps"]] == ["-> reorder_analyst", "get_full_sorted_list"]
    assert len(data["tables"][0]["rows"]) == 25
    assert data["guardrail"]["passed"] is True
    # session continues
    assert data["session_id"]


def test_chat_model_error_is_friendly(monkeypatch):
    class Boom:
        session_service = InMemoryRunner(agent=build_root_agent(model=ScriptedLlm(), use_mcp=False)).session_service

        async def run_async(self, **kwargs):
            raise RuntimeError("503 UNAVAILABLE high demand")
            yield  # pragma: no cover

    monkeypatch.setattr(webapp, "_runner", Boom())
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 502 and "overloaded" in r.json()["error"]


def test_unwrap_mcp_response():
    assert unwrap_tool_response({"structuredContent": {"a": 1}, "content": [], "isError": False}) == {"a": 1}
    assert unwrap_tool_response({"result": {"a": 1}}) == {"a": 1}


def test_simulate_table():
    from inventory_core import tools
    t = tables_from_tool("simulate_scenario", tools.simulate_scenario("IT-100", lead_time_days=21))[0]
    assert ["Reorder point", 850, 1200] in t["rows"]
