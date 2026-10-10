import unittest

from trader import indicators


class IndicatorTests(unittest.TestCase):
    def test_ema_of_constant_is_constant(self):
        self.assertEqual(indicators.ema([5.0] * 30, 12), [5.0] * 30)

    def test_macd_of_constant_is_zero(self):
        line, sig, hist = indicators.macd([10.0] * 50)
        self.assertTrue(all(abs(v) < 1e-12 for v in line + sig + hist))

    def test_rsi_none_until_period_then_100_on_rising_series(self):
        r = indicators.rsi([float(i) for i in range(30)], 14)
        self.assertTrue(all(v is None for v in r[:14]))
        self.assertEqual(r[29], 100.0)

    def test_rsi_is_50_when_ups_and_downs_balance(self):
        closes = [10.0 + (i % 2) for i in range(40)]
        self.assertAlmostEqual(indicators.rsi(closes, 14)[-1], 50.0, delta=4)

    def test_stochastic_bounds_and_extremes(self):
        n = 40
        closes = [float(i) for i in range(n)]
        highs = [c + 0.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        k, d = indicators.stochastic(highs, lows, closes, 14, 3, 3)
        self.assertIsNone(k[14])
        self.assertAlmostEqual(k[-1], 100.0)  # closing at the high every bar
        self.assertAlmostEqual(d[-1], 100.0)
        falling = list(reversed(closes))
        k2, d2 = indicators.stochastic([c + 1 for c in falling], [c for c in falling], falling, 14, 3, 3)
        self.assertAlmostEqual(k2[-1], 0.0)

    def test_macd_crosses_up_after_a_v_bottom(self):
        closes = [100 - i * 0.5 for i in range(40)] + [80 + i * 0.5 for i in range(40)]
        line, sig, _ = indicators.macd(closes)
        cross = [i for i in range(1, len(closes)) if line[i] > sig[i] and line[i - 1] <= sig[i - 1]]
        self.assertTrue(cross)
        self.assertGreater(cross[0], 40)


if __name__ == "__main__":
    unittest.main()
