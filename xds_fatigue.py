"""Fatigue signals: is the current training too much? Used by the review packet and the trends page.

Four signals, each judged against the rider's own history (all from normal commutes, no tests needed):
  hr      same-power heart rate (after warm-up, moved to 100 W, temperature taken out) of the last 3 trusted
          commutes vs the 8 before them in the same direction — ≥ +5 bpm on at least 2 of the 3
  hrr     red-light heart-rate recovery: median of the last 3 rides vs the 10 before — ≥ 5 bpm smaller
  ramp    work of the last 7 days ≥ 1.5 × the weekly average of the 4 weeks before AND more than any week
          of the previous 26 and ≥ 1200 kJ (a comeback to a load handled before, or a small week, does not count)
  streak  ≥ 4 riding days in a row
No signal → 正常; one → 留意; two or more → 建议休息 1–2 天. Rides whose HR is suspect/bad are left out.
"""
import datetime
import statistics as st

from xds_bin2fit import TZ
from xds_weather import TEMP_HR_PER_10C

HR_PER_W = 0.27
DAY = 86400


def _norm_hr(r):
    f = r.get("fit") or {}
    if f.get("ahr") is None or f.get("apw") is None:
        return None
    return f["ahr"] - HR_PER_W * (f["apw"] - 100)


def _am(r):
    return datetime.datetime.fromtimestamp(r["start"], TZ).hour < 12


def _temp(r):
    return (r.get("wx") or {}).get("temp")


def assess(data, upto=None):
    """data: rides oldest first (xds_dashboard.collect); upto: unix time to judge at (default: now)."""
    upto = upto or datetime.datetime.now(TZ).timestamp()
    rides = [r for r in data if r["start"] <= upto]
    signals = []

    # 1) same-power heart rate, per direction, temperature-adjusted to the ride being judged
    com = [r for r in rides if r.get("commute") and r["dist"] > 14 and _norm_hr(r) is not None]
    recent = [r for r in com if r["start"] > upto - 14 * DAY][-3:]
    rows = []
    for r in recent:
        prev = [x for x in com if x["start"] < r["start"] and _am(x) == _am(r)][-8:]
        if len(prev) < 4:
            continue
        t = _temp(r)
        base = st.median(_norm_hr(x) + (TEMP_HR_PER_10C / 10 * (t - _temp(x)) if t is not None and _temp(x) is not None else 0)
                         for x in prev)
        rows.append(dict(when=datetime.datetime.fromtimestamp(r["start"], TZ).strftime("%m-%d %H:%M"),
                         direction="上班" if _am(r) else "下班", value=round(_norm_hr(r), 1),
                         baseline=round(base, 1), delta=round(_norm_hr(r) - base, 1)))
    high = sum(1 for x in rows if x["delta"] >= 5)
    signals.append(dict(key="hr", name="同功率心率", flag=len(rows) >= 2 and high >= 2, rides=rows,
                        detail=(f"最近 {len(rows)} 次可信通勤里 {high} 次比自己的基线高 5 bpm 以上（已扣气温）"
                                if rows else "最近两周没有可信的心率数据")))

    # 2) red-light recovery
    hrr = [r["fit"]["hrr"] for r in rides if (r.get("fit") or {}).get("hrr") is not None and r["start"] > upto - 120 * DAY]
    last, before = hrr[-3:], hrr[-13:-3]
    if len(last) >= 2 and len(before) >= 5:
        d = st.median(last) - st.median(before)
        signals.append(dict(key="hrr", name="红灯心率恢复", flag=d <= -5, now=round(st.median(last), 1),
                            baseline=round(st.median(before), 1),
                            detail=f"最近 {len(last)} 次中位数 {st.median(last):.0f}，之前 {st.median(before):.0f}（{d:+.0f} bpm）"))
    else:
        signals.append(dict(key="hrr", name="红灯心率恢复", flag=False, detail="数据不够，暂不判断"))

    # 3) sudden jump in work
    def kj(a, b):
        return sum((r.get("fit") or {}).get("kj") or 0 for r in rides if a < r["start"] <= b)
    week = kj(upto - 7 * DAY, upto)
    prev4 = kj(upto - 35 * DAY, upto - 7 * DAY) / 4
    # every 7-day window (sliding by a day) in the half year before this week: fixed weekly steps miss peaks
    prev_max = max(kj(upto - (i + 7) * DAY, upto - i * DAY) for i in range(7, 26 * 7 + 1))
    jump = week >= 1.5 * prev4 and week > prev_max and week >= 1200  # below ~3 commute days the risk is small
    signals.append(dict(key="ramp", name="加量速度", flag=jump, week_kj=round(week), prev_4wk_avg_kj=round(prev4),
                        prev_26wk_max_kj=round(prev_max),
                        detail=f"近 7 天 {week:.0f} kJ，之前 4 周平均每周 {prev4:.0f} kJ，之前半年最多一周 {prev_max:.0f} kJ"))

    # 4) consecutive riding days ending today/yesterday
    days = {datetime.datetime.fromtimestamp(r["start"], TZ).date() for r in rides}
    d, streak = datetime.datetime.fromtimestamp(upto, TZ).date(), 0
    if d not in days:
        d -= datetime.timedelta(days=1)
    while d in days:
        streak += 1
        d -= datetime.timedelta(days=1)
    signals.append(dict(key="streak", name="连续骑行", flag=streak >= 4, days=streak,
                        detail=f"已连续骑 {streak} 天" if streak else "今天和昨天都没骑"))

    n = sum(s["flag"] for s in signals)
    level = "正常" if n == 0 else "留意" if n == 1 else "建议休息"
    advice = {"正常": "没有疲劳信号，按现在的节奏骑。",
              "留意": "有 1 个信号：这两天睡够、吃够，通勤按平时强度骑，暂时不安排强度日。",
              "建议休息": "有 2 个以上信号：休息 1–2 天或换交通方式，恢复后先骑轻松的再看指标。"}[level]
    return dict(level=level, flags=n, advice=advice, signals=signals,
                as_of=datetime.datetime.fromtimestamp(upto, TZ).strftime("%Y-%m-%d %H:%M"))


if __name__ == "__main__":
    import json
    import os
    import sys
    os.environ.setdefault("XDS_NO_LOOKUP", "1")
    from xds_dashboard import collect
    from xds_sync import INDEX, RAW, apply_corrections
    with open(INDEX, encoding="utf-8") as f:
        rides, fixes = apply_corrections(json.load(f))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(assess(collect(rides, fixes, RAW)[0]), ensure_ascii=False, indent=1))
