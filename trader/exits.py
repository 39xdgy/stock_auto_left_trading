"""Exit rules.

Live: fixed take-profit (a resting limit order) + flatten at end of day. No price stop.
Shadow: dynamic take-profit variants, simulated on the same bars for the journal only.
"""

import math

from .data import et_day, et_hhmm


def take_profit_price(entry_price, cfg):
    """Limit price for the take-profit order, rounded UP to the cent so the gain is at least pct."""
    return math.ceil(entry_price * (1 + cfg.exit.take_profit.pct) * 100 - 1e-9) / 100


def stop_price(entry_price, cfg):
    """Placeholder: only used if exit.stop_loss.enabled is turned on."""
    return math.floor(entry_price * (1 - cfg.exit.stop_loss.pct) * 100 + 1e-9) / 100


def simulate(bars, ind, entry_index, entry_price, cfg, variant="fixed", half_spread=0.0):
    """Walk forward from the entry bar and return (exit_price, exit_index, reason).

    variant:
      "fixed"     take-profit limit at +pct, fills when a bar's high reaches it
      "dynamic2"  after the high reaches +pct: sell on half giveback (resting stop raised each bar close)
                  or when %K and %D are both above sell_on_kd_above (sell next bar open)
      "dynamic3"  dynamic2 + never let the stop sit below the take-profit level
    Anything still open at flatten_time is sold at that bar's close. Market exits pay half_spread.
    """
    day = et_day(bars[entry_index].ts)
    tp = entry_price * (1 + cfg.exit.take_profit.pct)
    dyn = cfg.exit.dynamic
    use_sl = cfg.exit.stop_loss.enabled
    sl = entry_price * (1 - cfg.exit.stop_loss.pct)
    armed, peak, trail = False, entry_price, None
    j = entry_index
    while j < len(bars) and et_day(bars[j].ts) == day:
        b = bars[j]
        next_open = bars[j + 1].open if j + 1 < len(bars) and et_day(bars[j + 1].ts) == day else b.close
        if variant == "fixed":
            if b.high >= tp + 0.01:
                return tp, j, "take_profit"
        else:
            if armed and b.low <= trail:
                return min(trail, b.open) * (1 - half_spread), j, "giveback"
            peak = max(peak, b.high)
            if not armed and b.high >= tp + 0.01:
                armed = True
            if armed:
                if ind.k[j] is not None and ind.d[j] is not None and min(ind.k[j], ind.d[j]) > dyn.sell_on_kd_above:
                    return next_open * (1 - half_spread), j + 1, "kd_overbought"
                trail = entry_price + dyn.giveback * (peak - entry_price)
                if variant == "dynamic3":
                    trail = max(trail, tp)
        if use_sl and b.close <= sl:  # polled stop: sell next bar open
            return next_open * (1 - half_spread), j + 1, "stop_loss"
        if et_hhmm(b.ts) >= cfg.exit.end_of_day.flatten_time:
            return b.close * (1 - half_spread), j, "end_of_day"
        j += 1
    j -= 1
    return bars[j].close * (1 - half_spread), j, "end_of_day"
