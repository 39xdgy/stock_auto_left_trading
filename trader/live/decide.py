"""One decision step: market state in, list of broker actions out. Runs once per minute.

    python -m trader.live.decide state.json
    python -m trader.live.decide state.json --historicals saved_historicals.txt   # bars from a saved tool result

The code never talks to the broker. Claude gathers the state through the Robinhood connector,
runs this, and carries out the actions (or only journals them in observe mode). See docs/RUNBOOK.md.

State (JSON):
  now                 UTC time of the run, "2026-10-12T14:31:05Z" (CLI default: the current time)
  historicals         raw get_equity_historicals response (interval=minute), or
  bars                {symbol: [Robinhood bar dicts]}
  quotes              {symbol: {"ask": 109.88, "bid": 109.71}}               (optional)
  positions           [{"symbol", "quantity", "avg_price"}]
  open_orders         [{"id", "symbol", "side", "type", "quantity", "limit_price", "created_at"}]
  oco_orders          [{"id", "symbol", "quantity", "take_profit_price", "stop_price", "leg_ids": [...]}]
                      open OCO orders (get_advanced_orders); their legs are ignored in open_orders
  oco_unavailable     true when OCO cannot be used today: stops are then monitored (optional)
  realized_pnl_today  float
  earnings_today      [symbol, ...]                                          (optional)
  buying_power        float                                                  (optional)
  handled_signals     ["AAOI@2026-10-12T14:31:00Z", ...] signals already acted on or skipped today;
                      needed because a signal stays visible for signal_lookback_bars more minutes
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone

from .. import config as config_mod
from .. import signals
from ..data import ET, Bar, bars_from_robinhood, load_files, parse_ts
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
    actions, sigs, notes, forming = [], [], [], []

    def orders_for(sym, side):
        return [o for o in orders if o["symbol"] == sym and o["side"] == side]

    # Legs of an OCO also show up as plain sell orders; handle them through the OCO only.
    ocos = state.get("oco_orders", [])
    oco_legs = {leg for o in ocos for leg in o.get("leg_ids", [])}
    orders = [o for o in orders if o["id"] not in oco_legs]

    # 1. End of day: cancel everything, sell everything, no new entries.
    if hhmm >= cfg.exit.end_of_day.flatten_time:
        for o in ocos:
            actions.append({"type": "cancel_oco", "order_id": o["id"], "symbol": o["symbol"], "reason": "end_of_day"})
        for o in orders:
            actions.append({"type": "cancel_order", "order_id": o["id"], "symbol": o["symbol"], "reason": "end_of_day"})
        for p in positions:
            actions.append({"type": "sell_market", "symbol": p["symbol"], "quantity": float(p["quantity"]),
                            "reason": "end_of_day"})
        return {"now_et": hhmm, "mode": cfg.run.mode, "actions": actions, "signals": sigs, "notes": notes,
                "forming": forming}

    # 2. Buy orders that did not fill in time.
    ttl = timedelta(minutes=cfg.entry.buy_order_ttl_minutes)
    for o in orders:
        if o["side"] == "buy" and o.get("created_at") and now - parse_ts(o["created_at"]) > ttl:
            actions.append({"type": "cancel_order", "order_id": o["id"], "symbol": o["symbol"], "reason": "buy_not_filled"})

    # 3. Every position gets its exits for the full quantity:
    #    stop_loss.mode = "oco":       one OCO order (take-profit limit + stop-market; the broker cancels the other leg)
    #    stop_loss.mode = "monitored": a take-profit limit, and a market sell when a completed bar closes at or
    #                                  below the stop (used when OCO is unavailable: state "oco_unavailable": true)
    #    stop_loss disabled:           a take-profit limit only
    bars = _bars(state)
    sl = cfg.exit.stop_loss
    mode = None
    if sl.enabled:
        mode = "monitored" if state.get("oco_unavailable") else getattr(sl, "mode", "oco")
    for p in positions:
        sym, qty, avg = p["symbol"], float(p["quantity"]), float(p["avg_price"])
        sells = orders_for(sym, "sell")
        my_ocos = [o for o in ocos if o["symbol"] == sym]
        tp, stop = take_profit_price(avg, cfg), stop_price(avg, cfg)
        if mode == "monitored":
            last = _completed(bars.get(sym, []), now)
            if last and last[-1].close <= stop:
                actions += [{"type": "cancel_oco", "order_id": o["id"], "symbol": sym, "reason": "stop_loss"}
                            for o in my_ocos]
                actions += [{"type": "cancel_order", "order_id": o["id"], "symbol": sym, "reason": "stop_loss"}
                            for o in sells]
                actions.append({"type": "sell_market", "symbol": sym, "quantity": qty, "reason": "stop_loss"})
                continue
        if mode == "oco":
            good = [o for o in my_ocos if float(o["quantity"]) == qty
                    and abs(float(o["take_profit_price"]) - tp) < 0.005 and abs(float(o["stop_price"]) - stop) < 0.005]
            if good and not sells and len(my_ocos) == 1:
                continue
            actions += [{"type": "cancel_oco", "order_id": o["id"], "symbol": sym, "reason": "replace_exits"}
                        for o in my_ocos]
            actions += [{"type": "cancel_order", "order_id": o["id"], "symbol": sym, "reason": "replace_exits"}
                        for o in sells]
            actions.append({"type": "place_oco", "symbol": sym, "quantity": qty, "take_profit_limit_price": tp,
                            "stop_loss_stop_price": stop, "time_in_force": "gfd"})
            continue
        if not cfg.exit.take_profit.enabled:
            continue
        if not my_ocos and sum(float(o["quantity"]) for o in sells) == qty:
            continue
        actions += [{"type": "cancel_oco", "order_id": o["id"], "symbol": sym, "reason": "replace_exits"}
                    for o in my_ocos]
        actions += [{"type": "cancel_order", "order_id": o["id"], "symbol": sym, "reason": "resize_take_profit"}
                    for o in sells]
        actions.append({"type": "place_take_profit", "symbol": sym, "quantity": qty,
                        "limit_price": tp, "time_in_force": "gfd"})
        if mode == "monitored":
            actions.append({"type": "set_watch_stop", "symbol": sym, "stop_price": stop})
        if not sells and not my_ocos and cfg.alerts.price_alert_pct and not sl.enabled:
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
        handled = set(state.get("handled_signals", []))
        lookback = getattr(cfg.entry, "signal_lookback_bars", 0)
        hits = [j for j in range(len(b) - 1, len(b) - 2 - lookback, -1) if signals.is_entry(ind, j, cfg)]
        if not hits or f"{sym}@{b[hits[0]].ts}" in handled:
            j = len(b) - 1
            if not hits and signals.rsi_low(ind, j, cfg) and signals.kd_low(ind, j, cfg) \
                    and ind.hist[j] <= 0 and ind.hist[j] > ind.hist[j - 1]:
                forming.append(sym)  # oversold and MACD closing in: worth another look next minute
            continue
        i = hits[0]
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
    return {"now_et": hhmm, "mode": cfg.run.mode, "actions": actions, "signals": sigs, "notes": notes,
            "forming": forming}


def main(argv=None):
    ap = argparse.ArgumentParser(description="One decision step")
    ap.add_argument("state", help="state JSON file, or - for stdin")
    ap.add_argument("--config")
    ap.add_argument("--historicals", nargs="+", metavar="FILE",
                    help="saved get_equity_historicals results, used instead of the state's bars")
    ap.add_argument("--signals-only", action="store_true",
                    help="quick look without positions/orders: report signals and forming setups, no actions")
    a = ap.parse_args(argv)
    state = json.load(sys.stdin if a.state == "-" else open(a.state))
    state.setdefault("now", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if a.historicals:
        state.pop("historicals", None)
        state["bars"] = {sym: [{"begins_at": b.ts, "open_price": b.open, "high_price": b.high, "low_price": b.low,
                                "close_price": b.close, "volume": b.volume} for b in bars]
                         for sym, bars in load_files(a.historicals).items()}
    out = decide(state, config_mod.load(a.config))
    if a.signals_only:
        out["actions"] = []
        out["notes"].append("signals-only: positions and orders were not checked; nothing to execute")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
