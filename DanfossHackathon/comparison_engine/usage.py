"""
USAGE - what the AI costs. Every Claude call in the app is recorded here (tokens + estimated USD).

    record("compare", "claude-sonnet-5-5", response.usage)   -> {"usd": 0.0123, "input": ..., "output": ...}
    MeteredClient(anthropic_client, "extract")               -> same client, but every messages.create is recorded
    with collect() as calls: ...                             -> the calls made inside the block (this thread only)
    summary()                                                -> totals per purpose, for GET /api/usage

Calls are appended to data/usage.csv (env USAGE_FILE). That file is never committed.
Prices are USD per million tokens from the official price list (platform.claude.com/docs/en/about-claude/pricing,
checked 2026-11-14). Change them HERE if the price list changes. Unknown models are counted at Sonnet prices,
so the estimate is never too low.
"""
import csv
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config

#            input  output  cache write (5 min)  cache read
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),          # prompts up to 100,000 tokens
    "claude-haiku-5-5@long": (0.50, 2.50, 0.625, 0.05),     # prompts over 100,000 tokens
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
}
DEFAULT_PRICE = PRICES["claude-sonnet-5-5"]
FIELDS = ["at", "purpose", "model", "input", "output", "cache_write", "cache_read", "usd"]
_LOCK = threading.Lock()
_LOCAL = threading.local()


def usage_file() -> Path:
    return Path(os.getenv("USAGE_FILE", config.PROJECT_ROOT / "data" / "usage.csv"))


def _n(usage, name) -> int:
    value = getattr(usage, name, None) if not isinstance(usage, dict) else usage.get(name)
    return int(value or 0)


def cost(model: str, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0) -> float:
    prompt = input_tokens + cache_write + cache_read
    key = f"{model}@long" if model.startswith("claude-haiku-5-5") and prompt > 100_000 else model
    p_in, p_out, p_write, p_read = PRICES.get(key, PRICES.get(model, DEFAULT_PRICE))
    return (input_tokens * p_in + output_tokens * p_out + cache_write * p_write + cache_read * p_read) / 1_000_000


def record(purpose: str, model: str, usage) -> dict:
    """Store one call. `usage` is the API's response.usage (or a dict with the same names)."""
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "purpose": purpose, "model": model,
           "input": _n(usage, "input_tokens"), "output": _n(usage, "output_tokens"),
           "cache_write": _n(usage, "cache_creation_input_tokens"), "cache_read": _n(usage, "cache_read_input_tokens")}
    row["usd"] = round(cost(model, row["input"], row["output"], row["cache_write"], row["cache_read"]), 6)
    for calls in getattr(_LOCAL, "collectors", []):
        calls.append(row)
    try:
        path = usage_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    writer.writeheader()
                writer.writerow(row)
    except OSError:
        pass                                    # the cost log is a nice-to-have, never a reason to fail
    return row


@contextmanager
def collect():
    """Collect the calls made inside the block, e.g. to show the cost of one comparison."""
    calls: list[dict] = []
    stack = getattr(_LOCAL, "collectors", [])
    _LOCAL.collectors = stack + [calls]
    try:
        yield calls
    finally:
        _LOCAL.collectors = stack


def total(calls: list[dict]) -> dict:
    return {"calls": len(calls), "usd": round(sum(c["usd"] for c in calls), 6),
            "input": sum(c["input"] + c["cache_write"] + c["cache_read"] for c in calls),
            "output": sum(c["output"] for c in calls)}


def summary() -> dict:
    """Totals per purpose (compare, extract, ask) from data/usage.csv."""
    path = usage_file()
    rows = []
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                for k in ("input", "output", "cache_write", "cache_read"):
                    r[k] = int(r[k] or 0)
                r["usd"] = float(r["usd"] or 0)
                rows.append(r)
    purposes = sorted({r["purpose"] for r in rows})
    return {"total": total(rows), "byPurpose": {p: total([r for r in rows if r["purpose"] == p]) for p in purposes}}


class _MeteredMessages:
    def __init__(self, messages, purpose):
        self._messages, self._purpose = messages, purpose

    def create(self, **kwargs):
        response = self._messages.create(**kwargs)
        if getattr(response, "usage", None) is not None:
            record(self._purpose, kwargs.get("model", "?"), response.usage)
        return response

    def __getattr__(self, name):
        return getattr(self._messages, name)


class MeteredClient:
    """Wraps an Anthropic client so every messages.create() is recorded. Everything else passes through."""
    def __init__(self, client, purpose: str):
        self._client = client
        if hasattr(client, "messages"):          # test doubles may have no messages attribute
            self.messages = _MeteredMessages(client.messages, purpose)

    def __getattr__(self, name):
        return getattr(self._client, name)
