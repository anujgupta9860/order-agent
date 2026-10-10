"""Order management A2A worker agent.

Receives tasks from the intent router via A2A dispatch:
  POST /message {"text", "intent", "context": {"analyzer": {...}}}

The analyzer's Tier-2 decisions (order_action, has_order_id, is_urgent,
...) tell the agent *what* the customer wants; this agent *does* it
against the order store and returns a result.

This is a demo worker: the order store is in-memory (src/orders_db.py).
Swap orders_db for a real orders API client to go production.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field

from . import orders_db

log = logging.getLogger(__name__)

_ORDER_ID_RE = re.compile(r"#?(\d{5,})")


def extract_order_id(text: str) -> str | None:
    m = _ORDER_ID_RE.search(text or "")
    return m.group(1) if m else None


@dataclass
class AgentResult:
    status: str  # completed | needs_input | failed
    action: str
    message: str
    data: dict = field(default_factory=dict)


class OrderAgent:
    """Handles the orders domain's six actions."""

    def handle(self, text: str, intent: str,
               analyzer: dict | None) -> AgentResult:
        analyzer = analyzer or {}
        action = analyzer.get("order_action") or self._infer_action(text)
        order_id = (extract_order_id(text)
                    or analyzer.get("order_id"))
        log.info("order agent: action=%s order_id=%s intent=%s",
                 action, order_id, intent)
        handler = {
            "track": self._track,
            "cancel": self._cancel,
            "modify": self._modify,
            "return": self._return,
            "reorder": self._reorder,
        }.get(action, self._order_info)
        return handler(text, order_id, analyzer)

    # ---------------------------------------------------------------- actions
    def _need_id(self, action: str) -> AgentResult:
        return AgentResult(
            status="needs_input", action=action,
            message="I need your order ID to do that — it looks like "
                    "#48291 (5+ digits).")

    def _track(self, text: str, order_id: str | None,
               analyzer: dict) -> AgentResult:
        if not order_id:
            return self._need_id("track")
        order = orders_db.get(order_id)
        if not order:
            return AgentResult(status="failed", action="track",
                               message=f"No order found for #{order_id}.")
        msg = (f"Order #{order_id} is {order['status']}."
               + (f" {order['carrier']} tracking {order['tracking_number']},"
                  f" arriving {order['eta']}." if order["tracking_number"]
                  else f" Expected {order['eta']}."))
        return AgentResult(status="completed", action="track",
                           message=msg, data={"order": order})

    def _cancel(self, text: str, order_id: str | None,
                analyzer: dict) -> AgentResult:
        if not order_id:
            return self._need_id("cancel")
        order = orders_db.cancel(order_id)
        if not order:
            current = orders_db.get(order_id)
            reason = ("it has already shipped — I can start a return instead."
                      if current else "I couldn't find that order.")
            return AgentResult(status="failed", action="cancel",
                               message=f"Can't cancel #{order_id}: {reason}")
        return AgentResult(status="completed", action="cancel",
                           message=f"Order #{order_id} cancelled. "
                                   f"${order['total']:.2f} will be refunded.",
                           data={"order": order})

    def _modify(self, text: str, order_id: str | None,
                analyzer: dict) -> AgentResult:
        if not order_id:
            return self._need_id("modify")
        order = orders_db.get(order_id)
        if not order:
            return AgentResult(status="failed", action="modify",
                               message=f"No order found for #{order_id}.")
        if not order["cancellable"]:
            return AgentResult(
                status="needs_input", action="modify",
                message=f"Order #{order_id} has already shipped, so I can't "
                        f"modify it. I can cancel + reorder, or start a return "
                        f"when it arrives — which would you like?")
        return AgentResult(status="completed", action="modify",
                           message=f"Order #{order_id} is still processing — "
                                   f"tell me what to change (items, quantity, "
                                   f"address) and I'll update it.",
                           data={"order": order})

    def _return(self, text: str, order_id: str | None,
                analyzer: dict) -> AgentResult:
        if not order_id:
            return self._need_id("return")
        ret = orders_db.create_return(order_id)
        if not ret:
            return AgentResult(status="failed", action="return",
                               message=f"Can't start a return for #{order_id} "
                                       f"(not found or not yet shipped).")
        return AgentResult(
            status="completed", action="return",
            message=f"Return started for order #{order_id} "
                    f"({ret['return_id']}). Label: {ret['label_url']}",
            data=ret)

    def _reorder(self, text: str, order_id: str | None,
                 analyzer: dict) -> AgentResult:
        if not order_id:
            return self._need_id("reorder")
        new = orders_db.reorder(order_id)
        if not new:
            return AgentResult(status="failed", action="reorder",
                               message=f"No order found for #{order_id}.")
        return AgentResult(
            status="completed", action="reorder",
            message=f"Reordered the items from #{order_id} as new order "
                    f"#{new['order_id']} (${new['total']:.2f}).",
            data={"order": new})

    def _order_info(self, text: str, order_id: str | None,
                    analyzer: dict) -> AgentResult:
        return AgentResult(
            status="completed", action="order_info",
            message=("I can track, cancel, modify, return, or reorder your "
                     "orders. Give me an order ID (like #48291) and tell me "
                     "what you'd like to do."))

    # -------------------------------------------------------------- internals
    @staticmethod
    def _infer_action(text: str) -> str:
        t = (text or "").lower()
        if any(k in t for k in ("track", "where is", "status", "arriv", "shipped")):
            return "track"
        if "cancel" in t:
            return "cancel"
        if any(k in t for k in ("change", "modify", "update", "edit")):
            return "modify"
        if "return" in t:
            return "return"
        if "reorder" in t or "buy again" in t or "order again" in t:
            return "reorder"
        return "order_info"


def new_task_id() -> str:
    return f"task-{uuid.uuid4().hex[:12]}"


def timed(result: AgentResult, started: float, text: str) -> dict:
    return {
        "task_id": new_task_id(),
        "status": result.status,
        "action": result.action,
        "message": result.message,
        "data": result.data,
        "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
        "ts": time.time(),
    }
