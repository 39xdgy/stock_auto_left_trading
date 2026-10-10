from datetime import datetime, timedelta, timezone

from trader.data import ET, Bar


def utc(day, hhmm_et):
    """UTC bar label for an ET wall-clock time on day."""
    local = datetime.strptime(f"{day} {hhmm_et}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")


def make_bars(closes, day="2026-10-12", start="09:30", spread=0.05, highs=None, lows=None, opens=None):
    """One bar per minute from start (ET). Open = previous close unless given."""
    t0 = datetime.strptime(f"{day} {start}", "%Y-%m-%d %H:%M")
    bars = []
    for i, c in enumerate(closes):
        ts = utc(day, (t0 + timedelta(minutes=i)).strftime("%H:%M"))
        o = opens[i] if opens else (closes[i - 1] if i else c)
        h = highs[i] if highs else max(o, c) + spread
        lo = lows[i] if lows else min(o, c) - spread
        bars.append(Bar(ts, o, h, lo, c, 1000))
    return bars


def rh_rows(bars):
    """Bars back to Robinhood's historicals row format."""
    return [{"begins_at": b.ts, "open_price": str(b.open), "high_price": str(b.high), "low_price": str(b.low),
             "close_price": str(b.close), "volume": b.volume} for b in bars]
