"""Entry rules. Only "strict" is used live; the others are kept for comparison in backtests."""

from dataclasses import dataclass

from . import indicators


@dataclass
class Indicators:
    macd: list
    signal: list
    hist: list
    rsi: list
    k: list
    d: list


def compute(bars, cfg):
    e = cfg.entry
    closes = [b.close for b in bars]
    line, sig, hist = indicators.macd(closes, e.macd_fast, e.macd_slow, e.macd_signal)
    k, d = indicators.stochastic([b.high for b in bars], [b.low for b in bars], closes,
                                 e.stoch_k, e.stoch_smooth_k, e.stoch_d)
    return Indicators(line, sig, hist, indicators.rsi(closes, e.rsi_period), k, d)


def macd_cross(ind, i, cfg):
    if i < 1:
        return False
    margin = cfg.entry.macd_min_cross_margin
    return ind.macd[i] - ind.signal[i] > margin and ind.macd[i - 1] <= ind.signal[i - 1]


def rsi_low(ind, i, cfg):
    return ind.rsi[i] is not None and ind.rsi[i] < cfg.entry.rsi_max


def kd_low(ind, i, cfg):
    m = cfg.entry.stoch_max
    return ind.k[i] is not None and ind.d[i] is not None and ind.k[i] < m and ind.d[i] < m


RULES = {
    "strict": lambda ind, i, cfg: macd_cross(ind, i, cfg) and rsi_low(ind, i, cfg) and kd_low(ind, i, cfg),
    # Tested and not adopted (worse out of sample), kept for side-by-side journaling:
    "macd_kd": lambda ind, i, cfg: macd_cross(ind, i, cfg) and kd_low(ind, i, cfg),
    "macd_rsi": lambda ind, i, cfg: macd_cross(ind, i, cfg) and rsi_low(ind, i, cfg),
}


def is_entry(ind, i, cfg, rule=None):
    return RULES[rule or cfg.entry.rule](ind, i, cfg)


def snapshot(ind, i):
    """Indicator values on bar i, for logs."""
    r = lambda v: None if v is None else round(v, 4)
    return {"rsi": r(ind.rsi[i]), "k": r(ind.k[i]), "d": r(ind.d[i]),
            "macd": r(ind.macd[i]), "signal": r(ind.signal[i]), "hist": r(ind.hist[i])}
