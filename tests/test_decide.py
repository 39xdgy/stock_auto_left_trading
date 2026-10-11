import unittest
from unittest import mock

from trader import config
from trader.live.decide import decide

from tests.helpers import make_bars, rh_rows, utc

DAY = "2026-10-12"


def state(now_et="10:30", positions=(), orders=(), pnl=0.0, earnings=(), quotes=None, n_bars=90, cash=None):
    # bars from 09:00 so the bar for now_et is still forming and must be ignored
    bars = make_bars([100.0 + (i % 3) * 0.1 for i in range(n_bars)], day=DAY, start="09:00")
    s = {"now": utc(DAY, now_et).replace(":00Z", ":20Z"),
         "bars": {sym: rh_rows(bars) for sym in ("AAOI", "SNDK", "ASTS")},
         "positions": list(positions), "open_orders": list(orders), "realized_pnl_today": pnl,
         "earnings_today": list(earnings), "quotes": quotes or {}}
    if cash is not None:
        s["buying_power"] = cash
    return s


def types(out):
    return [a["type"] for a in out["actions"]]


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_new_position_gets_take_profit_and_alert(self):
        out = decide(state(positions=[{"symbol": "AAOI", "quantity": 13, "avg_price": 109.9}]), self.cfg)
        tp = [a for a in out["actions"] if a["type"] == "place_take_profit"][0]
        self.assertEqual((tp["symbol"], tp["quantity"], tp["limit_price"]), ("AAOI", 13, 110.82))
        alert = [a for a in out["actions"] if a["type"] == "create_price_alert"][0]
        self.assertEqual(alert["threshold"], round(109.9 * 0.97, 2))

    def test_existing_take_profit_is_left_alone(self):
        out = decide(state(positions=[{"symbol": "AAOI", "quantity": 13, "avg_price": 109.9}],
                           orders=[{"id": "o1", "symbol": "AAOI", "side": "sell", "type": "limit",
                                    "quantity": 13, "limit_price": 110.82}]), self.cfg)
        self.assertEqual(out["actions"], [])

    def test_partial_take_profit_is_replaced(self):
        out = decide(state(positions=[{"symbol": "AAOI", "quantity": 13, "avg_price": 109.9}],
                           orders=[{"id": "o1", "symbol": "AAOI", "side": "sell", "type": "limit",
                                    "quantity": 5, "limit_price": 110.82}]), self.cfg)
        self.assertEqual(types(out), ["cancel_order", "place_take_profit"])

    def test_end_of_day_cancels_and_sells_everything(self):
        out = decide(state(now_et="15:50", positions=[{"symbol": "SNDK", "quantity": 1, "avg_price": 1586.3}],
                           orders=[{"id": "o1", "symbol": "SNDK", "side": "sell", "type": "limit",
                                    "quantity": 1, "limit_price": 1599.47}]), self.cfg)
        self.assertEqual(types(out), ["cancel_order", "sell_market"])

    @mock.patch("trader.live.decide.signals.is_entry", return_value=True)
    def test_signal_buys_at_the_ask_on_the_last_completed_bar(self, _):
        out = decide(state(quotes={"AAOI": {"ask": 109.88}}), self.cfg)
        buys = [a for a in out["actions"] if a["type"] == "buy_limit"]
        self.assertEqual(len(buys), 2)  # max_positions = 2: watchlist order decides who gets the slots
        self.assertEqual((buys[0]["symbol"], buys[0]["quantity"], buys[0]["limit_price"]), ("AAOI", 13, 109.88))
        self.assertEqual(buys[0]["signal_bar"], utc(DAY, "10:29"))  # not the forming 10:30 bar
        self.assertEqual([s["action"] for s in out["signals"]], ["buy", "buy", "max_positions"])

    @mock.patch("trader.live.decide.signals.is_entry", return_value=True)
    def test_min_one_share_when_a_share_costs_more_than_the_position_size(self, _):
        out = decide(state(quotes={"AAOI": {"ask": 1586.0}}), self.cfg)
        self.assertEqual(out["actions"][0]["quantity"], 1)

    @mock.patch("trader.live.decide.signals.is_entry", return_value=True)
    def test_gates(self, _):
        self.assertEqual(decide(state(pnl=-100), self.cfg)["signals"][0]["action"], "daily_loss_limit")
        self.assertEqual(decide(state(now_et="15:45", n_bars=405), self.cfg)["signals"][0]["action"], "outside_entry_window")
        self.assertEqual(decide(state(earnings=["AAOI"]), self.cfg)["signals"][0]["action"], "earnings_day")
        held = decide(state(positions=[{"symbol": "AAOI", "quantity": 13, "avg_price": 100}]), self.cfg)
        self.assertEqual([s["action"] for s in held["signals"]], ["already_holding", "buy", "max_positions"])
        self.assertEqual(decide(state(cash=50), self.cfg)["signals"][0]["action"], "not_enough_buying_power")

    def test_signal_on_the_previous_bar_still_counts_once(self):
        last, prev = utc(DAY, "10:29"), utc(DAY, "10:28")
        on_prev = lambda ind, i, cfg: i == 88  # bars 09:00.. -> index 88 = 10:28
        with mock.patch("trader.live.decide.signals.is_entry", side_effect=on_prev):
            out = decide(state(), self.cfg)
            self.assertEqual([a["signal_bar"] for a in out["actions"]], [prev, prev])
            handled = state()
            handled["handled_signals"] = [f"AAOI@{prev}", f"SNDK@{prev}", f"ASTS@{prev}"]
            self.assertEqual(decide(handled, self.cfg)["actions"], [])
        self.assertNotEqual(last, prev)

    def test_forming_setup_is_flagged(self):
        with mock.patch("trader.live.decide.signals.is_entry", return_value=False), \
                mock.patch("trader.live.decide.signals.rsi_low", return_value=True), \
                mock.patch("trader.live.decide.signals.kd_low", return_value=True), \
                mock.patch("trader.live.decide.signals.compute") as compute:
            compute.return_value.hist = [0.0] * 88 + [-0.3, -0.1]
            self.assertEqual(decide(state(), self.cfg)["forming"], ["AAOI", "SNDK", "ASTS"])

    def test_stale_buy_order_is_cancelled(self):
        out = decide(state(orders=[{"id": "b1", "symbol": "ASTS", "side": "buy", "type": "limit", "quantity": 29,
                                    "limit_price": 51.7, "created_at": utc(DAY, "10:27")}]), self.cfg)
        self.assertIn({"type": "cancel_order", "order_id": "b1", "symbol": "ASTS", "reason": "buy_not_filled"},
                      out["actions"])

    def test_stale_data_is_reported_not_traded(self):
        out = decide(state(now_et="11:30"), self.cfg)  # bars end at 10:29
        self.assertTrue(any("stale" in n for n in out["notes"]))


if __name__ == "__main__":
    unittest.main()
