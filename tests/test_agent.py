"""Tests for the order management agent."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import orders_db
from src.agent import OrderAgent, extract_order_id


def setup_function(_):
    orders_db.reset()


def test_extract_order_id():
    assert extract_order_id("where is my order #48291?") == "48291"
    assert extract_order_id("track order 77340 please") == "77340"
    assert extract_order_id("hello") is None


def test_track_shipped():
    r = OrderAgent().handle("where is my package #48291", "order_status",
                            {"order_action": "track"})
    assert r.status == "completed" and r.action == "track"
    assert "shipped" in r.message and "1Z999AA10123456784" in r.message


def test_track_needs_id():
    r = OrderAgent().handle("where is my package", "order_status",
                            {"order_action": "track"})
    assert r.status == "needs_input"


def test_track_unknown():
    r = OrderAgent().handle("track #99999", "order_status",
                            {"order_action": "track"})
    assert r.status == "failed"


def test_cancel_processing():
    r = OrderAgent().handle("cancel order #77340", "order_cancel",
                            {"order_action": "cancel"})
    assert r.status == "completed"
    assert orders_db.get("77340")["status"] == "cancelled"


def test_cancel_shipped_fails():
    r = OrderAgent().handle("cancel #48291", "order_cancel",
                            {"order_action": "cancel"})
    assert r.status == "failed" and "return" in r.message


def test_return_delivered():
    r = OrderAgent().handle("return #12058", "order_return",
                            {"order_action": "return"})
    assert r.status == "completed" and r.data["return_id"] == "RMA-12058"


def test_reorder():
    r = OrderAgent().handle("buy #12058 again", "order_reorder",
                            {"order_action": "reorder"})
    assert r.status == "completed" and "new order" in r.message


def test_infers_action_without_analyzer():
    r = OrderAgent().handle("where is my package #48291", "order_status", {})
    assert r.action == "track" and r.status == "completed"


def test_list_orders():
    r = OrderAgent().handle("show my orders", "order_list", {})
    assert r.status == "completed" and r.action == "list"
    assert len(r.data["orders"]) == 3


def test_estimate_delivery_shipped():
    r = OrderAgent().handle("when will #48291 arrive", "order_eta", {})
    assert r.status == "completed" and r.action == "estimate"
    assert "2026-10-14" in r.message


def test_estimate_needs_id():
    r = OrderAgent().handle("when will it arrive", "order_eta", {})
    assert r.status == "needs_input"


def test_mcp_initialize_and_list():
    from src.mcp_server import handle_rpc
    init = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {}})
    assert init["result"]["serverInfo"]["name"] == "order-management-mcp"
    tools = handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                        "params": {}})
    names = [t["name"] for t in tools["result"]["tools"]]
    assert "track_order" in names and "list_orders" in names
    assert "estimate_delivery" in names and len(names) == 8


def test_mcp_call_track():
    from src.mcp_server import handle_rpc
    resp = handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "track_order",
                                  "arguments": {"order_id": "48291"}}})
    text = resp["result"]["content"][0]["text"]
    assert "shipped" in text and resp["result"]["isError"] is False


def test_mcp_call_list():
    from src.mcp_server import handle_rpc
    resp = handle_rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "list_orders", "arguments": {}}})
    assert "3 orders" in resp["result"]["content"][0]["text"]


def test_mcp_unknown_tool():
    from src.mcp_server import handle_rpc
    resp = handle_rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                       "params": {"name": "nope", "arguments": {}}})
    assert resp["result"]["isError"] is True


def test_mcp_http_endpoint():
    from fastapi.testclient import TestClient
    from src.app import app
    with TestClient(app) as t:
        r = t.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                 "method": "tools/list", "params": {}})
        assert r.status_code == 200
        assert len(r.json()["result"]["tools"]) == 8
        r2 = t.post("/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "estimate_delivery",
                       "arguments": {"order_id": "77340"}}})
        assert "2026-10-18" in r2.json()["result"]["content"][0]["text"]
        card = t.get("/.well-known/agent-card.json").json()
        assert card["protocols"]["mcp"]["endpoint"] == "/mcp"
        assert len(card["skills"]) == 8


def test_low_confidence_analyzer_falls_back_to_text():
    # analyzer guessed "modify" @ 0.75 for a list request (outside its
    # trained labels) -> agent trusts the text instead
    r = OrderAgent().handle(
        "show me all my orders", "order_status",
        {"order_action": "modify", "choice_confidence": 0.757})
    assert r.action == "list" and r.status == "completed"


def test_high_confidence_analyzer_wins():
    r = OrderAgent().handle(
        "show me all my orders", "order_status",
        {"order_action": "track", "choice_confidence": 0.95})
    assert r.action == "track"


def test_explicit_action_without_confidence_kept():
    # MCP path: explicit action, no confidence -> used as-is
    r = OrderAgent().handle("list", "mcp", {"order_action": "list"})
    assert r.action == "list"


def test_place_order_full():
    r = OrderAgent().handle(
        "buy a wireless mouse and a webcam, ship to 123 Main St, Tampa FL",
        "order_status", {})
    assert r.status == "completed" and r.action == "place"
    assert r.data["order"]["total"] == 24.99 + 89.00
    assert "123 Main St" in r.message


def test_place_order_needs_items():
    r = OrderAgent().handle("i want to buy something", "order_status", {})
    assert r.status == "needs_input" and r.action == "place"


def test_place_order_needs_address():
    r = OrderAgent().handle("buy a webcam", "order_status", {})
    assert r.status == "needs_input" and "ship" in r.message.lower()


def test_skill_loader_hot_reload(tmp_path):
    import yaml
    from src.skills_loader import SkillLoader, SkillError
    p = tmp_path / "skills.yaml"
    p.write_text(yaml.safe_dump({"skills": [
        {"name": "track_order", "description": "t", "action": "track",
         "mcp_input": {"type": "object", "properties": {}}}]}))
    loader = SkillLoader(p)
    assert len(loader.skills()) == 1
    # add a skill on disk
    p.write_text(yaml.safe_dump({"skills": [
        {"name": "track_order", "description": "t", "action": "track",
         "mcp_input": {"type": "object", "properties": {}}},
        {"name": "list_orders", "description": "l", "action": "list",
         "mcp_input": {"type": "object", "properties": {}}}]}))
    assert loader.maybe_reload() is True
    assert len(loader.skills()) == 2
    assert loader.action_for("list_orders") == "list"
    # broken edit keeps old set
    p.write_text("skills:\n  - name: bad\n    description: x\n    action: nope\n")
    assert loader.maybe_reload() is False
    assert "unknown action" in loader.last_error
    assert len(loader.skills()) == 2
    assert loader.status()["live"] is False


def test_skill_loader_rejects_empty():
    import yaml
    from src.skills_loader import SkillLoader, SkillError
    import tempfile, pytest
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.yaml"
        p.write_text(yaml.safe_dump({"skills": []}))
        with pytest.raises(SkillError):
            SkillLoader(p)


def test_no_id_recovers_with_order_list():
    r = OrderAgent().handle("i dont have the order number",
                            "order_status", {})
    assert r.status == "needs_input" and r.action == "list"
    assert "#48291" in r.message and "Which one" in r.message


def test_need_id_offers_list_hint():
    r = OrderAgent().handle("track my order", "order_status", {})
    assert r.status == "needs_input"
    assert "list all your orders" in r.message
