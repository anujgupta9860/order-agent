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
        action = analyzer.get("order_action")
        conf = analyzer.get("choice_confidence")
        if action and conf is not None and conf < 0.8:
            # Analyzer is unsure — possibly a request outside its trained
            # labels (e.g. list/estimate). Trust the message text instead.
            log.info("order agent: analyzer unsure (%s @ %.2f); "
                     "falling back to text inference", action, conf)
            action = self._infer_action(text)
        action = action or self._infer_action(text)
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
            "list": self._list,
            "estimate": self._estimate,
            "place": self._place,
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
            message=("I can place new orders, track, cancel, modify, return, "
                     "or reorder. To buy something, tell me what you want "
                     "and where to ship it — e.g. 'buy a wireless mouse, "
                     "ship to 123 Main St, Tampa FL'."))

    def _list(self, text: str, order_id: str | None,
              analyzer: dict) -> AgentResult:
        orders = orders_db.list_all()
        lines = [f"#{o['order_id']}: {o['status']} "
                 f"(${o['total']:.2f})" for o in orders]
        return AgentResult(
            status="completed", action="list",
            message=f"You have {len(orders)} orders:\n" + "\n".join(lines),
            data={"orders": orders})

    def _estimate(self, text: str, order_id: str | None,
                  analyzer: dict) -> AgentResult:
        if not order_id:
            return self._need_id("estimate")
        order = orders_db.get(order_id)
        if not order:
            return AgentResult(status="failed", action="estimate",
                               message=f"No order found for #{order_id}.")
        if order["status"] == "delivered":
            msg = f"Order #{order_id} was already delivered."
        elif order["tracking_number"]:
            msg = (f"Order #{order_id} is {order['status']} via "
                   f"{order['carrier']} — estimated delivery {order['eta']}.")
        else:
            msg = (f"Order #{order_id} is still {order['status']} — "
                   f"estimated delivery {order['eta']} once it ships.")
        return AgentResult(status="completed", action="estimate",
                           message=msg, data={"order": order})

    def _place(self, text: str, order_id: str | None,
               analyzer: dict) -> AgentResult:
        items = self._extract_items(text)
        address = self._extract_address(text)
        if not items:
            return AgentResult(
                status="needs_input", action="place",
                message="What would you like to order? I have: " +
                        ", ".join(sorted(orders_db.CATALOG)) + ".")
        if not address:
            return AgentResult(
                status="needs_input", action="place",
                message=f"Got it — {', '.join(items)}. "
                        f"Where should I ship it? (e.g. 'ship to 123 Main St, "
                        f"Tampa FL 33601')")
        order = orders_db.place(items, address)
        return AgentResult(
            status="completed", action="place",
            message=(f"Order #{order['order_id']} placed! "
                     f"{', '.join(items)} (${order['total']:.2f}) "
                     f"shipping to {address}. Expected {order['eta']}."),
            data={"order": order})

    # -------------------------------------------------------------- internals
    @staticmethod
    def _extract_items(text: str) -> list[str]:
        t = (text or "").lower()
        return [name for name in orders_db.CATALOG if name in t]

    @staticmethod
    def _extract_address(text: str) -> str | None:
        m = re.search(r"(?:ship to|deliver to|address:?)\s+(.+)",
                      text or "", re.IGNORECASE)
        return m.group(1).strip().rstrip(".") if m else None

    @staticmethod
    def _infer_action(text: str) -> str:
        t = (text or "").lower()
        if "reorder" in t or "buy again" in t or "order again" in t:
            return "reorder"
        if any(k in t for k in ("place an order", "place order", "buy ",
                                "purchase", "order a ", "i want a ")):
            return "place"
        if any(k in t for k in ("list", "my orders", "order history",
                                "all orders")):
            return "list"
        if any(k in t for k in ("when will", "eta", "estimate",
                                "delivery date")):
            return "estimate"
        if any(k in t for k in ("track", "where is", "status", "arriv",
                                "shipped")):
            return "track"
        if "cancel" in t:
            return "cancel"
        if any(k in t for k in ("change", "modify", "update", "edit")):
            return "modify"
        if "return" in t:
            return "return"
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
