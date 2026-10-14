"""Watch the watchlist with Alpaca 1-minute bars and wake Claude only when something needs doing.

    python -m trader.live.watcher                              # live, until the end-of-day event
    python -m trader.live.watcher --once                       # evaluate the latest bar now and exit
    python -m trader.live.watcher --backfill 2026-10-09 2026-10-09   # list the candidates those days would have produced

Each stdout line is one JSON event, and each one is meant to wake Claude (Monitor tool):
  CANDIDATE  a loose version of the entry rule fired on IEX data (or MACD is about to cross:
             near_cross=true). Claude re-checks the strict rule on Robinhood bars with decide.py
             before buying, and re-checks for a couple of minutes; most candidates will not trade.
  STOP       a completed bar closed at or below a stop listed in the stops file (--stops; only used in the
             "monitored" stop-loss fallback, when OCO orders are unavailable). Claude re-checks and sells.
  EOD        flatten time reached. Claude cancels orders and sells what is left. The watcher exits.
  NO_DATA    no bars at all this morning (market holiday?). The watcher exits.
  ERROR      Alpaca polls keep failing; RECOVERED when they work again; FATAL then exit if it lasts.
Nothing else goes to stdout. Progress goes to the status file (--status) and stderr.
"""

import argparse
import bisect
import json
import sys
import time
from datetime import datetime, timedelta, timezone

from .. import config as config_mod
from .. import signals
from ..data import ET, parse_ts
from . import alpaca

MAX_BARS = 800  # indicators use at most this many recent bars (about two trading days)


def loose_match(ind, i, cfg):
    """MACD crossed up on one of the last cross_window bars, while RSI and %K/%D were (each on some
    bar of that window) within slack of the strict limits. Wider than the strict rule on purpose:
    IEX bars differ from Robinhood's, and a missed candidate is a missed trade."""
    w, e = cfg.watcher, cfg.entry
    window = range(max(1, i - w.cross_window + 1), i + 1)
    if not any(ind.macd[j] > ind.signal[j] and ind.macd[j - 1] <= ind.signal[j - 1] for j in window) \
            and not near_cross(ind, i, w.near_cross_bars):
        return False
    rsi_ok = any(ind.rsi[j] is not None and ind.rsi[j] < e.rsi_max + w.rsi_slack for j in window)
    kd_lim = e.stoch_max + w.stoch_slack
    kd_ok = any(ind.k[j] is not None and ind.d[j] is not None and ind.k[j] < kd_lim and ind.d[j] < kd_lim
                for j in window)
    return rsi_ok and kd_ok


def near_cross(ind, i, bars_ahead):
    """MACD still below its signal line but closing the gap fast enough to cross within bars_ahead bars.
    IEX crosses often come a minute later than Robinhood's; this wakes Claude early instead."""
    if not bars_ahead or i < 1:
        return False
    gap, step = ind.hist[i], ind.hist[i] - ind.hist[i - 1]
    return gap <= 0 and step > 0 and -gap <= step * bars_ahead


def candidate(sym, bars, ind, i, cfg):
    snap = signals.snapshot(ind, i)
    return {"event": "CANDIDATE", "symbol": sym, "bar": bars[i].ts,
            "bar_et": parse_ts(bars[i].ts).astimezone(ET).strftime("%H:%M"), "close": bars[i].close,
            "strict_on_iex": signals.is_entry(ind, i, cfg), "near_cross": ind.hist[i] <= 0, **snap}


class Watcher:
    def __init__(self, cfg, fetch=alpaca.fetch_bars, now=lambda: datetime.now(timezone.utc), sleep=time.sleep,
                 emit=None, status_path=None, stops_path=None):
        self.cfg, self.fetch, self.now, self.sleep = cfg, fetch, now, sleep
        self.emit_fn = emit or (lambda line: print(line, flush=True))
        self.status_path = status_path
        self.stops_path = stops_path
        self.stops_fired = set()
        self.symbols = list(cfg.symbols.watchlist)
        self.bars = {s: {} for s in self.symbols}
        self.last_candidate = {}   # symbol -> bar index time of the last CANDIDATE
        self.errors, self.error_since, self.error_reported = 0, None, False
        self.seq = 0

    # -- output --------------------------------------------------------------
    def emit(self, event):
        self.seq += 1
        event = {"seq": self.seq, "time_et": self.now().astimezone(ET).strftime("%Y-%m-%d %H:%M:%S"), **event}
        self.emit_fn(json.dumps(event))

    def status(self, **extra):
        if not self.status_path:
            return
        last = {s: (max(d) if d else None) for s, d in self.bars.items()}
        with open(self.status_path, "w") as f:
            json.dump({"updated": self.now().strftime("%Y-%m-%dT%H:%M:%SZ"), "last_bar": last,
                       "consecutive_errors": self.errors, "events": self.seq, **extra}, f)

    # -- data ----------------------------------------------------------------
    def poll(self, start, end=None):
        """Fetch and merge bars. False if the fetch failed (and reports ERROR/FATAL as needed)."""
        try:
            got = self.fetch(self.symbols, start, end, feed=self.cfg.watcher.feed)
        except alpaca.AlpacaError as e:
            self.errors += 1
            self.error_since = self.error_since or self.now()
            print(f"poll failed: {e}", file=sys.stderr, flush=True)
            if self.errors >= self.cfg.watcher.max_errors and not self.error_reported:
                self.emit({"event": "ERROR", "detail": str(e), "consecutive_errors": self.errors})
                self.error_reported = True
            return False
        if self.error_reported:
            self.emit({"event": "RECOVERED", "after_errors": self.errors})
        self.errors, self.error_since, self.error_reported = 0, None, False
        for sym, rows in got.items():
            if sym in self.bars:
                self.bars[sym].update({b.ts: b for b in rows})
        return True

    def series(self, sym, until):
        rows = alpaca.regular_hours([self.bars[sym][t] for t in sorted(self.bars[sym])])
        if self.cfg.watcher.fill_gaps:
            rows = alpaca.fill_gaps(rows, until=until)
        return [b for b in rows if parse_ts(b.ts) + timedelta(minutes=1) <= until]  # completed bars only

    def check_stops(self, now):
        """STOP events for {symbol: stop_price} in the stops file (written by Claude)."""
        if not self.stops_path:
            return []
        try:
            with open(self.stops_path) as f:
                stops = json.load(f)
        except (OSError, ValueError):
            return []
        found = []
        for sym, stop in stops.items():
            if sym not in self.bars or (sym, stop) in self.stops_fired:
                continue
            bars = self.series(sym, now)
            if bars and now - parse_ts(bars[-1].ts) <= timedelta(minutes=3) and bars[-1].close <= float(stop):
                self.stops_fired.add((sym, stop))
                found.append({"event": "STOP", "symbol": sym, "stop_price": float(stop), "bar": bars[-1].ts,
                              "close": bars[-1].close})
        for ev in found:
            self.emit(ev)
        return found

    def check(self, now, force=False, prepared=None):
        """CANDIDATE events for the last completed bar of each symbol."""
        hhmm = now.astimezone(ET).strftime("%H:%M")
        in_window = self.cfg.schedule.first_entry_time <= hhmm < self.cfg.schedule.last_entry_time
        found = []
        for sym in self.symbols:
            if prepared is None:
                bars = self.series(sym, now)
            else:  # backfill: one series per day, cut at now
                full, starts = prepared[sym]
                bars = full[:bisect.bisect_right(starts, now - timedelta(minutes=1))]
            bars = bars[-MAX_BARS:]
            if len(bars) < 60 or (not force and not in_window):
                continue
            if now - parse_ts(bars[-1].ts) > timedelta(minutes=3):
                continue  # nothing recent (halted, or no data yet today)
            ind = signals.compute(bars, self.cfg)
            i = len(bars) - 1
            if not loose_match(ind, i, self.cfg):
                continue
            last = self.last_candidate.get(sym)
            if last and parse_ts(bars[i].ts) - last < timedelta(minutes=self.cfg.watcher.cross_window):
                continue  # same cross, already reported
            self.last_candidate[sym] = parse_ts(bars[i].ts)
            found.append(candidate(sym, bars, ind, i, self.cfg))
        for ev in found:
            self.emit(ev)
        return found

    # -- modes ---------------------------------------------------------------
    def _today_et(self, hhmm, now=None):
        now = now or self.now()
        h, m = map(int, hhmm.split(":"))
        return now.astimezone(ET).replace(hour=h, minute=m, second=0, microsecond=0).astimezone(timezone.utc)

    def _sleep_until(self, t):
        while True:
            left = (t - self.now()).total_seconds()
            if left <= 0:
                return
            self.sleep(min(left, 30))

    def run(self):
        cfg = self.cfg
        flatten = self._today_et(cfg.exit.end_of_day.flatten_time)
        if self.now() >= flatten:
            self.emit({"event": "EOD", "flatten_time": cfg.exit.end_of_day.flatten_time})
            return 0
        while not self.poll(self.now() - timedelta(days=cfg.watcher.history_days)):
            if self._fatal():
                return 2
            self.sleep(30)
        self.status(state="started")
        open_check = self._today_et(cfg.watcher.no_data_check_time)
        while True:
            nxt = self.now().replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=cfg.watcher.poll_second)
            if nxt >= flatten:
                self._sleep_until(flatten)
                self.emit({"event": "EOD", "flatten_time": cfg.exit.end_of_day.flatten_time})
                self.status(state="done")
                return 0
            self._sleep_until(nxt)
            now = self.now()
            if not self.poll(now - timedelta(minutes=10)):
                if self._fatal():
                    return 2
                continue
            if now >= open_check and not self._has_today(now):
                self.emit({"event": "NO_DATA", "detail": "no bars today; market closed?"})
                self.status(state="no_data")
                return 0
            self.check_stops(now)
            self.check(now)
            self.status(state="running")

    def _has_today(self, now):
        day = now.astimezone(ET).date()
        return any(parse_ts(t).astimezone(ET).date() == day for d in self.bars.values() for t in d)

    def _fatal(self):
        limit = timedelta(minutes=self.cfg.watcher.fatal_after_minutes)
        if self.error_since and self.now() - self.error_since >= limit:
            self.emit({"event": "FATAL", "detail": f"Alpaca failing for {self.cfg.watcher.fatal_after_minutes} min; "
                                                   "watcher stopped"})
            self.status(state="fatal")
            return True
        return False

    def once(self):
        now = self.now()
        if not self.poll(now - timedelta(days=self.cfg.watcher.history_days)):
            return 2
        found = self.check(now, force=True)
        for sym in self.symbols:
            bars = self.series(sym, now)
            if bars:
                ind = signals.compute(bars, self.cfg)
                info = {"symbol": sym, "bars": len(bars), "last_bar": bars[-1].ts, "close": bars[-1].close,
                        "loose": loose_match(ind, len(bars) - 1, self.cfg), **signals.snapshot(ind, len(bars) - 1)}
                print(json.dumps(info), file=sys.stderr)
        return 0 if found is not None else 1

    def backfill(self, first_day, last_day):
        """Replay [first_day, last_day] (ET dates) minute by minute, as the live loop would see it."""
        start = datetime.strptime(first_day, "%Y-%m-%d").replace(tzinfo=ET) - timedelta(days=self.cfg.watcher.history_days)
        end = datetime.strptime(last_day, "%Y-%m-%d").replace(tzinfo=ET) + timedelta(days=1)
        if not self.poll(start.astimezone(timezone.utc), end.astimezone(timezone.utc)):
            return 2
        times = sorted({parse_ts(t) for d in self.bars.values() for t in d
                        if first_day <= parse_ts(t).astimezone(ET).strftime("%Y-%m-%d") <= last_day})
        days = sorted({t.astimezone(ET).date() for t in times})
        for day in days:
            t = datetime(day.year, day.month, day.day, 9, 31, tzinfo=ET).astimezone(timezone.utc)
            stop = datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET).astimezone(timezone.utc)
            prepared = {}
            for sym in self.symbols:
                full = self.series(sym, stop)
                prepared[sym] = (full, [parse_ts(b.ts) for b in full])
            while t <= stop:
                self.now = lambda t=t: t
                self.check(t, prepared=prepared)
                t += timedelta(minutes=1)
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Alpaca pre-filter that wakes Claude on candidate entries")
    ap.add_argument("--config")
    ap.add_argument("--status", help="write a small JSON status file here every minute")
    ap.add_argument("--stops", help="JSON file {symbol: stop_price} to watch (monitored stop-loss fallback)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--backfill", nargs=2, metavar=("FIRST_DAY", "LAST_DAY"))
    a = ap.parse_args(argv)
    w = Watcher(config_mod.load(a.config), status_path=a.status, stops_path=a.stops)
    if a.backfill:
        return w.backfill(*a.backfill)
    if a.once:
        return w.once()
    return w.run()


if __name__ == "__main__":
    sys.exit(main())
