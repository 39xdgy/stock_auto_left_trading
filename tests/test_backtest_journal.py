import unittest
from unittest import mock

from trader import backtest, config, journal

from tests.helpers import make_bars


def entry_at(*bar_indexes):
    return lambda ind, i, cfg, rule=None: i in bar_indexes


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()
        closes = [100.0] * 70 + [100.0, 100.4, 101.2, 101.0] + [101.0] * 20
        self.bars = make_bars(closes, start="09:30", spread=0.0)

    def test_buy_next_open_and_take_profit(self):
        with mock.patch("trader.backtest.signals.is_entry", entry_at(70)):
            trades = backtest.run({"AAOI": self.bars}, self.cfg)
        self.assertEqual(len(trades), 1)
        t = trades[0]
        entry = 100.0 * (1 + self.cfg.backtest.half_spread)
        self.assertAlmostEqual(t["entry"], round(entry, 4))
        self.assertEqual(t["qty"], 14)  # floor(1500 / 100.05)
        self.assertEqual(t["reason"], "take_profit")
        self.assertAlmostEqual(t["pnl"], round(14 * (entry * 1.0083 - entry), 2))

    def test_max_positions_across_symbols(self):
        one = config.load(overrides={"account": {"max_positions": 1}})
        with mock.patch("trader.backtest.signals.is_entry", entry_at(70)):
            self.assertEqual(len(backtest.run({"AAOI": self.bars, "SNDK": self.bars}, one)), 1)
            self.assertEqual(len(backtest.run({"AAOI": self.bars, "SNDK": self.bars}, self.cfg)), 2)

    def test_journal_report_shows_all_exit_variants(self):
        rec = [{"kind": "signal", "symbol": "AAOI", "bar": self.bars[70].ts, "action": "buy"},
               {"kind": "user_call", "symbol": "AAOI", "ts": self.bars[71].ts.replace(":00Z", ":30Z"), "note": "looks like a bottom"}]
        text = journal.report(rec, {"AAOI": self.bars}, self.cfg, "2026-10-12")
        self.assertIn("| signal | AAOI | 10:40 | buy |", text)
        self.assertIn("user_call", text)
        self.assertIn("take_profit", text)
        self.assertIn("Hypothetical totals", text)


if __name__ == "__main__":
    unittest.main()
