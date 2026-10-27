# Inventory Reorder Alert: multi-agent system with MCP tools

Problem B: compute each item's reorder point (daily usage × lead time + safety stock),
list items at or below it, sorted by days until stockout, and handle bad data
(zero usage, negative stock, missing lead time).

**Design principle: LLMs reason, tools calculate.**
Every number comes from deterministic Python. Gemini agents decide *which* tool to
call, explain the result and write the summary, and a guardrail checks that they
never invent a number.

## Architecture

```
                      inventory_coordinator   (Gemini, routes the request)
                     /          |            \
          reorder_analyst  data_quality_agent  report_writer
                |                 |                  |
                |      validator findings injected   |
                |      + structured JSON output      |
                +--------- MCP (FastMCP, stdio) -----+
                                  |
                  inventory_core  (pure Python, no LLM)
        validate -> step 1 -> step 2 -> step 3 -> step 4 (rank)
```

| The 4 steps | Function / MCP tool |
|---|---|
| 1. Units used while waiting for delivery | `calculate_lead_time_demand` = usage × lead time |
| 2. Reorder point | `calculate_reorder_point` = step 1 + safety stock |
| 3. Order now or not | `check_reorder_status` = on hand ≤ reorder point |
| 4. Sort by urgency | `rank_items_by_urgency` = days until stockout, then tier |

Extra tools: `analyze_inventory_file`, `get_data_quality_issues`, `get_item_details`,
`simulate_scenario` (what-if, e.g. "lead time becomes 21 days").

## Edge-case policy

A validator runs **before** any math. No row is ever dropped silently, and source
values are never overwritten.

| Case | Rule |
|---|---|
| Usage = 0 (IT-106) | Reorder point = safety stock. Days until stockout = "never" (no divide-by-zero). **Always ranked last.** Flagged as possible dead stock. |
| Negative stock (IT-113) | Value kept as −12. Treated as already out of stock: 0 days, tier OUT_OF_STOCK, ranked first, action **ORDER IMMEDIATELY** (also cover the 12 backordered units). Flagged for a recount. |
| Missing lead time (IT-118) | **Decision-robustness check, no guessing.** Solve the formula backwards: break-even lead time = (on hand − safety stock) ÷ usage = (2494 − 175) ÷ 25 = **92.8 days**. Test every lead time in the file (7, 14, 21, 30, 45): reorder needed in **0 of 5** → **ROBUST_SAFE** (2.1× margin over the worst case). Confirm the real lead time within **47.8 days**. Other verdicts: ROBUST_REORDER (order whatever the lead time) and DEPENDS_ON_LEAD_TIME (provisional reorder at the worst case, confirm today). |
| Text in a number column, negative usage / lead time / safety stock, blank values | NEEDS REVIEW, excluded from the formula, listed in exceptions with the reason. |
| Duplicate or blank item code, Excel BOM, upper-case headers, "1,200" | Handled and flagged. |
| Missing column | Clear `SchemaError` naming the column. |

Urgency tiers and actions:

| Tier | Meaning | Action |
|---|---|---|
| OUT_OF_STOCK | 0 or negative on hand | ORDER IMMEDIATELY |
| CRITICAL | days left < lead time: runs out before a delivery can arrive | ORDER TODAY |
| REORDER | at/below reorder point, safety stock covers the wait | ORDER NOW |
| WATCH | within 10% above the reorder point | No order yet, monitor |
| SAFE | comfortably above the reorder point | No need to reorder right now |
| REVIEW | data too broken to decide | Fix data first |

## Result on ex7_inventory.csv

9 items to reorder: IT-113, IT-120, IT-103, IT-121, IT-119, IT-122, IT-116, IT-111, IT-101
(1 out of stock, 6 critical, 2 reorder). Watch: IT-115, IT-100, IT-112. Safe: 13.
Exceptions: IT-106 (last in ranking), IT-113 (order immediately), IT-118 (ROBUST_SAFE).

## GenAI concepts used

| Concept | Where |
|---|---|
| Multi-agent orchestration (Google ADK) | Coordinator routes to 3 specialists via LLM-driven transfer |
| Tool use / function calling | Agents call exact tools instead of doing arithmetic |
| Model Context Protocol (FastMCP) | Tools served over MCP; any MCP client can reuse them |
| Structured output | Data Quality agent must answer in a Pydantic schema (`schemas.py`) |
| Context engineering | Validator findings are injected into the prompt via session state (`load_data_issues`) |
| Hallucination guardrail | `after_model_callback` checks every number against tool results and the user's message |
| Grounding check on IDs | Data Quality output may only mention item codes the validator flagged |
| Human in the loop | Data issues are always escalated, never auto-fixed |
| Testing LLM systems without an LLM | Scripted fake model tests routing, tools and guardrails offline |
| Robust decision analysis | Missing lead time handled by break-even + scenario testing instead of imputation |
| Security | MCP tools are read-only and can only open CSVs inside `data/` |

## Setup

Python 3.10 or newer.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Run the web app

Chat UI with verified tables, agent trace and guardrail status, plus a
**Full report (no AI)** button that works even without Gemini.

```bash
# Docker (recommended) - .env is passed at runtime, never baked into the image
docker compose up --build          # http://localhost:8080

# or locally
uvicorn web.app:app --port 8000    # http://localhost:8000
```

API: `GET /api/report` (deterministic), `POST /api/chat` `{message, session_id}`, `GET /api/health`.

## How to test

```bash
# 1. All automated tests (80), no API key needed
pytest

# 2. Deterministic report, no API key needed -> output/ folder
python run_report.py

# 3. MCP server on its own (for MCP Inspector, Claude Desktop, etc.)
python mcp_server/server.py              # stdio
python mcp_server/server.py --http 8000  # HTTP at http://127.0.0.1:8000/mcp

# 4. Agents with Gemini (needs the key)
cp .env.example .env        # then put your key in .env
python ask.py "What should I reorder today?"
python ask.py "What if IT-100's lead time becomes 21 days?"
python ask.py "Are there any data problems?"
python ask.py "Write a summary for the purchasing team"

# 5. ADK chat UI in the browser
cd agents
adk web                     # open http://localhost:8000 and pick inventory_agent
```

## Project structure

```
inventory_core/      deterministic engine (models, validation, 4 steps, pipeline, guardrail, tools)
mcp_server/          FastMCP server exposing the tools
agents/inventory_agent/   ADK agents, prompts, structured-output schema, callbacks
web/                 FastAPI backend + chat page (tables built from tool JSON, not LLM text)
tests/               80 tests: steps, edge cases, full file, MCP, guardrail, agents, web
run_report.py        CLI report (CSV, Markdown, JSON in output/)
Dockerfile, docker-compose.yml   container (non-root user, health check)
ask.py               ask the agents a question from the terminal
data/                input CSV
```

## Assumptions

- Stock position = on hand only; the file has no "quantity already on order" column.
- "At or below" the reorder point triggers a reorder (850 on hand vs 850 → reorder).
- Missing lead times are never guessed into the main result; the worst case is only used to check risk.
