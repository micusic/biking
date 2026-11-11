"""Body weight from the PICOOC (有品) scale, for the trends page and the review packet.

Source: picooc/body_data.csv (written by picooc/report.mjs: node cloud.mjs → node report.mjs). Rows with
abnormal=1 or an excluded_reason are dropped. Body fat / lean mass are the scale's own estimates and read about
2 points lower in the evening than in the morning, so they are only ever compared within one time_of_day;
weight is the reliable number. Bluetooth rows carry weight (and impedance) only.
"""
import csv
import datetime
import os
import statistics as st

from xds_bin2fit import TZ

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "picooc", "body_data.csv")
DAY = 86400


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load():
    """Valid measurements, oldest first: {t, tod, weight, fat_pct, fat_kg, lean_kg}."""
    if not os.path.exists(CSV):
        return []
    out = []
    with open(CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("abnormal") == "1" or (r.get("excluded_reason") or "").strip() or _f(r.get("weight_kg")) is None:
                continue
            out.append(dict(t=int(r["unix_time"]), tod=r["time_of_day"], weight=_f(r["weight_kg"]),
                            fat_pct=_f(r.get("fat_pct")), fat_kg=_f(r.get("fat_kg")), lean_kg=_f(r.get("lean_kg"))))
    return sorted(out, key=lambda x: x["t"])


def summary(rows, upto=None):
    """Latest weight and its change over ~30 and ~90 days (same time of day), as of `upto`."""
    upto = upto or datetime.datetime.now(TZ).timestamp()
    rows = [r for r in rows if r["t"] <= upto]
    if not rows:
        return None
    last = rows[-1]
    near = [r["weight"] for r in rows if r["tod"] == last["tod"] and r["t"] > last["t"] - 7 * DAY]

    def change(days):
        old = [r["weight"] for r in rows if r["tod"] == last["tod"] and abs(r["t"] - (last["t"] - days * DAY)) <= 14 * DAY]
        return round(st.median(near) - st.median(old), 1) if old else None
    when = datetime.datetime.fromtimestamp(last["t"], TZ)
    return dict(
        last_weighed=when.strftime("%Y-%m-%d %H:%M"), time_of_day=last["tod"],
        days_since=round((upto - last["t"]) / DAY), weight_kg=round(st.median(near), 1),
        change_30d_kg=change(30), change_90d_kg=change(90),
        fat_kg=last["fat_kg"], fat_pct=last["fat_pct"], lean_kg=last["lean_kg"],
        note=("体重来自有品体脂秤（同一时段取近 7 天中位数）。体脂、去脂体重是秤的估算值，单次误差约 ±2 个百分点，"
              "只看几周以上的趋势；早晚不能混比。超过 14 天没称时不要据此评价体重变化。"))
