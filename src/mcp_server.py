"""Mock MCP server for the order management agent.

Exposes the agent's skills as MCP tools over streamable HTTP
(POST /mcp, JSON-RPC 2.0). Minimal implementation: initialize,
tools/list, tools/call. Enough for any MCP client (Claude, Cursor,
Muse custom connectors) to discover and call the tools.

This is a *mock*: tools run against the in-memory order store.
"""
from __future__ import annotations

import logging

from .agent import OrderAgent

log = logging.getLogger(__name__)

SERVER_INFO = {"name": "order-management-mcp", "version": "0.1.0"}

TOOLS = [
    {
        "name": "track_order",
        "description": "Look up shipment status, carrier, tracking number "
                       "and ETA for an order.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string",
                             "description": "Order ID, e.g. 48291"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancel an order that is still processing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "modify_order",
        "description": "Request a change (items, quantity, address) to a "
                       "processing order.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "change": {"type": "string",
                           "description": "What to change"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "return_order",
        "description": "Start a return for a shipped/delivered order; "
                       "returns an RMA label URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "reorder",
        "description": "Re-buy all items of a previous order as a new order.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "list_orders",
        "description": "List all orders with their statuses and totals.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "estimate_delivery",
        "description": "Estimate the delivery date of an order.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "place_order",
        "description": "Place a brand-new order. Items must be from the "
                       "catalog: wireless mouse, usb-c hub, mechanical "
                       "keyboard, monitor stand, desk lamp, webcam.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"},
                          "description": "Catalog item names"},
                "address": {"type": "string",
                            "description": "Shipping address"}},
            "required": ["items", "address"],
        },
    },
]

# tool name -> (agent action, text template)
_TOOL_ACTIONS = {
    "track_order": "track",
    "cancel_order": "cancel",
    "modify_order": "modify",
    "return_order": "return",
    "reorder": "reorder",
    "list_orders": "list",
    "estimate_delivery": "estimate",
    "place_order": "place",
}

_agent = OrderAgent()
_loader = None  # set via set_skill_loader() — skills hot-reload from YAML


def set_skill_loader(loader) -> None:
    global _loader
    _loader = loader


def _tools() -> list[dict]:
    if _loader is not None:
        return _loader.mcp_tools()
    return TOOLS


def _action_for(name: str) -> str | None:
    if _loader is not None:
        return _loader.action_for(name)
    return _TOOL_ACTIONS.get(name)


def _call_tool(name: str, args: dict) -> dict:
    action = _action_for(name)
    if action is None:
        return {"isError": True,
                "content": [{"type": "text",
                             "text": f"unknown tool: {name}"}]}
    order_id = args.get("order_id")
    if name == "place_order":
        items = args.get("items", [])
        address = args.get("address", "")
        text = f"place order {' + '.join(items)} ship to {address}"
        result = _agent.handle(text, "mcp", {"order_action": "place"})
    else:
        text = f"{action} #{order_id}" if order_id else action
        if name == "modify_order" and args.get("change"):
            text += f": {args['change']}"
        result = _agent.handle(text, "mcp",
                               {"order_action": action} if action in (
                                   "track", "cancel", "modify", "return",
                                   "reorder") else {})
    log.info("mcp tool=%s status=%s", name, result.status)
    return {
        "content": [{"type": "text", "text": result.message}],
        "isError": result.status == "failed",
    }


def handle_rpc(payload: dict) -> dict | None:
    """Handle one JSON-RPC 2.0 request. Returns None for notifications."""
    method = payload.get("method", "")
    req_id = payload.get("id")
    params = payload.get("params", {}) or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })
    if method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return ok({"tools": _tools()})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        return ok(_call_tool(name, args))
    return err(-32601, f"method not found: {method}")
