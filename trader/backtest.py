"""Backtest on saved Robinhood 1-minute bars.

    python -m trader.backtest data/*.json                 # fixed take-profit (the live setting)
    python -m trader.backtest data/*.json --compare       # fixed vs dynamic exits side by side

Execution model: signal on a bar's close -> buy at the next bar's open + half spread;
take-profit fills at its limit when a bar's high clears it by a cent; flat at flatten_time.
Shares = floor(position_size / entry). At most max_positions open at once (first come, first served).
"""

import argparse
import math
from collections import defaultdict

from . import config as config_mod
from . import signals
from .data import et_day, et_hhmm, load_files
from .exits import simulate

WARMUP_BARS = 60


def symbol_trades(sym, bars, cfg, variant="fixed", rule=None, days=None):
    ind = signals.compute(bars, cfg)
    hs = cfg.backtest.half_spread
    out, i = [], WARMUP_BARS
    while i < len(bars) - 1:
        day, hhmm = et_day(bars[i].ts), et_hhmm(bars[i].ts)
        ok = ((days is None or day in days) and et_day(bars[i + 1].ts) == day
              and cfg.schedule.first_entry_time <= hhmm < cfg.schedule.last_entry_time
              and signals.is_entry(ind, i, cfg, rule))
        if not ok:
            i += 1
            continue
        entry = bars[i + 1].open * (1 + hs)
        qty = max(cfg.account.min_shares, math.floor(cfg.account.position_size_usd / entry))
        px, j, reason = simulate(bars, ind, i + 1, entry, cfg, variant, hs)
        j = min(j, len(bars) - 1)
        out.append(dict(symbol=sym, day=day, signal_ts=bars[i].ts, entry_ts=bars[i + 1].ts, exit_ts=bars[j].ts,
                        qty=qty, entry=round(entry, 4), exit=round(px, 4), pnl=round(qty * (px - entry), 2),
                        reason=reason, at_signal=signals.snapshot(ind, i)))
        i = j + 1
    return out


def run(bars_by_symbol, cfg, variant="fixed", rule=None, days=None):
    trades = sorted((t for s, b in bars_by_symbol.items() for t in symbol_trades(s, b, cfg, variant, rule, days)),
                    key=lambda t: t["entry_ts"])
    taken = []
    for t in trades:
        busy = sum(1 for x in taken if x["entry_ts"] <= t["entry_ts"] <= x["exit_ts"])
        if busy < cfg.account.max_positions:
            taken.append(t)
    return taken


def summarize(trades, days):
    daily = defaultdict(float)
    for t in trades:
        daily[t["day"]] += t["pnl"]
    eq = peak = max_dd = 0.0
    for d in days:
        eq += daily.get(d, 0.0)
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    n = len(trades)
    total = sum(t["pnl"] for t in trades)
    return {
        "days": len(days), "trades": n,
        "win_rate": round(sum(t["pnl"] > 0 for t in trades) / n, 3) if n else None,
        "take_profits": sum(t["reason"] == "take_profit" for t in trades),
        "total_pnl": round(total, 2), "per_day": round(total / len(days), 2) if days else None,
        "worst_trade": min((t["pnl"] for t in trades), default=0.0), "max_drawdown": round(max_dd, 2),
        "by_symbol": {s: round(sum(t["pnl"] for t in trades if t["symbol"] == s), 2)
                      for s in sorted({t["symbol"] for t in trades})},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="saved get_equity_historicals responses (interval=minute)")
    ap.add_argument("--config")
    ap.add_argument("--variant", default="fixed", choices=["fixed", "dynamic2", "dynamic3"])
    ap.add_argument("--compare", action="store_true", help="run fixed, dynamic2 and dynamic3")
    ap.add_argument("--rule", help="entry rule (default: config entry.rule)")
    ap.add_argument("--size", type=float, help="override position_size_usd")
    ap.add_argument("--trades", action="store_true", help="print every trade")
    a = ap.parse_args(argv)

    overrides = {"account": {"position_size_usd": a.size}} if a.size else None
    cfg = config_mod.load(a.config, overrides)
    bars = load_files(a.files)
    bars = {s: b for s, b in bars.items() if s in cfg.symbols.watchlist}
    days = sorted({et_day(b.ts) for bs in bars.values() for b in bs[WARMUP_BARS:]})
    for variant in (["fixed", "dynamic2", "dynamic3"] if a.compare else [a.variant]):
        trades = run(bars, cfg, variant, a.rule, set(days))
        print(variant, summarize(trades, days))
        if a.trades:
            for t in trades:
                print("  ", t)


if __name__ == "__main__":
    main()
