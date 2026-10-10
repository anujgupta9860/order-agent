"""Order management A2A worker agent (FastAPI).

A2A surface:
  GET  /.well-known/agent-card.json - agent card (name, skills, endpoints)
  POST /message - {"text", "intent", "context": {"analyzer": {...}}}
                  -> {"task_id", "status", "action", "message", "data"}
  GET  /health  - liveness

The router's A2AClient.dispatch POSTs here when the SDK is unavailable
(HTTP fallback). The analyzer's Tier-2 decisions arrive in
context.analyzer and drive which action runs.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .agent import OrderAgent, timed
from .mcp_server import SERVER_INFO, handle_rpc, set_skill_loader
from .skills_loader import SkillLoader

REPO_ROOT = Path(__file__).resolve().parent.parent
skill_loader = SkillLoader(REPO_ROOT / "skills" / "skills.yaml")
set_skill_loader(skill_loader)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Order Management Agent (A2A worker)", version="0.1.0")
agent = OrderAgent()

def build_agent_card() -> dict:
    """Agent card with hot-reloaded skills (rebuilt on every fetch)."""
    return {
        "name": "order-management-agent",
        "description": "Handles order actions. Consumes intent-analyzer "
                       "Tier-2 decisions.",
        "version": "0.1.0",
        "protocol": "a2a-http",
        "endpoints": {"message": "/message"},
        "skills": skill_loader.card_skills(),
        "protocols": {
            "a2a": {"message": "/message"},
            "mcp": {"endpoint": "/mcp", "transport": "streamable-http",
                    "server": SERVER_INFO["name"]},
        },
        "input": {"text": "customer message", "intent": "router intent",
                  "context": {"analyzer": "Tier-2 analyzer decisions"}},
    }


@app.get("/.well-known/agent-card.json")
def agent_card():
    return build_agent_card()


@app.get("/health")
def health():
    return {"status": "ok", "agent": "order-management-agent",
            "skills": [s["name"] for s in skill_loader.card_skills()]}


@app.get("/skills/status")
def skills_status():
    """Hot-reload status for skills.yaml."""
    return skill_loader.status()


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    intent: str = ""
    context: dict = Field(default_factory=dict)


@app.post("/message")
def message(req: MessageRequest):
    started = time.monotonic()
    analyzer = (req.context or {}).get("analyzer") or {}
    result = agent.handle(req.text.strip(), req.intent.strip(), analyzer)
    out = timed(result, started, req.text.strip())
    log.info("task=%s action=%s status=%s", out["task_id"],
             result.action, result.status)
    return out


@app.post("/mcp")
def mcp(payload: dict):
    """Mock MCP server (streamable HTTP, JSON-RPC 2.0).

    Methods: initialize, tools/list, tools/call.
    """
    if not isinstance(payload, dict) or "method" not in payload:
        return {"jsonrpc": "2.0", "id": payload.get("id"),
                "error": {"code": -32600, "message": "invalid request"}}
    resp = handle_rpc(payload)
    return resp if resp is not None else {"jsonrpc": "2.0",
                                         "id": payload.get("id"),
                                         "result": {}}
