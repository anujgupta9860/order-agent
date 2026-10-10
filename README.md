# Order Management Agent (A2A worker)

A2A worker agent for the orders domain. Receives tasks from the intent
router, uses the intent analyzer's Tier-2 decisions to pick the action,
and executes it against the order store.

```
POST /message {"text", "intent", "context": {"analyzer": {...}}}
  -> {"task_id", "status", "action", "message", "data"}
```

Actions: `track` `cancel` `modify` `return` `reorder` (+ `order_info`).
The store is in-memory (`src/orders_db.py`) — swap it for a real orders
API to go production.

Run locally: `uvicorn src.app:app --port 8082`
Test: `pytest tests/ -q`
