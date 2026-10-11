"""Recall of watcher CANDIDATEs against the expected strict Robinhood (SIP) signals.

    python3 validation/analyze.py validation/fill_augsep.jsonl validation/fill_oct.jsonl
"""
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
EXPECTED = [["2026-08-17","14:26","AAOI"],["2026-08-18","09:52","ASTS"],["2026-08-18","09:57","AAOI"],["2026-08-18","11:01","AAOI"],["2026-08-19","13:33","SNDK"],["2026-08-20","11:30","ASTS"],["2026-08-24","09:44","AAOI"],["2026-08-24","09:46","SNDK"],["2026-09-01","09:48","ASTS"],["2026-09-01","09:49","AAOI"],["2026-09-01","14:26","SNDK"],["2026-09-09","15:13","SNDK"],["2026-09-10","15:17","ASTS"],["2026-09-10","15:21","ASTS"],["2026-09-14","09:43","SNDK"],["2026-09-14","10:54","AAOI"],["2026-09-14","10:58","AAOI"],["2026-09-15","10:58","ASTS"],["2026-09-18","10:09","ASTS"],["2026-09-22","15:01","AAOI"],["2026-09-24","09:56","ASTS"],["2026-10-08","11:18","AAOI"],["2026-10-08","15:16","AAOI"],["2026-10-09","09:54","ASTS"],["2026-10-09","11:45","ASTS"],["2026-10-09","15:10","SNDK"]]


def minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def load(paths):
    cands = []
    for p in paths:
        for line in open(p):
            ev = json.loads(line)
            if ev.get("event") != "CANDIDATE":
                continue
            t = datetime.strptime(ev["bar"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(ET)
            ev["day"] = t.strftime("%Y-%m-%d")
            cands.append(ev)
    return cands


def analyze(cands, n_days):
    caught, late, missed = [], [], []
    for day, s, sym in EXPECTED:
        same = [c for c in cands if c["symbol"] == sym and c["day"] == day]
        deltas = sorted((minutes(c["bar_et"]) - minutes(s), c["bar_et"]) for c in same)
        if any(d in (0, 1) for d, _ in deltas):
            caught.append((day, s, sym))
        elif any(d in (2, 3) for d, _ in deltas):
            late.append((day, s, sym, [b for d, b in deltas if d in (2, 3)][0]))
        else:
            near = min(deltas, key=lambda x: abs(x[0]))[1] if deltas else None
            missed.append((day, s, sym, near))
    per_day = Counter(c["day"] for c in cands)
    counts = [per_day.get(d, 0) for d in n_days]
    return {"caught": caught, "late": late, "missed": missed, "n": len(cands),
            "mean_per_day": sum(counts) / len(counts), "max_per_day": max(counts),
            "max_day": max(n_days, key=lambda d: per_day.get(d, 0)),
            "strict_on_iex": sum(c["strict_on_iex"] for c in cands)}


if __name__ == "__main__":
    days = sys.argv[1].split(",")
    r = analyze(load(sys.argv[2:]), days)
    print(json.dumps(r, indent=1))
