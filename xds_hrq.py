"""Heart-rate strap data quality: find samples that cannot be real heart rate, so every later step
(FIT export, dashboard, analysis) can leave them out.

A sample is bad when it falls in one of these:
  dropout  the strap sent nothing while riding (≥5 s)
  freeze   the value stuck for ≥3 s right before a dropout (the app repeats the last reading)
  jump     +25 bpm within 10 s with no hard effort (5 s ≥200 W) and no stop in the last 90 s —
           until HR settles back near where it was
  blink    ≥2 signal losses within a minute and the value then off by ≥12 bpm: 15 s before to 30 s after
  fall     −25 bpm within 10 s — until HR recovers
  ceiling  above the plausible ceiling (rides/hr_quality.json "ceiling"), ±20 s around it
  start    in the first 3 minutes, more than 20 bpm above how the ride then settles (dry electrodes)

Results go to rides/hr_quality.json ("rides": per-ride summary, rewritten every build).
"manual" in that file wins: {"<ride id>": "bad"} drops a ride's HR entirely, "ok" keeps it untouched.
"""
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
QUALITY = os.path.join(HERE, "rides", "hr_quality.json")
DEFAULT_CEILING = 180  # highest real value seen on clean rides is 176


def load_config():
    try:
        with open(QUALITY, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    return dict(ceiling=d.get("ceiling", DEFAULT_CEILING), manual=d.get("manual", {}))


def moving(p):
    return p["speed"] * 3.6 > 5


def assess(pts, ceiling=DEFAULT_CEILING, manual=None):
    """Return (bad: set of point indices, info: dict). Points need ts, hr, power, speed, dist."""
    n = len(pts)
    has_hr = [i for i, p in enumerate(pts) if p["hr"]]
    info = dict(status="none", bad_pct=0.0, reasons={})
    if not has_hr:
        return set(), info
    if manual == "ok":
        info["status"] = "ok"
        return set(), info
    if manual == "bad":
        info.update(status="bad", bad_pct=100.0, reasons={"手动标记": 1})
        return set(has_hr), info

    bad, reasons = set(), {}
    add = lambda why: reasons.__setitem__(why, reasons.get(why, 0) + 1)
    hr = [p["hr"] for p in pts]
    pw = [p["power"] for p in pts]

    # dropouts, and the frozen value right before each one
    i = 0
    while i < n:
        if not hr[i] and moving(pts[i]) and i > has_hr[0] and i < has_hr[-1]:
            j = i
            while j < n and not hr[j]:
                j += 1
            if j - i >= 5:
                add("掉线")
                k = i - 1
                while k > 0 and hr[k] and hr[k] == hr[i - 1]:
                    k -= 1
                if i - 1 - k >= 3:
                    bad.update(range(k + 1, i))
            i = j
        else:
            i += 1

    # implausible jumps / falls: compare with 10 s earlier
    def settle(start, ref, up):
        """Mark from start until HR is back within 10 bpm of ref (max 180 s)."""
        t0 = pts[start]["ts"]
        k = start
        while k < n and pts[k]["ts"] - t0 <= 180:
            if hr[k] and (hr[k] <= ref + 10 if up else hr[k] >= ref - 10) and k > start + 10:
                break
            bad.add(k)
            k += 1
        return k

    def explained(a, b):
        """A fast rise is real after a hard effort (any 5 s ≥200 W) or when starting off from a stop."""
        t = pts[a]["ts"]
        lo = a
        while lo > 0 and t - pts[lo - 1]["ts"] <= 90:
            lo -= 1
        if any(p["speed"] * 3.6 < 3 for p in pts[lo:a + 1]):
            return True
        w = pw[max(0, a - 20):b + 1]
        return any(st.mean(w[j:j + 5]) >= 200 for j in range(0, max(1, len(w) - 4)))

    k = 10
    while k < n:
        a, b = k - 10, k
        if hr[a] and hr[b] and pts[b]["ts"] - pts[a]["ts"] <= 12:
            if hr[b] - hr[a] >= 25 and not explained(a, b):
                add("无发力骤升")
                k = settle(a, hr[a], True)
                continue
            if hr[a] - hr[b] >= 25:
                add("骤降")
                k = settle(a, hr[a], False)
                continue
        k += 1

    # unstable signal: the strap blinking out ≥2 times within a minute — readings around it are unreliable
    #           only where the value is disturbed: ≥12 bpm away from before the first blink within 30 s after
    blinks = [i for i in range(1, n) if not hr[i] and hr[i - 1] and moving(pts[i])]
    for x, y in zip(blinks, blinks[1:]):
        if pts[y]["ts"] - pts[x]["ts"] > 60:
            continue
        ref = hr[x - 1]
        after = [hr[j] for j in range(x, min(n, y + 40)) if hr[j] and pts[j]["ts"] - pts[y]["ts"] <= 30]
        if after and max(abs(h - ref) for h in after) >= 12:
            t0, t1 = pts[x]["ts"] - 15, pts[y]["ts"] + 30
            bad.update(j for j in range(max(0, x - 30), min(n, y + 60)) if t0 <= pts[j]["ts"] <= t1)
            reasons["信号不稳"] = reasons.get("信号不稳", 0) + 1

    # above the plausible ceiling
    for i in has_hr:
        if hr[i] > ceiling:
            t = pts[i]["ts"]
            bad.update(j for j in range(max(0, i - 25), min(n, i + 25)) if abs(pts[j]["ts"] - t) <= 20)
            add("超过合理上限")

    # dry-strap start: first 3 min well above how the ride then settles (minutes 3–10)
    mv = [i for i in has_hr if moving(pts[i])]
    if mv:
        t0 = pts[mv[0]]["ts"]
        settled = [hr[i] for i in mv if 180 <= pts[i]["ts"] - t0 <= 600]
        if len(settled) > 60:
            base = st.median(settled)
            early = [i for i in mv if pts[i]["ts"] - t0 < 180 and hr[i] > base + 20]
            if early:
                bad.update(range(mv[0], early[-1] + 1))
                add("开头偏高")

    bad &= set(has_hr)
    if reasons.get("掉线", 0) == 1 and len(reasons) == 1 and not bad:
        reasons = {}  # a single short gap with nothing else wrong is normal Bluetooth noise
    pct = 100.0 * len(bad) / len(has_hr)
    info.update(bad_pct=round(pct, 1), reasons=reasons,
                status="suspect" if (reasons and (pct >= 0.5 or "超过合理上限" in reasons or reasons.get("掉线", 0) >= 2)) else "ok")
    if info["status"] == "ok" and bad:
        info["status"] = "minor"
    return bad, info


def clean(pts, ceiling=DEFAULT_CEILING, manual=None):
    """Zero out bad HR samples in place (0 = no reading everywhere downstream); return info."""
    bad, info = assess(pts, ceiling, manual)
    for i in bad:
        pts[i]["hr"] = 0
    if bad:
        segs, start = [], None
        idx = sorted(bad)
        for a, b in zip(idx, idx[1:] + [None]):
            start = a if start is None else start
            if b is None or b != a + 1:
                segs.append([round(pts[start]["dist"] / 1000, 2), round(pts[a]["dist"] / 1000, 2)])
                start = None
        info["segments_km"] = segs
    return info


def save_report(report):
    """report: {ride_id: info}. Keeps the hand-edited keys (ceiling, manual)."""
    try:
        with open(QUALITY, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("ceiling", DEFAULT_CEILING)
    d.setdefault("manual", {})
    d["说明"] = ("心率带数据质量。rides 每次生成自动重写；manual 里写 \"<骑行 id>\": \"bad\" 整次丢弃心率，"
               "\"ok\" 不做处理；ceiling 是合理心率上限。被判为异常的点在 FIT 和展示页里都已去掉。")
    d["rides"] = {k: v for k, v in sorted(report.items()) if v["status"] not in ("none", "ok") or v.get("reasons")}
    tmp = QUALITY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, QUALITY)
