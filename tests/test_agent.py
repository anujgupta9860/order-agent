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
