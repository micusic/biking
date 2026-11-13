"""Body weight from the PICOOC (有品) scale, for the trends page and the review packet.

Source: picooc/body_data.csv (written by picooc/report.mjs: node cloud.mjs → node report.mjs). Rows with
abnormal=1 or an excluded_reason are dropped. Body fat / lean mass are the scale's own estimates and read about
2 points lower in the evening than in the morning, so they are only ever compared within one time_of_day;
weight is the reliable number. Bluetooth rows carry weight (and impedance) only.
"""
import csv
import json
import datetime
import os
import shutil
import statistics as st
import subprocess

from xds_bin2fit import TZ

HERE = os.path.dirname(os.path.abspath(__file__))
PICOOC = os.path.join(HERE, "picooc")
CSV = os.path.join(PICOOC, "body_data.csv")
DAY = 86400


def update(log=print):
    """Pull the latest weighings from the PICOOC cloud and rebuild body_data.csv (node cloud.mjs → report.mjs).
    Needs a session: PICOOC_SESSION in the cloud routine, picooc/session.json locally. Never logs in or
    asks for anything; on any failure the existing body_data.csv is kept."""
    if not shutil.which("node"):
        return log("  体重：没有 node，跳过")
    if not os.environ.get("PICOOC_SESSION") and not os.path.exists(os.path.join(PICOOC, "session.json")):
        return log("  体重：没有有品登录会话（PICOOC_SESSION），跳过")
    env = dict(os.environ, TZ="Asia/Shanghai")  # the scripts write Beijing times with the machine's zone
    for script in ("cloud.mjs", "report.mjs"):
        try:
            r = subprocess.run(["node", script], cwd=PICOOC, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, encoding="utf-8", timeout=180)
        except subprocess.TimeoutExpired:
            return log(f"  体重：{script} 超时，沿用已有数据")
        if r.returncode:
            return log(f"  体重：{script} 失败，沿用已有数据：{(r.stderr or r.stdout).strip().splitlines()[-1:]}")
    log("  体重：" + (r.stdout.strip().splitlines() or [""])[-1])


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
    # Bluetooth readings (weigh.bat) land in weights.csv first; body_data.csv only has them after report.mjs runs,
    # so take any that are missing from it straight away (exclude.json still applies)
    seen = [x["t"] for x in out]
    excluded = set()
    try:
        with open(os.path.join(PICOOC, "exclude.json"), encoding="utf-8") as f:
            excluded = {e["time"][:16] for e in json.load(f)}
    except (OSError, ValueError, KeyError):
        pass
    try:
        with open(os.path.join(PICOOC, "weights.csv"), encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                when = datetime.datetime.strptime(r["time"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
                t, w = int(when.timestamp()), _f(r.get("weight_kg"))
                if not w or r["time"][:16] in excluded or any(abs(t - s) < 600 for s in seen):
                    continue
                h = when.hour
                out.append(dict(t=t, tod="morning" if 5 <= h < 11 else "evening" if h >= 16 else "other",
                                weight=w, fat_pct=None, fat_kg=None, lean_kg=None))
    except (OSError, ValueError, KeyError):
        pass
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
