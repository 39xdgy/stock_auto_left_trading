# Watcher validation (Alpaca IEX vs expected strict Robinhood SIP signals)

Backfill of `trader/live/watcher.py` on main (bb10e21) over 2026-08-17..2026-09-28 and 2026-10-08..09 (32 trading days), 26 expected signals. CAUGHT = candidate at bar s or s+1; LATE = s+2..s+3. Candidates/day is over all trading days in the range.

| variant | caught | caught+late | candidates | per day mean / max | strict_on_iex |
|---|---|---|---|---|---|
| fill_gaps=true, slack 5 (config) | 18/26 | 22/26 | 271 | 8.5 / 19 (2026-10-08) | 39 |
| fill_gaps=false, slack 5 | 15/26 | 22/26 | 203 | 6.3 / 21 (2026-10-08) | 31 |
| fill_gaps=true, slack 10 | 18/26 | 22/26 | 444 | 13.9 / 28 (2026-09-23) | 39 |
| fill_gaps=false, slack 10 | 15/26 | 22/26 | 363 | 11.3 / 24 (2026-10-08) | 31 |

Slack 10 was run because slack-5 recall is below 24/26. It produces the same caught/late/missed sets as slack 5 for both gap settings, with ~65-80% more candidates: the misses are about where the MACD cross lands on IEX bars, not about the RSI/stochastic limits.

## fill_gaps=true, slack 5 (config)

Caught late (s+2..s+3):

- 2026-08-19 13:33 SNDK: candidate at 13:35
- 2026-08-20 11:30 ASTS: candidate at 11:32
- 2026-09-01 14:26 SNDK: candidate at 14:28
- 2026-09-14 10:54 AAOI: candidate at 10:56

Missed (nearest same-day candidate for that symbol):

- 2026-08-17 14:26 AAOI: 14:25
- 2026-09-10 15:21 ASTS: 15:18
- 2026-09-14 10:58 AAOI: 10:56
- 2026-09-18 10:09 ASTS: 09:47

## fill_gaps=false, slack 5

Caught late (s+2..s+3):

- 2026-08-19 13:33 SNDK: candidate at 13:35
- 2026-08-20 11:30 ASTS: candidate at 11:32
- 2026-09-01 09:48 ASTS: candidate at 09:51
- 2026-09-01 14:26 SNDK: candidate at 14:28
- 2026-09-10 15:21 ASTS: candidate at 15:24
- 2026-09-15 10:58 ASTS: candidate at 11:00
- 2026-09-24 09:56 ASTS: candidate at 09:58

Missed (nearest same-day candidate for that symbol):

- 2026-08-17 14:26 AAOI: 14:30
- 2026-09-14 10:54 AAOI: 10:58
- 2026-09-18 10:09 ASTS: 09:47
- 2026-09-22 15:01 AAOI: 15:08

## Files

- `*_augsep.jsonl`, `*_oct.jsonl`: raw backfill stdout (one CANDIDATE per line)
- `analyze.py`: recall script (`python3 validation/analyze.py <comma-separated days> <jsonl>...`)
