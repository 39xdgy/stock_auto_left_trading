# Tighter slack / cross window, window rule (2026-10-11)

Same backfill as below (main 55f1d86, fill_gaps=true, near_cross_bars=2, 32 trading days, 26 expected signals), scored with `analyze.py --rule window`. Each variant used a config copy changing only the `[watcher]` keys listed.

| variant | caught (window) | missed | candidates | per day mean / max | near_cross=true |
|---|---|---|---|---|---|
| near2: slack 5, cross_window 2 (config) | 24/26 | 09-10 15:21 ASTS; 09-18 10:09 ASTS | 840 | 26.2 / 54 (2026-09-23) | 641 |
| a: slack 0, cross_window 2 | 23/26 | 08-20 11:30 ASTS; 09-10 15:21 ASTS; 09-18 10:09 ASTS | 496 | 15.5 / 29 (2026-09-10) | 389 |
| b: slack 2, cross_window 2 | 24/26 | 09-10 15:21 ASTS; 09-18 10:09 ASTS | 617 | 19.3 / 37 (2026-09-23) | 479 |
| c: slack 0, cross_window 1 | 20/26 | 08-17 14:26 AAOI; 08-19 13:33 SNDK; 08-20 11:30 ASTS; 09-09 15:13 SNDK; 09-10 15:21 ASTS; 09-18 10:09 ASTS | 237 | 7.4 / 20 (2026-10-08) | 198 |

Slack 2 keeps near2's recall (same two misses) with 27% fewer candidates. Slack 0 loses 2026-08-20 ASTS (nearest candidate 11:32, one bar late). cross_window = 1 cuts candidates by ~70% but loses 4 more signals.

Files: `a_*.jsonl`, `b_*.jsonl`, `c_*.jsonl`.

# Near-cross wake condition, window matching rule (2026-10-11)

Backfill of `trader/live/watcher.py` on main (55f1d86, adds `near_cross_bars`), fill_gaps=true, slack 5, same 32 trading days and 26 expected signals. New rule (`analyze.py --rule window`): an expected signal at bar s is CAUGHT if a candidate for that symbol and day has bar_et in s-2..s+1 (Claude rechecks Robinhood at the candidate minute and up to 2 minutes after, each check looking at the last 2 completed bars).

| variant | caught (window) | caught / late (old rule) | candidates | per day mean / max | near_cross=true |
|---|---|---|---|---|---|
| near_cross_bars=2 (config) | 24/26 | 19 / 4 | 840 | 26.2 / 54 (2026-09-23) | 641 |
| near_cross_bars=3 | 25/26 | 21 / 2 | 1073 | 33.5 / 65 (2026-09-23) | 870 |
| previous fill variant (no near-cross) | 21/26 | 18 / 4 | 271 | 8.5 / 19 (2026-10-08) | n/a |

Missed under the window rule (nearest same-day candidate for that symbol):

- near2: 2026-09-10 15:21 ASTS (15:18); 2026-09-18 10:09 ASTS (09:46)
- near3: 2026-09-18 10:09 ASTS (09:46)
- fill: 2026-08-20 11:30 ASTS (11:32); 2026-09-01 14:26 SNDK (14:28); 2026-09-10 15:21 ASTS (15:18); 2026-09-14 10:54 AAOI (10:56); 2026-09-18 10:09 ASTS (09:47)

Files: `near2_*.jsonl`, `near3_*.jsonl` (near3 used a config copy with `near_cross_bars = 3`).

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
