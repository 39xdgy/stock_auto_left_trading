import io
import json
import os
import unittest
from datetime import timedelta
from unittest import mock

from trader import config
from trader.data import Bar, parse_ts
from trader.live import alpaca
from trader.live.watcher import Watcher

from tests.helpers import make_bars, utc

DAY = "2026-10-12"


def dip_then_bounce(n=120):
    """Steady fall, then a sharp turn up: oversold MACD golden cross near the end."""
    closes = [100 - 0.08 * i for i in range(n - 6)] + [100 - 0.08 * (n - 7) + 0.25 * k for k in range(1, 7)]
    return make_bars(closes, day=DAY, start="09:30")


class FakeClock:
    def __init__(self, t):
        self.t = parse_ts(t)

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


class AlpacaTests(unittest.TestCase):
    def test_fetch_pages_and_headers(self):
        pages = [{"bars": {"AAOI": [{"t": "2026-10-12T13:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]},
                  "next_page_token": "p2"},
                 {"bars": {"AAOI": [{"t": "2026-10-12T13:31:00Z", "o": 1.5, "h": 2, "l": 1, "c": 1.8, "v": 5}]},
                  "next_page_token": None}]
        seen = []

        def urlopen(req, timeout):
            seen.append(req)
            body = io.BytesIO(json.dumps(pages[len(seen) - 1]).encode())
            body.__enter__ = lambda *a: body
            body.__exit__ = lambda *a: None
            return body

        env = {"APCA_API_KEY_ID": "k", "APCA_API_SECRET_KEY": "s"}
        with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen", urlopen):
            out = alpaca.fetch_bars(["AAOI", "ASTS"], parse_ts("2026-10-12T13:00:00Z"))
        self.assertEqual([b.close for b in out["AAOI"]], [1.5, 1.8])
        self.assertEqual(out["ASTS"], [])
        self.assertIn("page_token=p2", seen[1].full_url)
        self.assertEqual(seen[0].get_header("Apca-api-key-id"), "k")

    def test_missing_keys(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(alpaca.AlpacaError):
                alpaca.fetch_bars(["AAOI"], parse_ts("2026-10-12T13:00:00Z"))

    def test_fill_gaps_within_day_and_up_to_now(self):
        bars = [Bar(utc(DAY, "09:30"), 1, 1, 1, 1.0), Bar(utc(DAY, "09:33"), 2, 2, 2, 2.0)]
        out = alpaca.fill_gaps(bars, until=parse_ts(utc(DAY, "09:36")))
        self.assertEqual([b.ts for b in out], [utc(DAY, t) for t in ("09:30", "09:31", "09:32", "09:33", "09:34", "09:35")])
        self.assertEqual((out[1].close, out[1].volume, out[4].close), (1.0, 0.0, 2.0))
        # no filling across the overnight gap
        two_days = [Bar(utc("2026-10-09", "15:59"), 1, 1, 1, 1.0), Bar(utc(DAY, "09:30"), 2, 2, 2, 2.0)]
        self.assertEqual(len(alpaca.fill_gaps(two_days)), 2)

    def test_regular_hours(self):
        bars = [Bar(utc(DAY, t), 1, 1, 1, 1) for t in ("09:29", "09:30", "15:59", "16:00")]
        self.assertEqual(len(alpaca.regular_hours(bars)), 2)


class NearCrossTests(unittest.TestCase):
    def test_near_cross(self):
        from types import SimpleNamespace
        from trader.live.watcher import near_cross
        ind = lambda h: SimpleNamespace(hist=h)
        self.assertTrue(near_cross(ind([-0.30, -0.10]), 1, 2))    # closes 0.2/bar, 0.1 left
        self.assertFalse(near_cross(ind([-0.30, -0.25]), 1, 2))   # too slow
        self.assertFalse(near_cross(ind([-0.10, -0.20]), 1, 2))   # moving away
        self.assertFalse(near_cross(ind([-0.30, -0.10]), 1, 0))   # disabled


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()
        self.events = []

    def watcher(self, bars_by_sym, now, fetch=None):
        clock = FakeClock(now)

        def fake_fetch(symbols, start, end=None, feed="iex"):
            return {s: [b for b in bars_by_sym.get(s, []) if parse_ts(b.ts) + timedelta(minutes=1) <= clock()]
                    for s in symbols}

        w = Watcher(self.cfg, fetch=fetch or fake_fetch, now=clock, sleep=clock.sleep,
                    emit=lambda line: self.events.append(json.loads(line)))
        return w, clock

    def test_candidate_once_per_cross(self):
        bars = dip_then_bounce()
        hits = []
        for k in range(100, len(bars) + 1):
            now = parse_ts(bars[k - 1].ts) + timedelta(minutes=1, seconds=4)
            w, _ = self.watcher({"AAOI": bars}, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
            w.last_candidate = getattr(self, "_last", {})
            w.poll(now - timedelta(days=1))
            hits += w.check(now)
            self._last = w.last_candidate
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0]["symbol"], "AAOI")

    def test_stop_event_once(self):
        import tempfile
        bars = make_bars([100.0] * 70 + [97.0], day=DAY, start="09:30")
        now = parse_ts(bars[-1].ts) + timedelta(minutes=1, seconds=4)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"AAOI": 98.5}, f)
        w, _ = self.watcher({"AAOI": bars}, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        w.stops_path = f.name
        w.poll(now - timedelta(days=1))
        self.assertEqual([e["event"] for e in w.check_stops(now)], ["STOP"])
        self.assertEqual(w.check_stops(now), [])
        os.unlink(f.name)

    def test_no_candidate_outside_entry_window(self):
        bars = dip_then_bounce()
        now = parse_ts(bars[-1].ts) + timedelta(minutes=1)
        w, _ = self.watcher({"AAOI": bars}, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        w.cfg = config.load(overrides={"schedule": {"last_entry_time": "09:35"}})
        w.poll(now - timedelta(days=1))
        self.assertEqual(w.check(now), [])

    def test_run_emits_eod_and_exits(self):
        bars = make_bars([100 + (i % 5) * 0.1 for i in range(390)], day=DAY, start="09:30")
        w, clock = self.watcher({s: bars for s in self.cfg.symbols.watchlist}, utc(DAY, "15:40"))
        self.assertEqual(w.run(), 0)
        self.assertEqual(self.events[-1]["event"], "EOD")
        self.assertGreaterEqual(clock(), parse_ts(utc(DAY, "15:50")))

    def test_no_data_means_market_closed(self):
        w, _ = self.watcher({}, utc(DAY, "09:40"))
        self.assertEqual(w.run(), 0)
        self.assertEqual([e["event"] for e in self.events], ["NO_DATA"])

    def test_errors_then_recovery_then_fatal(self):
        calls = {"n": 0}

        def flaky(symbols, start, end=None, feed="iex"):
            calls["n"] += 1
            if calls["n"] in (2, 3, 4) or calls["n"] >= 6:
                raise alpaca.AlpacaError("HTTP 500")
            return {s: [Bar(utc(DAY, "09:30"), 1, 1, 1, 1.0)] for s in symbols}

        w, _ = self.watcher({}, utc(DAY, "10:00"), fetch=flaky)
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(w.run(), 2)
        self.assertEqual([e["event"] for e in self.events], ["ERROR", "RECOVERED", "ERROR", "FATAL"])


if __name__ == "__main__":
    unittest.main()
