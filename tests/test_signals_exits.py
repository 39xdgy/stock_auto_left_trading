import unittest

from trader import config, signals
from trader.exits import simulate, take_profit_price

from tests.helpers import make_bars


def ind(macd, signal, rsi, k, d):
    return signals.Indicators(macd, signal, [a - b for a, b in zip(macd, signal)], rsi, k, d)


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_strict_needs_all_three_on_the_same_bar(self):
        x = ind([-1.0, -0.4], [-0.5, -0.5], [30, 30], [10, 10], [12, 12])
        self.assertTrue(signals.is_entry(x, 1, self.cfg))

    def test_no_cross_no_signal(self):
        x = ind([-1.0, -0.6], [-0.5, -0.5], [30, 30], [10, 10], [12, 12])
        self.assertFalse(signals.is_entry(x, 1, self.cfg))

    def test_rsi_or_kd_too_high_blocks(self):
        self.assertFalse(signals.is_entry(ind([-1, -0.4], [-0.5, -0.5], [30, 36], [10, 10], [12, 12]), 1, self.cfg))
        self.assertFalse(signals.is_entry(ind([-1, -0.4], [-0.5, -0.5], [30, 30], [10, 21], [12, 12]), 1, self.cfg))
        self.assertFalse(signals.is_entry(ind([-1, -0.4], [-0.5, -0.5], [30, 30], [10, 10], [12, 20]), 1, self.cfg))

    def test_min_cross_margin(self):
        cfg = config.load(overrides={"entry": {"macd_min_cross_margin": 0.2}})
        x = ind([-1.0, -0.4], [-0.5, -0.5], [30, 30], [10, 10], [12, 12])  # clears by 0.1
        self.assertFalse(signals.is_entry(x, 1, cfg))


class ExitTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_take_profit_rounds_up_to_the_cent(self):
        self.assertEqual(take_profit_price(100.0, self.cfg), 100.83)
        self.assertEqual(take_profit_price(109.9, self.cfg), 110.82)  # 110.81217 -> 110.82
        self.assertGreaterEqual(take_profit_price(51.78, self.cfg), 51.78 * 1.0083)

    def _flat_ind(self, bars):
        n = len(bars)
        return ind([0.0] * n, [0.0] * n, [50.0] * n, [50.0] * n, [50.0] * n)

    def test_fixed_take_profit_fills_at_limit(self):
        bars = make_bars([100, 100.2, 100.5, 101.2, 101.0], start="10:00", spread=0.0)
        px, j, reason = simulate(bars, self._flat_ind(bars), 1, 100.0, self.cfg)
        self.assertEqual(reason, "take_profit")
        self.assertAlmostEqual(px, 100.83)
        self.assertEqual(j, 3)

    def test_unfilled_position_is_flattened_at_1550(self):
        bars = make_bars([100.0] * 10, start="15:45", spread=0.0)
        px, j, reason = simulate(bars, self._flat_ind(bars), 1, 100.0, self.cfg)
        self.assertEqual(reason, "end_of_day")
        self.assertEqual(bars[j].ts, make_bars([0], start="15:50")[0].ts)

    def test_no_stop_loss_by_default(self):
        bars = make_bars([100, 95, 90, 85, 99], start="10:00", spread=0.0)
        _, _, reason = simulate(bars, self._flat_ind(bars), 1, 100.0, self.cfg)
        self.assertEqual(reason, "end_of_day")

    def test_dynamic2_keeps_half_of_the_best_gain(self):
        closes = [100, 100.5, 101.0, 102.0, 101.4, 101.0, 100.9]
        bars = make_bars(closes, start="10:00", spread=0.0)
        px, _, reason = simulate(bars, self._flat_ind(bars), 1, 100.0, self.cfg, "dynamic2")
        self.assertEqual(reason, "giveback")
        self.assertAlmostEqual(px, 101.0)  # peak 102.0 -> keep half of +2.0


if __name__ == "__main__":
    unittest.main()
