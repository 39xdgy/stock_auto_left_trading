"""RSI (Wilder), MACD and slow Stochastic, matching Robinhood's charts.

Checked against a Robinhood Legend screenshot (SNDK 1-min, 2026-09-28 10:46 ET):
RSI 19.19 vs 19.18 here, %K/%D 7.92/5.05 vs 7.89/5.03. MACD lines differ by ~0.1 because of the EMA seed.
Values are None until enough bars exist.
"""


def ema(values, period):
    k = 2 / (period + 1)
    out = []
    for v in values:
        out.append(v if not out else out[-1] + k * (v - out[-1]))
    return out


def macd(closes, fast=12, slow=26, signal=9):
    line = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    sig = ema(line, signal)
    hist = [a - b for a, b in zip(line, sig)]
    return line, sig, hist


def rsi(closes, period=14):
    out = [None] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        up, down = max(d, 0.0), max(-d, 0.0)
        if i <= period:
            gain += up / period
            loss += down / period
        else:
            gain = (gain * (period - 1) + up) / period
            loss = (loss * (period - 1) + down) / period
        if i >= period:
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def _sma(values, period):
    out = [None] * len(values)
    for i in range(len(values)):
        window = values[max(0, i - period + 1):i + 1]
        if len(window) == period and None not in window:
            out[i] = sum(window) / period
    return out


def stochastic(highs, lows, closes, k_period=14, smooth_k=3, d_period=3):
    """Slow stochastic: raw %K over k_period, smoothed by smooth_k; %D = SMA(d_period) of %K."""
    raw = [None] * len(closes)
    for i in range(k_period - 1, len(closes)):
        hh = max(highs[i - k_period + 1:i + 1])
        ll = min(lows[i - k_period + 1:i + 1])
        raw[i] = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100
    k = _sma(raw, smooth_k)
    d = _sma(k, d_period)
    return k, d
