"""Bars and time helpers. Accepts Robinhood get_equity_historicals output."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Bar:
    ts: str  # UTC, left edge of the bar, e.g. "2026-10-09T13:54:00Z"
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def parse_ts(ts):
    """UTC timestamp; fractional seconds (Robinhood sends nanoseconds) are dropped."""
    return datetime.strptime(ts.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def et_day(ts):
    return parse_ts(ts).astimezone(ET).strftime("%Y-%m-%d")


def et_hhmm(ts):
    return parse_ts(ts).astimezone(ET).strftime("%H:%M")


def bars_from_robinhood(payload):
    """{symbol: [Bar]} from a get_equity_historicals response (dict or JSON string).

    Interpolated bars are gap fill with no information and are dropped.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    out = {}
    for result in payload["data"]["results"]:
        bars = out.setdefault(result["symbol"], {})
        for b in result["bars"]:
            if b.get("interpolated"):
                continue
            bars[b["begins_at"]] = Bar(b["begins_at"], float(b["open_price"]), float(b["high_price"]),
                                       float(b["low_price"]), float(b["close_price"]), float(b.get("volume") or 0))
    return {s: [bars[t] for t in sorted(bars)] for s, bars in out.items()}


def load_files(paths):
    """Merge several saved get_equity_historicals responses into {symbol: [Bar]}."""
    merged = {}
    for p in paths:
        for sym, bars in bars_from_robinhood(Path(p).read_text()).items():
            merged.setdefault(sym, {}).update({b.ts: b for b in bars})
    return {s: [d[t] for t in sorted(d)] for s, d in merged.items()}
