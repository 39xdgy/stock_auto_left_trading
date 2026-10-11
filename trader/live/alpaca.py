"""1-minute bars from Alpaca's market data API (stdlib only).

Used only by the watcher as a cheap pre-filter. Orders and the final signal check still use
Robinhood data: the free IEX feed is a small slice of the market, so its bars differ from
Robinhood's (and some minutes have no IEX trades at all).

Keys come from the environment, never from files in this repo:
  APCA_API_KEY_ID, APCA_API_SECRET_KEY
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from ..data import ET, Bar, parse_ts

BASE_URL = "https://data.alpaca.markets/v2/stocks/bars"


class AlpacaError(Exception):
    pass


def _headers():
    key, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise AlpacaError("APCA_API_KEY_ID / APCA_API_SECRET_KEY are not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"}


def _get(params, timeout):
    req = urllib.request.Request(BASE_URL + "?" + urllib.parse.urlencode(params), headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # never echo the request headers: they carry the keys
        raise AlpacaError(f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AlpacaError(f"network: {e}") from None


def fetch_bars(symbols, start, end=None, feed="iex", timeout=15):
    """{symbol: [Bar]} for 1-minute bars with start <= bar < end (UTC datetimes), all pages."""
    params = {"symbols": ",".join(symbols), "timeframe": "1Min", "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "feed": feed, "adjustment": "raw", "limit": 10000, "sort": "asc"}
    if end is not None:
        params["end"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {s: [] for s in symbols}
    while True:
        payload = _get(params, timeout)
        for sym, rows in (payload.get("bars") or {}).items():
            out.setdefault(sym, []).extend(
                Bar(r["t"].replace("+00:00", "Z"), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]),
                    float(r.get("v") or 0)) for r in rows)
        token = payload.get("next_page_token")
        if not token:
            return out
        params["page_token"] = token


def regular_hours(bars):
    """Keep bars whose minute starts between 09:30 and 15:59 ET."""
    return [b for b in bars if "09:30" <= parse_ts(b.ts).astimezone(ET).strftime("%H:%M") <= "15:59"]


def fill_gaps(bars, until=None):
    """Insert a flat bar (open=high=low=close=previous close, volume 0) for each regular-hours minute
    with no IEX trade, within each trading day. until (UTC datetime) extends the last day up to the
    bar just before it, so a quiet final minute still counts as completed."""
    out = []
    for b in bars:
        if out:
            prev = parse_ts(out[-1].ts)
            t = prev + timedelta(minutes=1)
            same_day = prev.astimezone(ET).date() == parse_ts(b.ts).astimezone(ET).date()
            while same_day and t < parse_ts(b.ts):
                out.append(_flat(t, out[-1].close))
                t += timedelta(minutes=1)
        out.append(b)
    if out and until is not None:
        t = parse_ts(out[-1].ts) + timedelta(minutes=1)
        last_day = parse_ts(out[-1].ts).astimezone(ET).date()
        while t + timedelta(minutes=1) <= until and t.astimezone(ET).date() == last_day \
                and t.astimezone(ET).strftime("%H:%M") <= "15:59":
            out.append(_flat(t, out[-1].close))
            t += timedelta(minutes=1)
    return out


def _flat(t, price):
    return Bar(t.strftime("%Y-%m-%dT%H:%M:00Z"), price, price, price, price, 0.0)
