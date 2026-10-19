"""Build rides/dashboard.html — a single-file viewer for all synced rides.

Usage: uv run --with fit-tool python xds_dashboard.py   (xds_sync.py also calls build() after every sync)
Coordinates are stored as WGS-84; the page converts them to GCJ-02 itself when the AMap (高德) layer is on.
Heart-rate samples that xds_hrq.py judges wrong are left out of every HR number and chart; rides with
such samples carry `hrq` so the page can say so.
"""
import datetime
import json
import os
import statistics

from xds_bin2fit import TZ, parse_points
from xds_hrq import clean as clean_hr, load_config as load_hrq_config
from xds_places import assign_names
from xds_segments import efforts as segment_efforts, load as load_segments

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "dashboard_template.html")
OUT_HTML = os.path.join(HERE, "rides", "dashboard.html")
REVIEWS = os.path.join(HERE, "rides", "reviews")


def splits(pts, has_pw):
    """Per-kilometre splits: [km, moving seconds, km/h, elevation change m, avg hr, avg power]."""
    out, seg = [], []
    for p in pts:
        seg.append(p)
        last_split = p is pts[-1]
        if p["dist"] >= 1000 * (len(out) + 1) or last_split:
            d = p["dist"] - 1000 * len(out)
            if last_split and d < 100:
                break
            mov = sum(b["ts"] - a["ts"] for a, b in zip(seg, seg[1:]) if b["ts"] - a["ts"] <= 5)
            alts = [x["alt"] for x in seg if x["alt"] is not None]
            hrs = [x["hr"] for x in seg if x["hr"]]
            pws = [x["power"] for x in seg] if has_pw else []
            out.append([round(d / 1000, 2), mov, round(d / mov * 3.6, 1) if mov else None,
                        round(alts[-1] - alts[0]) if len(alts) > 1 else None,
                        round(sum(hrs) / len(hrs)) if hrs else None, round(sum(pws) / len(pws)) if pws else None])
            seg = [p]
    return out


def _best_mean(xs, n):
    if len(xs) < n:
        return None
    s = sum(xs[:n])
    best = s
    for i in range(n, len(xs)):
        s += xs[i] - xs[i - n]
        best = max(best, s)
    return round(best / n)


def fitness(pts, has_pw, hr_ok):
    """Numbers for the trends page.
    mspd/mpw: moving speed (km/h) and average power while moving (W); b1/b5/b20: best 1/5/20-min power
    (gaps count as 0 W); hr110: heart rate at 110 W from a fit of 2-min blocks after the first 10 min,
    only when the ride's HR is trustworthy (rides/hr_quality.json not suspect/bad)."""
    mov = [p for p in pts if p["speed"] * 3.6 > 5]
    if len(mov) < 300:
        return None
    out = dict(mspd=round(mov[-1]["dist"] / 1000 / (len(mov) / 3600), 1) if mov[-1]["dist"] else None,
               kj=round(sum(p["power"] for p in pts) / 1000) if has_pw else None)
    # heart-rate recovery at red lights: the app records nothing while stopped, so compare the last reading
    # before a pause with the first after it, when riding at ≥140 bpm into the stop. Only ~1-minute stops
    # (45–75 s): the drop grows fast with stop length, so longer stops would not compare
    if hr_ok:
        drops = [a["hr"] - b["hr"] for a, b in zip(pts, pts[1:])
                 if 45 <= b["ts"] - a["ts"] <= 75 and a["hr"] >= 140 and b["hr"] and a["dist"] > 2000]
        if drops:
            out.update(hrr=round(statistics.median(drops), 1), hrr_n=len(drops))
    if not has_pw:
        return out
    # average HR and power after the first 10 minutes (warm-up), for "same-power heart rate"
    late = mov[600:]
    late_hr = [p["hr"] for p in late if p["hr"]]
    if hr_ok and len(late) > 900 and len(late_hr) > 0.8 * len(late):
        out.update(ahr=round(sum(late_hr) / len(late_hr), 1), apw=round(sum(p["power"] for p in late) / len(late), 1))
    by_t = {p["ts"]: p["power"] for p in pts}
    series = [by_t.get(t, 0) for t in range(pts[0]["ts"], pts[-1]["ts"] + 1)]
    out.update(mpw=round(sum(p["power"] for p in mov) / len(mov)),
               b1=_best_mean(series, 60), b5=_best_mean(series, 300), b20=_best_mean(series, 1200))
    if hr_ok:
        blocks = []
        rest = mov[600:]
        for i in range(0, len(rest) - 120, 120):
            b = rest[i:i + 120]
            hrs = [p["hr"] for p in b if p["hr"]]
            if len(hrs) > 100:
                blocks.append((sum(p["power"] for p in b) / len(b), sum(hrs) / len(hrs)))
        if len(blocks) >= 8:
            mx = sum(a for a, _ in blocks) / len(blocks)
            my = sum(c for _, c in blocks) / len(blocks)
            sxx = sum((a - mx) ** 2 for a, _ in blocks)
            if sxx > 0:
                slope = sum((a - mx) * (c - my) for a, c in blocks) / sxx
                out["hr110"] = round(my + slope * (110 - mx), 1)
    return out


def ride_data(ride, pts, shift, hrq, segdefs=()):
    for p in pts:
        p["ts"] += shift
    q = clean_hr(pts, hrq["ceiling"], hrq["manual"].get(ride["id"]))  # bad strap samples → hr 0 (left out)
    gps = [p for p in pts if p["lat"] or p["lon"]]
    step = max(1, len(gps) // 400)
    trk = [[round(p["lat"], 5), round(p["lon"], 5)] for p in gps[::step]]
    has_pw = any(p["power"] for p in pts)  # with a power meter, 0 W (coasting) is a real value
    prof, last = [], -1e9  # sampled every ~50 m: [km, alt, km/h, hr, W, lat, lon]
    for p in pts:
        if p["dist"] - last >= 50 or p is pts[-1]:
            prof.append([round(p["dist"] / 1000, 3), p["alt"], round(p["speed"] * 3.6, 1), p["hr"] or None,
                         round(p["power"]) if has_pw else None,
                         round(p["lat"], 5) if p["lat"] else None, round(p["lon"], 5) if p["lat"] else None])
            last = p["dist"]
    hrs = [p["hr"] for p in pts if p["hr"]]
    return dict(
        id=ride["id"], start=ride["started_at"], end=ride["finished_at"], dist=round(ride["distance"] / 1000, 2),
        secs=ride["seconds"], avg=round(ride["avgSpeed"] / 10, 1),
        max=round(max((p["speed"] for p in pts), default=0) * 3.6, 1),
        kcal=round(ride.get("calories") or 0), gain=round(ride.get("gain_altitude") or 0),
        hr=round(sum(hrs) / len(hrs)) if hrs else None, maxhr=max(hrs) if hrs else None,
        pw=round(ride["average_power"]) if ride.get("average_power") else None,
        maxpw=round(ride["max_power"]) if ride.get("max_power") else None,
        cad=round(ride["average_cadence"]) if ride.get("average_cadence") else None,
        fixed=bool(shift), trk=trk, prof=prof, splits=splits(pts, has_pw),
        fit=fitness(pts, has_pw, q["status"] not in ("suspect", "bad")),
        segs=[dict(sid=sd["id"], **e) for sd in segdefs
              for e in segment_efforts(pts, sd, q["status"] not in ("suspect", "bad"))],
        hrq=dict(status=q["status"], pct=q["bad_pct"], why=list(q["reasons"]), segs=q.get("segments_km", []))
        if q["status"] in ("suspect", "bad", "minor") else None)


def collect(rides, fixes, raw_dir):
    """Per-ride page data (oldest first) and the segment definitions; also used by xds_review.py."""
    data, hrq, segdefs = [], load_hrq_config(), load_segments()
    for r in sorted(rides, key=lambda r: r["started_at"]):
        binf = os.path.join(raw_dir, r["id"] + ".bin")
        if not os.path.exists(binf):
            continue
        with open(binf, "rb") as f:
            data.append(ride_data(r, parse_points(f.read()), fixes.get(r["id"], 0), hrq, segdefs))
    assign_names(data)
    return data, segdefs


def load_reviews():
    """Coach/doctor reviews written by xds_review.py: {ride id: review}."""
    out = {}
    if os.path.isdir(REVIEWS):
        for fn in os.listdir(REVIEWS):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(REVIEWS, fn), encoding="utf-8") as f:
                        rv = json.load(f)
                    out[rv["id"]] = rv
                except (OSError, ValueError, KeyError):
                    pass
    return out


def build(rides, fixes, raw_dir, out_html=OUT_HTML):
    """rides: corrected ride dicts (cloud fields, times already shifted); fixes: {id: shift_seconds}."""
    data, segdefs = collect(rides, fixes, raw_dir)
    reviews = load_reviews()
    for r in data:
        if r["id"] in reviews:
            r["review"] = reviews[r["id"]]
    payload = json.dumps(dict(generated=datetime.datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), rides=data,
                              segments=segdefs),
                         ensure_ascii=False, separators=(",", ":"))
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read().replace("/*__DATA__*/null", payload)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    return out_html


if __name__ == "__main__":
    from xds_sync import INDEX, RAW, apply_corrections
    with open(INDEX, encoding="utf-8") as f:
        rides, fixes = apply_corrections(json.load(f))
    print(build(rides, fixes, RAW))
