"""Mock order database (in-memory). A real deployment swaps this for
an orders API / database client with the same function signatures."""
from __future__ import annotations

import copy

_INITIAL_ORDERS = {
    "48291": {
        "order_id": "48291",
        "status": "shipped",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10123456784",
        "eta": "2026-10-14",
        "items": ["Wireless mouse", "USB-C hub"],
        "total": 89.98,
        "cancellable": False,
    },
    "77340": {
        "order_id": "77340",
        "status": "processing",
        "carrier": None,
        "tracking_number": None,
        "eta": "2026-10-18",
        "items": ["Mechanical keyboard"],
        "total": 129.00,
        "cancellable": True,
    },
    "12058": {
        "order_id": "12058",
        "status": "delivered",
        "carrier": "FedEx",
        "tracking_number": "784901234567",
        "eta": "2026-10-02",
        "items": ["Monitor stand", "Desk lamp"],
        "total": 145.50,
        "cancellable": False,
    },
}

_orders: dict = copy.deepcopy(_INITIAL_ORDERS)


def reset() -> None:
    global _orders
    _orders = copy.deepcopy(_INITIAL_ORDERS)


def get(order_id: str) -> dict | None:
    return copy.deepcopy(_orders.get(order_id))


def cancel(order_id: str) -> dict | None:
    order = _orders.get(order_id)
    if order is None or not order["cancellable"]:
        return None
    order["status"] = "cancelled"
    order["cancellable"] = False
    return copy.deepcopy(order)


def create_return(order_id: str) -> dict | None:
    order = _orders.get(order_id)
    if order is None or order["status"] not in ("delivered", "shipped"):
        return None
    order["status"] = "return_initiated"
    return {"order_id": order_id, "return_id": f"RMA-{order_id}",
            "label_url": f"https://example.com/returns/RMA-{order_id}"}


def reorder(order_id: str) -> dict | None:
    order = _orders.get(order_id)
    if order is None:
        return None
    new_id = str(int(max(_orders)) + 1)
    _orders[new_id] = {
        "order_id": new_id, "status": "processing", "carrier": None,
        "tracking_number": None, "eta": "2026-10-20",
        "items": list(order["items"]), "total": order["total"],
        "cancellable": True,
    }
    return copy.deepcopy(_orders[new_id])
