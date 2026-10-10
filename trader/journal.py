"""Trade journal (JSON lines) and the daily comparison report.

Record kinds (one JSON object per line, appended by Claude while running):
  {"kind": "signal",    "symbol", "bar", "action": "buy" | <skip reason>, ...indicator values}
  {"kind": "fill",      "symbol", "side": "buy" | "sell", "qty", "price", "ts", "reason"?}
  {"kind": "user_call", "symbol", "ts", "note"}     # the user's own real-time picks

    python -m trader.journal report journal.jsonl bars/*.json --day 2026-10-12

For every rule signal and every user call, the report shows what the live exit (fixed take-profit)
and the shadow exits (dynamic2, dynamic3) would have made from the next bar's open, plus actual fills.
"""

import argparse
import json
import math
from pathlib import Path

from . import config as config_mod
from . import signals
from .data import et_day, et_hhmm, load_files
from .exits import simulate

VARIANTS = ("fixed", "dynamic2", "dynamic3")


def append(path, record):
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def read(path):
    p = Path(path)
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def hypothetical(bars, ind, signal_ts, cfg):
    """P&L per exit variant for a buy at the open of the bar after signal_ts."""
    idx = {b.ts: i for i, b in enumerate(bars)}
    i = idx.get(signal_ts)
    if i is None or i + 1 >= len(bars) or et_day(bars[i + 1].ts) != et_day(signal_ts):
        return None
    hs = cfg.backtest.half_spread
    entry = bars[i + 1].open * (1 + hs)
    qty = max(cfg.account.min_shares, math.floor(cfg.account.position_size_usd / entry))
    out = {"entry": round(entry, 2), "qty": qty}
    for v in VARIANTS:
        px, j, reason = simulate(bars, ind, i + 1, entry, cfg, v, hs)
        out[v] = {"pnl": round(qty * (px - entry), 2), "exit": et_hhmm(bars[min(j, len(bars) - 1)].ts), "reason": reason}
    return out


def _floor_minute(ts):
    return ts[:17] + "00Z"


def report(records, bars_by_symbol, cfg, day):
    ind_by = {s: signals.compute(b, cfg) for s, b in bars_by_symbol.items()}
    rows = []
    for r in records:
        if r["kind"] not in ("signal", "user_call"):
            continue
        ts = r["bar"] if r["kind"] == "signal" else _floor_minute(r["ts"])
        if et_day(ts) != day or r["symbol"] not in bars_by_symbol:
            continue
        h = hypothetical(bars_by_symbol[r["symbol"]], ind_by[r["symbol"]], ts, cfg)
        rows.append((r["kind"], r["symbol"], et_hhmm(ts), r.get("action", r.get("note", "")), h))

    lines = [f"# Journal {day}", "",
             "| source | symbol | time ET | action | fixed TP | dynamic2 | dynamic3 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    totals = {v: 0.0 for v in VARIANTS}
    for kind, sym, t, action, h in rows:
        if h is None:
            lines.append(f"| {kind} | {sym} | {t} | {action} | n/a | n/a | n/a |")
            continue
        cells = []
        for v in VARIANTS:
            totals[v] += h[v]["pnl"]
            cells.append(f"{h[v]['pnl']:+.2f} ({h[v]['reason']} {h[v]['exit']})")
        lines.append(f"| {kind} | {sym} | {t} | {action} | " + " | ".join(cells) + " |")
    lines += ["", "Hypothetical totals: " + ", ".join(f"{v} {totals[v]:+.2f}" for v in VARIANTS)]

    fills = [r for r in records if r["kind"] == "fill" and et_day(_floor_minute(r["ts"])) == day]
    if fills:
        realized, open_buys = 0.0, {}
        for f in sorted(fills, key=lambda r: r["ts"]):
            if f["side"] == "buy":
                open_buys.setdefault(f["symbol"], []).append(f)
            elif open_buys.get(f["symbol"]):
                b = open_buys[f["symbol"]].pop(0)
                realized += float(f["qty"]) * (float(f["price"]) - float(b["price"]))
        lines += ["", f"Actual fills: {len(fills)}, realized P&L {realized:+.2f}"]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("journal")
    rp.add_argument("bars", nargs="+", help="saved get_equity_historicals responses covering the day")
    rp.add_argument("--day", required=True, help="YYYY-MM-DD (ET)")
    rp.add_argument("--config")
    a = ap.parse_args(argv)
    cfg = config_mod.load(a.config)
    print(report(read(a.journal), load_files(a.bars), cfg, a.day))


if __name__ == "__main__":
    main()
