"""One decision step: market state in, list of broker actions out. Runs once per minute.

    python -m trader.live.decide state.json

The code never talks to the broker. Claude gathers the state through the Robinhood connector,
runs this, and carries out the actions (or only journals them in observe mode). See docs/RUNBOOK.md.

State (JSON):
  now                 UTC time of the run, "2026-10-12T14:31:05Z"
  historicals         raw get_equity_historicals response (interval=minute), or
  bars                {symbol: [Robinhood bar dicts]}
  quotes              {symbol: {"ask": 109.88, "bid": 109.71}}               (optional)
  positions           [{"symbol", "quantity", "avg_price"}]
  open_orders         [{"id", "symbol", "side", "type", "quantity", "limit_price", "created_at"}]
  realized_pnl_today  float
  earnings_today      [symbol, ...]                                          (optional)
  buying_power        float                                                  (optional)
"""

import argparse
import json
import math
import sys
from datetime import timedelta

from .. import config as config_mod
from .. import signals
from ..data import ET, Bar, bars_from_robinhood, parse_ts
from ..exits import stop_price, take_profit_price

STALE_AFTER = timedelta(minutes=3)


def _bars(state):
    if "historicals" in state:
        return bars_from_robinhood(state["historicals"])
    out = {}
    for sym, rows in state.get("bars", {}).items():
        out[sym] = [Bar(r["begins_at"], float(r["open_price"]), float(r["high_price"]), float(r["low_price"]),
                        float(r["close_price"]), float(r.get("volume") or 0))
                    for r in rows if not r.get("interpolated")]
    return out


def _completed(bars, now):
    """Drop the bar that is still forming (its minute has not ended yet)."""
    return [b for b in bars if parse_ts(b.ts) + timedelta(minutes=1) <= now]


def decide(state, cfg):
    now = parse_ts(state["now"])
    hhmm = now.astimezone(ET).strftime("%H:%M")
    positions = [p for p in state.get("positions", []) if float(p["quantity"]) > 0]
    orders = state.get("open_orders", [])
    actions, sigs, notes = [], [], []

    def orders_for(sym, side):
        return [o for o in orders if o["symbol"] == sym and o["side"] == side]

    # 1. End of day: cancel everything, sell everything, no new entries.
    if hhmm >= cfg.exit.end_of_day.flatten_time:
        for o in orders:
            actions.append({"type": "cancel_order", "order_id": o["id"], "symbol": o["symbol"], "reason": "end_of_day"})
        for p in positions:
            actions.append({"type": "sell_market", "symbol": p["symbol"], "quantity": float(p["quantity"]),
                            "reason": "end_of_day"})
        return {"now_et": hhmm, "mode": cfg.run.mode, "actions": actions, "signals": sigs, "notes": notes}

    # 2. Buy orders that did not fill in time.
    ttl = timedelta(minutes=cfg.entry.buy_order_ttl_minutes)
    for o in orders:
        if o["side"] == "buy" and o.get("created_at") and now - parse_ts(o["created_at"]) > ttl:
            actions.append({"type": "cancel_order", "order_id": o["id"], "symbol": o["symbol"], "reason": "buy_not_filled"})

    # 3. Every position gets exactly one take-profit order for its full quantity.
    bars = _bars(state)
    for p in positions:
        sym, qty, avg = p["symbol"], float(p["quantity"]), float(p["avg_price"])
        sells = orders_for(sym, "sell")
        if cfg.exit.stop_loss.enabled:  # placeholder, off by default
            last = _completed(bars.get(sym, []), now)
            if last and last[-1].close <= stop_price(avg, cfg):
                actions += [{"type": "cancel_order", "order_id": o["id"], "symbol": sym, "reason": "stop_loss"} for o in sells]
                actions.append({"type": "sell_market", "symbol": sym, "quantity": qty, "reason": "stop_loss"})
                continue
        if not cfg.exit.take_profit.enabled:
            continue
        if sum(float(o["quantity"]) for o in sells) == qty:
            continue
        actions += [{"type": "cancel_order", "order_id": o["id"], "symbol": sym, "reason": "resize_take_profit"}
                    for o in sells]
        actions.append({"type": "place_take_profit", "symbol": sym, "quantity": qty,
                        "limit_price": take_profit_price(avg, cfg), "time_in_force": "gfd"})
        if not sells and cfg.alerts.price_alert_pct:
            actions.append({"type": "create_price_alert", "symbol": sym, "condition": "price_below",
                            "threshold": round(avg * (1 + cfg.alerts.price_alert_pct), 2)})

    # 4. New entries.
    held = {p["symbol"] for p in positions} | {o["symbol"] for o in orders if o["side"] == "buy"}
    capacity = cfg.account.max_positions - len(held)
    gate = None
    if not (cfg.schedule.first_entry_time <= hhmm < cfg.schedule.last_entry_time):
        gate = "outside_entry_window"
    elif state.get("realized_pnl_today", 0.0) <= -cfg.account.max_daily_loss_usd:
        gate = "daily_loss_limit"
    cash = state.get("buying_power")
    for sym in cfg.symbols.watchlist:
        b = _completed(bars.get(sym, []), now)
        if len(b) < 60:
            notes.append(f"{sym}: only {len(b)} completed bars, need 60")
            continue
        if now - parse_ts(b[-1].ts) > STALE_AFTER:
            notes.append(f"{sym}: last bar {b[-1].ts} is stale")
            continue
        ind = signals.compute(b, cfg)
        i = len(b) - 1
        if not signals.is_entry(ind, i, cfg):
            continue
        sig = {"symbol": sym, "bar": b[i].ts, "close": b[i].close, **signals.snapshot(ind, i)}
        skip = gate
        if not skip and sym in held:
            skip = "already_holding"
        elif not skip and cfg.schedule.skip_earnings_day and sym in state.get("earnings_today", []):
            skip = "earnings_day"
        elif not skip and capacity <= 0:
            skip = "max_positions"
        if not skip:
            quote = state.get("quotes", {}).get(sym, {})
            px = float(quote["ask"]) if cfg.entry.limit_price == "ask" and quote.get("ask") else b[i].close
            qty = max(cfg.account.min_shares, math.floor(cfg.account.position_size_usd / px))
            if cash is not None and qty * px > cash:
                qty = math.floor(cash / px)
            if qty < cfg.account.min_shares:
                skip = "not_enough_buying_power"
            else:
                actions.append({"type": "buy_limit", "symbol": sym, "quantity": qty, "limit_price": round(px, 2),
                                "time_in_force": "gfd", "signal_bar": b[i].ts})
                capacity -= 1
                if cash is not None:
                    cash -= qty * px
        sig["action"] = skip or "buy"
        sigs.append(sig)
    return {"now_et": hhmm, "mode": cfg.run.mode, "actions": actions, "signals": sigs, "notes": notes}


def main(argv=None):
    ap = argparse.ArgumentParser(description="One decision step")
    ap.add_argument("state", help="state JSON file, or - for stdin")
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    state = json.load(sys.stdin if a.state == "-" else open(a.state))
    print(json.dumps(decide(state, config_mod.load(a.config)), indent=2))


if __name__ == "__main__":
    main()
